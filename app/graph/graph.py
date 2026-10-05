"""
app/graph/graph.py
LangGraph workflow graph — assembly, checkpointer wiring, and lifecycle helpers.

The graph is compiled once at application startup and shared across all requests.
Each run gets its own thread_id (= run_id UUID) for checkpoint isolation.

Graph topology:
    triage
      │
      ├─ (Billing) ──→ billing_lookup ──→ draft_reply
      ├─ (Tech)    ──→ doc_search     ──→ draft_reply
      └─ (error)   ──→ error_terminal ──→ END
                                           │
                                    human_approval  ← INTERRUPT BEFORE
                                           │
                              ┌────────────┴──────────────┐
                              ▼ (approved)                 ▼ (rejected)
                          send_email                      END
                              │
                             END
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from app.config import settings
from app.graph.edges import route_after_approval, route_to_specialist
from app.graph.nodes import (
    billing_lookup_node,
    doc_search_node,
    draft_reply_node,
    error_terminal_node,
    human_approval_node,
    send_email_node,
    triage_node,
)
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level graph singleton — set during app startup
# ---------------------------------------------------------------------------
_compiled_graph: Any = None
_checkpointer: AsyncPostgresSaver | None = None


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------
def _build_graph(checkpointer: AsyncPostgresSaver) -> Any:
    """
    Assemble and compile the LangGraph StateGraph.

    interrupt_before=["human_approval"] tells LangGraph to persist state and
    halt execution just before that node runs. The graph will not proceed until
    graph.ainvoke() is called again with a Command(resume=...) payload.
    """
    builder: StateGraph = StateGraph(AgentState)

    # -- Register all nodes --------------------------------------------------
    builder.add_node("triage",          triage_node)
    builder.add_node("billing_lookup",  billing_lookup_node)
    builder.add_node("doc_search",      doc_search_node)
    builder.add_node("draft_reply",     draft_reply_node)
    builder.add_node("human_approval",  human_approval_node)
    builder.add_node("send_email",      send_email_node)
    builder.add_node("error_terminal",  error_terminal_node)

    # -- Entry point ---------------------------------------------------------
    builder.set_entry_point("triage")

    # -- Conditional edge: triage → specialist -------------------------------
    builder.add_conditional_edges(
        "triage",
        route_to_specialist,
        {
            "billing_lookup": "billing_lookup",
            "doc_search":     "doc_search",
            "error":          "error_terminal",
        },
    )

    # -- Linear edges: specialist → draft ------------------------------------
    builder.add_edge("billing_lookup", "draft_reply")
    builder.add_edge("doc_search",     "draft_reply")

    # -- Draft → human approval (graph will pause HERE before running node) --
    builder.add_edge("draft_reply", "human_approval")

    # -- Conditional edge: after human approval decision ---------------------
    builder.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {
            "send_email": "send_email",
            "end":        END,
        },
    )

    # -- Terminal edges ------------------------------------------------------
    builder.add_edge("send_email",      END)
    builder.add_edge("error_terminal",  END)

    # -- Compile with checkpointer + human-in-the-loop interrupt -------------
    compiled = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_approval"],   # ← pause here, wait for /approve
    )

    logger.info("LangGraph workflow compiled successfully.")
    return compiled


# ---------------------------------------------------------------------------
# Lifecycle — called by FastAPI lifespan
# ---------------------------------------------------------------------------
async def init_graph() -> None:
    """
    Open the psycopg3 async connection, initialise AsyncPostgresSaver,
    create the LangGraph checkpoint tables, and compile the graph.

    Must be called once during application startup.
    """
    global _compiled_graph, _checkpointer

    logger.info("Initialising LangGraph AsyncPostgresSaver with PostgreSQL...")

    # AsyncPostgresSaver requires a raw psycopg3 async connection pool.
    # We use the CHECKPOINT_DB_URI (no driver prefix) directly.
    connection = await psycopg.AsyncConnection.connect(
        settings.checkpoint_db_uri,
        autocommit=True,
    )

    _checkpointer = AsyncPostgresSaver(connection)

    # Creates `checkpoints` and `checkpoint_writes` tables if they don't exist
    await _checkpointer.setup()
    logger.info("LangGraph checkpoint tables ready.")

    _compiled_graph = _build_graph(_checkpointer)
    logger.info("LangGraph graph ready.")


async def close_graph() -> None:
    """
    Cleanly close the checkpointer connection.
    Called during application shutdown.
    """
    global _checkpointer
    if _checkpointer is not None:
        try:
            await _checkpointer.conn.close()
            logger.info("LangGraph checkpointer connection closed.")
        except Exception as exc:
            logger.warning(f"Error closing checkpointer connection: {exc}")


def get_graph() -> Any:
    """
    Return the compiled graph. Raises RuntimeError if called before init_graph().
    Used as a FastAPI dependency.
    """
    if _compiled_graph is None:
        raise RuntimeError(
            "Graph has not been initialised. Ensure init_graph() is called at startup."
        )
    return _compiled_graph


# ---------------------------------------------------------------------------
# Run configuration helper
# ---------------------------------------------------------------------------
def make_config(run_id: str) -> RunnableConfig:
    """
    Build the LangGraph RunnableConfig for a given run.

    thread_id ties all checkpoints for this run together.
    Using run_id as thread_id means the checkpoint store and run metadata
    share the same primary key — no extra mapping needed.
    """
    return {
        "configurable": {
            "thread_id": run_id,
        }
    }


# ---------------------------------------------------------------------------
# Graph operation helpers — thin wrappers used by the API routes
# ---------------------------------------------------------------------------
async def start_run(initial_state: AgentState) -> dict[str, Any]:
    """
    Invoke the graph from the beginning.

    The graph will run until it hits the human_approval interrupt, then
    pause and return the final state snapshot up to that point.

    Returns:
        The state dict after the graph pauses or completes.
    """
    graph = get_graph()
    config = make_config(initial_state["run_id"])

    try:
        result = await graph.ainvoke(initial_state, config=config)
        return result or {}
    except Exception as exc:
        logger.error(f"Graph invocation failed for run {initial_state['run_id']}: {exc}")
        raise


async def resume_run(run_id: str, state_update: dict[str, Any]) -> dict[str, Any]:
    """
    Resume a paused or failed run from its last checkpoint.

    Merges state_update into the existing state before continuing.
    Used by /approve, /reject, and /retry endpoints.

    Args:
        run_id:       The UUID of the run to resume.
        state_update: Partial state dict to merge (e.g. approval decision,
                      cleared inject_failure flag).

    Returns:
        The final state dict after resumption.
    """
    graph = get_graph()
    config = make_config(run_id)

    try:
        # Update the checkpoint state before resuming
        if state_update:
            await graph.aupdate_state(config, state_update)

        # Resume — None input tells LangGraph to continue from last checkpoint
        result = await graph.ainvoke(None, config=config)
        return result or {}
    except Exception as exc:
        logger.error(f"Graph resume failed for run {run_id}: {exc}")
        raise


async def get_run_state(run_id: str) -> dict[str, Any] | None:
    """
    Retrieve the current state snapshot from the latest checkpoint.

    Returns None if no checkpoint exists for this run_id (run not found).
    """
    graph = get_graph()
    config = make_config(run_id)

    try:
        snapshot = await graph.aget_state(config)
        if snapshot is None or snapshot.values is None:
            return None
        return dict(snapshot.values)
    except Exception as exc:
        logger.error(f"Failed to retrieve state for run {run_id}: {exc}")
        return None


async def get_run_history(run_id: str) -> list[dict[str, Any]]:
    """
    Retrieve the full checkpoint history for a run (all state snapshots).
    Useful for deep debugging — shows state after every node.
    """
    graph = get_graph()
    config = make_config(run_id)

    history = []
    try:
        async for state_snapshot in graph.aget_state_history(config):
            history.append({
                "checkpoint_id": state_snapshot.config.get("configurable", {}).get("checkpoint_id"),
                "next": list(state_snapshot.next),
                "values": dict(state_snapshot.values),
            })
    except Exception as exc:
        logger.error(f"Failed to retrieve history for run {run_id}: {exc}")

    return history
