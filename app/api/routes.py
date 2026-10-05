"""
app/api/routes.py
All FastAPI route handlers for the workflow orchestrator.

Endpoints:
  POST   /runs                    — Start a new workflow run
  GET    /runs                    — List all runs (paginated)
  GET    /runs/{run_id}           — Get full state + step trace for a run
  GET    /runs/{run_id}/history   — Get full checkpoint history (debug)
  POST   /runs/{run_id}/approve   — Resume a paused run (human approved)
  POST   /runs/{run_id}/reject    — Resume a paused run (human rejected)
  POST   /runs/{run_id}/retry     — Re-trigger a failed run from checkpoint
  POST   /runs/{run_id}/cancel    — Force terminal cancelled state
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Run
from app.graph.graph import get_run_history, get_run_state, resume_run, start_run
from app.graph.state import AgentState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["Workflow Runs"])


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class StartRunRequest(BaseModel):
    ticket_text: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="The raw customer support ticket text.",
        examples=["I was charged twice for my Pro subscription this month. Please investigate."],
    )
    customer_email: str = Field(
        ...,
        description="Customer's email address — recipient of the final reply.",
        examples=["customer@example.com"],
    )
    customer_id: str = Field(
        default="UNKNOWN",
        description="Customer identifier for billing lookup.",
        examples=["CUST-4421"],
    )
    inject_failure: bool = Field(
        default=False,
        description=(
            "Debug flag. When True, Step 2 raises a recoverable exception "
            "to demonstrate the failure + resume workflow."
        ),
    )


class ApproveRunRequest(BaseModel):
    reviewer_note: str = Field(
        default="",
        max_length=1000,
        description="Optional note from the human reviewer.",
        examples=["Looks accurate — billing data confirmed. Safe to send."],
    )
    reviewed_by: str = Field(
        default="support_agent",
        max_length=100,
        description="Name or ID of the reviewer.",
        examples=["agent_sarah"],
    )


class RejectRunRequest(BaseModel):
    reviewer_note: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Reason for rejection — required when rejecting.",
        examples=["Reply tone is too formal. Please regenerate with more empathy."],
    )
    reviewed_by: str = Field(
        default="support_agent",
        max_length=100,
    )


class RetryRunRequest(BaseModel):
    clear_failure: bool = Field(
        default=True,
        description="If True, sets inject_failure=False before retrying.",
    )


class RunSummary(BaseModel):
    run_id: str
    status: str
    category: Optional[str]
    urgency: Optional[str]
    customer_email: str
    customer_id: Optional[str]
    retry_count: int
    created_at: Optional[str]
    updated_at: Optional[str]
    completed_at: Optional[str]


class RunDetail(BaseModel):
    run_id: str
    status: str
    input_data: str
    customer_email: str
    customer_id: str
    inject_failure: bool
    artifacts: dict[str, Any]
    step_log: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    # Metadata from the runs table
    retry_count: int
    created_at: Optional[str]
    updated_at: Optional[str]
    completed_at: Optional[str]


# ---------------------------------------------------------------------------
# Helper: sync status from graph state → runs table
# ---------------------------------------------------------------------------
async def _sync_run_status(
    session: AsyncSession,
    run_id: str,
    state: dict[str, Any],
    extra: dict | None = None,
) -> Run:
    """
    Read the current graph state and update the Run metadata table row.
    Creates the row if it somehow doesn't exist (defensive).
    """
    result = await session.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

    new_status = state.get("status", run.status)
    run.status = new_status
    run.category = state.get("artifacts", {}).get("category") or run.category
    run.urgency = state.get("artifacts", {}).get("urgency") or run.urgency
    run.artifacts_snapshot = state.get("artifacts", {})
    run.updated_at = datetime.now(timezone.utc)

    # Set errors
    errors = state.get("errors", [])
    run.last_error = errors[-1]["error"] if errors else None

    if new_status in ("completed", "cancelled", "rejected", "failed"):
        if run.completed_at is None:
            run.completed_at = datetime.now(timezone.utc)

    if extra:
        for k, v in extra.items():
            if hasattr(run, k):
                setattr(run, k, v)

    await session.flush()
    return run


# ---------------------------------------------------------------------------
# POST /runs — Start a new workflow run
# ---------------------------------------------------------------------------
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Start a new workflow run",
    response_description="The created run with its unique run_id and initial status.",
)
async def create_run(
    body: StartRunRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Trigger a new customer support triage workflow.

    The graph will execute Steps 1–3 (triage → specialist → draft) and then
    pause before Step 4 (human approval). The run_id returned here is used for
    all subsequent API calls.

    Set `inject_failure: true` to simulate a recoverable failure at Step 2
    for testing the retry/resume flow.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Insert run metadata row before invoking the graph
    run = Run(
        run_id=run_id,
        status="running",
        ticket_text=body.ticket_text,
        customer_email=body.customer_email,
        customer_id=body.customer_id,
        inject_failure=body.inject_failure,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    await db.flush()  # Write row before graph starts (so GET /runs sees it immediately)

    # Build initial AgentState
    initial_state: AgentState = {
        "run_id": run_id,
        "status": "running",
        "input_data": body.ticket_text,
        "customer_email": body.customer_email,
        "customer_id": body.customer_id,
        "inject_failure": body.inject_failure,
        "artifacts": {},
        "step_log": [],
        "errors": [],
    }

    try:
        final_state = await start_run(initial_state)
    except Exception as exc:
        # Graph crashed hard — update DB and re-raise
        logger.error(f"Graph failed for run {run_id}: {exc}")
        run.status = "failed"
        run.last_error = str(exc)
        run.updated_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Graph execution failed: {exc}",
        )

    # Determine correct status.
    # LangGraph returns status="running" when it pauses at an interrupt — we
    # detect a pause by checking whether a draft_reply exists but email has NOT
    # been sent yet. A hard failure from a node sets status="failed" explicitly.
    graph_status = final_state.get("status", "running")
    artifacts = final_state.get("artifacts", {})

    if graph_status == "failed":
        current_status = "failed"
    elif artifacts.get("email_sent") is True:
        current_status = "completed"
    elif artifacts.get("draft_reply"):
        # Graph paused before human_approval — always mark as paused
        current_status = "paused"
    else:
        current_status = graph_status  # running or failed set by a node

    # Persist the correct status to both the Run row and the checkpoint state
    run.status = current_status
    run.category = artifacts.get("category")
    run.urgency = artifacts.get("urgency")
    run.artifacts_snapshot = artifacts
    run.updated_at = datetime.now(timezone.utc)
    errors = final_state.get("errors", [])
    run.last_error = errors[-1]["error"] if errors else None
    if current_status in ("completed", "failed"):
        run.completed_at = datetime.now(timezone.utc)
    await db.flush()

    # Also patch checkpoint so GET /runs/{id} reads "paused" from graph state
    if current_status == "paused":
        from app.graph.graph import get_graph, make_config
        try:
            _g = get_graph()
            await _g.aupdate_state(make_config(run_id), {"status": "paused"})
        except Exception:
            pass  # non-fatal — DB row is the authoritative status

    logger.info(f"Run {run_id} created — status: {current_status}")

    return {
        "run_id": run_id,
        "status": current_status,
        "category": final_state.get("artifacts", {}).get("category"),
        "urgency": final_state.get("artifacts", {}).get("urgency"),
        "message": (
            "Run paused — awaiting human approval. "
            f"Call POST /runs/{run_id}/approve to continue."
            if current_status == "paused"
            else f"Run {current_status}."
        ),
    }


# ---------------------------------------------------------------------------
# GET /runs — List all runs
# ---------------------------------------------------------------------------
@router.get(
    "",
    summary="List all workflow runs",
    response_description="Paginated list of run summaries.",
)
async def list_runs(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status: running, paused, failed, completed, cancelled, rejected",
    ),
    category_filter: Optional[str] = Query(
        default=None,
        alias="category",
        description="Filter by category: Billing or Tech",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Results per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return a paginated list of all workflow runs with summary metadata.
    Filter by status or category for targeted views.
    """
    query = select(Run).order_by(Run.created_at.desc())

    if status_filter:
        query = query.where(Run.status == status_filter)
    if category_filter:
        query = query.where(Run.category == category_filter)

    # Total count
    count_query = select(Run)
    if status_filter:
        count_query = count_query.where(Run.status == status_filter)
    if category_filter:
        count_query = count_query.where(Run.category == category_filter)
    count_result = await db.execute(count_query)
    total = len(count_result.scalars().all())

    # Paginated results
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    runs = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "runs": [r.to_summary_dict() for r in runs],
    }


# ---------------------------------------------------------------------------
# GET /runs/{run_id} — Full run detail with step trace
# ---------------------------------------------------------------------------
@router.get(
    "/{run_id}",
    summary="Get full run detail and step trace",
)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return the complete state of a run including all step logs, artifacts,
    and errors. Reads directly from the latest LangGraph checkpoint.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    # Fetch live state from checkpoint
    state = await get_run_state(run_id)
    if state is None:
        # Checkpoint missing — return what we have from the DB
        return {
            "run_id": run_id,
            "status": run.status,
            "input_data": run.ticket_text,
            "customer_email": run.customer_email,
            "customer_id": run.customer_id,
            "inject_failure": run.inject_failure,
            "artifacts": run.artifacts_snapshot or {},
            "step_log": [],
            "errors": [],
            "retry_count": run.retry_count,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "_warning": "Checkpoint data not found — showing last known DB state.",
        }

    return {
        "run_id": run_id,
        "status": state.get("status", run.status),
        "input_data": state.get("input_data", run.ticket_text),
        "customer_email": state.get("customer_email", run.customer_email),
        "customer_id": state.get("customer_id", run.customer_id),
        "inject_failure": state.get("inject_failure", run.inject_failure),
        "artifacts": state.get("artifacts", {}),
        "step_log": state.get("step_log", []),
        "errors": state.get("errors", []),
        "retry_count": run.retry_count,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/history — Checkpoint history (debug)
# ---------------------------------------------------------------------------
@router.get(
    "/{run_id}/history",
    summary="Get full checkpoint history for a run (debug)",
)
async def get_run_checkpoint_history(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return all checkpoint snapshots for a run in reverse chronological order.
    Shows state after every node — useful for debugging and audit.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    history = await get_run_history(run_id)
    return {
        "run_id": run_id,
        "checkpoint_count": len(history),
        "history": history,
    }


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/approve — Resume paused run (approved)
# ---------------------------------------------------------------------------
@router.post(
    "/{run_id}/approve",
    summary="Approve a paused run — resumes execution to send_email",
)
async def approve_run(
    run_id: str,
    body: ApproveRunRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Resume a workflow run that is paused at the human_approval step.

    Injects approval_status='approved' into the state, then resumes the graph.
    The graph will run human_approval node body → send_email → END.

    Raises 409 if the run is not in 'paused' status.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    # Accept "paused" or "running" — some runs may have status="running" in DB
    # if they were started before the status-patching fix. Check the checkpoint
    # for a draft_reply to confirm the graph is actually waiting at human_approval.
    if run.status not in ("paused", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id!r} is in status '{run.status}'. "
                "Only paused runs can be approved."
            ),
        )

    # Verify graph is actually at the human_approval interrupt
    current_state = await get_run_state(run_id)
    if current_state is None:
        raise HTTPException(status_code=404, detail="Checkpoint state not found.")

    if not current_state.get("artifacts", {}).get("draft_reply"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run does not have a pending draft reply — cannot approve.",
        )

    # Inject approval decision into checkpoint state
    state_update = {
        "artifacts": {
            **current_state.get("artifacts", {}),
            "approval_status": "approved",
            "reviewer_note": body.reviewer_note,
            "reviewed_by": body.reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    try:
        final_state = await resume_run(run_id, state_update)
        await _sync_run_status(
            db, run_id, final_state,
            extra={"reviewer_note": body.reviewer_note, "reviewed_by": body.reviewed_by},
        )
    except Exception as exc:
        logger.error(f"Resume (approve) failed for run {run_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resume failed: {exc}",
        )

    logger.info(f"Run {run_id} approved and resumed by {body.reviewed_by!r}")

    return {
        "run_id": run_id,
        "status": final_state.get("status", "completed"),
        "email_sent": final_state.get("artifacts", {}).get("email_sent", False),
        "email_sent_at": final_state.get("artifacts", {}).get("email_sent_at"),
        "reviewed_by": body.reviewed_by,
        "message": "Run approved and completed — email dispatched.",
    }


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/reject — Resume paused run (rejected)
# ---------------------------------------------------------------------------
@router.post(
    "/{run_id}/reject",
    summary="Reject a paused run — terminates without sending email",
)
async def reject_run(
    run_id: str,
    body: RejectRunRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Reject a paused run. The graph resumes, human_approval body detects
    'rejected' status, and routes to END — no email is sent.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    if run.status not in ("paused", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id!r} is in status '{run.status}'. "
                "Only paused runs can be rejected."
            ),
        )

    current_state = await get_run_state(run_id)
    if current_state is None:
        raise HTTPException(status_code=404, detail="Checkpoint state not found.")

    if not current_state.get("artifacts", {}).get("draft_reply"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run does not have a pending draft reply — cannot reject.",
        )

    state_update = {
        "artifacts": {
            **current_state.get("artifacts", {}),
            "approval_status": "rejected",
            "reviewer_note": body.reviewer_note,
            "reviewed_by": body.reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    try:
        final_state = await resume_run(run_id, state_update)
        await _sync_run_status(
            db, run_id, final_state,
            extra={"reviewer_note": body.reviewer_note, "reviewed_by": body.reviewed_by},
        )
    except Exception as exc:
        logger.error(f"Resume (reject) failed for run {run_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resume failed: {exc}",
        )

    logger.info(f"Run {run_id} rejected by {body.reviewed_by!r}: {body.reviewer_note}")

    return {
        "run_id": run_id,
        "status": "rejected",
        "email_sent": False,
        "reviewed_by": body.reviewed_by,
        "rejection_reason": body.reviewer_note,
        "message": "Run rejected — no email sent. Draft discarded.",
    }


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/retry — Re-trigger a failed run
# ---------------------------------------------------------------------------
@router.post(
    "/{run_id}/retry",
    summary="Retry a failed run from its last successful checkpoint",
)
async def retry_run(
    run_id: str,
    body: RetryRunRequest = RetryRunRequest(),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Resume a failed run from the last successful checkpoint.

    LangGraph's checkpointer stores state after every completed node, so
    retrying replays only the failed node — not the entire workflow.

    Set `clear_failure: true` (default) to remove the inject_failure flag
    so the retry succeeds.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    if run.status not in ("failed",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id!r} is in status '{run.status}'. "
                "Only failed runs can be retried."
            ),
        )

    current_state = await get_run_state(run_id)
    if current_state is None:
        raise HTTPException(status_code=404, detail="Checkpoint state not found.")

    # Build state update
    state_update: dict[str, Any] = {"status": "running"}
    if body.clear_failure:
        state_update["inject_failure"] = False

    try:
        final_state = await resume_run(run_id, state_update)

        # Determine new status — if it paused at human_approval
        new_status = final_state.get("status", "running")
        if new_status == "running" and final_state.get("artifacts", {}).get("draft_reply"):
            new_status = "paused"

        run.retry_count += 1
        await _sync_run_status(db, run_id, {**final_state, "status": new_status})

    except Exception as exc:
        logger.error(f"Retry failed for run {run_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retry failed: {exc}",
        )

    logger.info(f"Run {run_id} retried — retry_count: {run.retry_count}")

    return {
        "run_id": run_id,
        "status": final_state.get("status"),
        "retry_count": run.retry_count,
        "message": (
            f"Retry successful. Run is now '{final_state.get('status')}'. "
            "Call /approve if paused at human_approval."
            if final_state.get("status") in ("paused", "completed")
            else f"Retry completed with status: {final_state.get('status')}."
        ),
    }


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/cancel — Force terminal cancelled state
# ---------------------------------------------------------------------------
@router.post(
    "/{run_id}/cancel",
    summary="Cancel a run — forces terminal cancelled state",
)
async def cancel_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Immediately cancel a run. Sets status to 'cancelled' in both the DB
    and the checkpoint state. No further graph execution will occur.

    Can cancel runs in any non-terminal status (running, paused, failed).
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    if run.status in ("completed", "cancelled", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id!r} is already in terminal status '{run.status}'. "
                "Completed/cancelled/rejected runs cannot be cancelled."
            ),
        )

    now = datetime.now(timezone.utc)
    run.status = "cancelled"
    run.updated_at = now
    run.completed_at = now
    await db.flush()

    # Also update the checkpoint state so GET /runs/{id} reflects cancellation
    current_state = await get_run_state(run_id)
    if current_state is not None:
        from app.graph.graph import get_graph, make_config
        graph = get_graph()
        config = make_config(run_id)
        try:
            await graph.aupdate_state(config, {"status": "cancelled"})
        except Exception as exc:
            logger.warning(f"Could not update checkpoint for cancelled run {run_id}: {exc}")

    logger.info(f"Run {run_id} cancelled.")

    return {
        "run_id": run_id,
        "status": "cancelled",
        "cancelled_at": now.isoformat(),
        "message": f"Run {run_id!r} has been cancelled. No further steps will execute.",
    }
