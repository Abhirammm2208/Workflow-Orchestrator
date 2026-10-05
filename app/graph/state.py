"""
app/graph/state.py
AgentState — the single source of truth passed between every LangGraph node.

Design rules:
  - Nodes ONLY add to step_log and artifacts, never delete or overwrite.
  - Errors are appended to the errors list, never raised past the node boundary.
  - The graph is the only writer; the API layer reads state via checkpoints.
"""

from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


# ---------------------------------------------------------------------------
# Step log entry schema (stored as plain dicts inside step_log list)
# ---------------------------------------------------------------------------
# Each node appends one entry when it starts and updates it when it finishes.
# Evidence holds the key output values for that step (human-readable audit).
#
# Example entry:
# {
#     "step": "triage",
#     "status": "completed",          # running | completed | failed | skipped
#     "started_at": "2026-10-05T10:00:00.000Z",
#     "ended_at":   "2026-10-05T10:00:02.341Z",
#     "duration_ms": 2341,
#     "evidence": {"category": "Billing", "urgency": "High"}
# }
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """
    Complete state object for a single workflow run.
    LangGraph serialises this to JSON and stores it in PostgreSQL checkpoints
    after every node completes.
    """

    # ------------------------------------------------------------------
    # Identity & lifecycle
    # ------------------------------------------------------------------
    run_id: str
    """UUID v4 — same value used as LangGraph thread_id."""

    status: str
    """
    Current lifecycle status.
    Values: running | paused | failed | completed | cancelled | rejected
    """

    # ------------------------------------------------------------------
    # Input payload (set once at trigger, never mutated)
    # ------------------------------------------------------------------
    input_data: str
    """Raw customer ticket text."""

    customer_email: str
    """Recipient email address for the final reply."""

    customer_id: str
    """Customer identifier used for billing lookup."""

    # ------------------------------------------------------------------
    # Debug / testing
    # ------------------------------------------------------------------
    inject_failure: bool
    """
    When True, Step 2 (specialist routing) raises a recoverable exception.
    Used by evaluate.py to demonstrate failure + resume flow.
    """

    # ------------------------------------------------------------------
    # Accumulated outputs — append-only dict
    # ------------------------------------------------------------------
    artifacts: dict[str, Any]
    """
    All step outputs land here under unique keys.

    Keys populated progressively:
      triage       → category, urgency, triage_reasoning
      specialist   → billing_info  OR  relevant_docs
      draft        → draft_reply
      approval     → approval_status, reviewer_note
      email        → email_sent (bool), email_sent_at (ISO timestamp)
    """

    # ------------------------------------------------------------------
    # Audit trail — append-only list
    # ------------------------------------------------------------------
    step_log: list[dict[str, Any]]
    """
    Ordered list of step execution records.
    Every node appends one dict with: step, status, started_at, ended_at,
    duration_ms, evidence.
    """

    errors: list[dict[str, Any]]
    """
    List of error records. Each entry:
      {"step": str, "error": str, "timestamp": ISO str}
    Errors do NOT halt the list — they accumulate for the trace.
    """
