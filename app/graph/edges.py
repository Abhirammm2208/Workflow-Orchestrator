"""
app/graph/edges.py
Conditional edge routing functions for the LangGraph workflow.

Each function receives the current AgentState and returns a string key
that LangGraph uses to select the next node.
"""

from app.graph.state import AgentState


def route_to_specialist(state: AgentState) -> str:
    """
    Conditional edge: triage → specialist tool.

    Reads the category determined by the triage node and routes:
      - "Billing"  → billing_lookup node
      - "Tech"     → doc_search node
      - anything else (or missing) → signals a terminal error

    Returns:
        One of: "billing_lookup", "doc_search", "error"
    """
    # If the run has already been cancelled or failed upstream, short-circuit
    if state.get("status") in ("cancelled", "failed"):
        return "error"

    category = state.get("artifacts", {}).get("category", "")

    if category == "Billing":
        return "billing_lookup"
    elif category == "Tech":
        return "doc_search"
    else:
        # Unrecognised category — treat as unroutable
        return "error"


def route_after_approval(state: AgentState) -> str:
    """
    Conditional edge: human_approval → next step.

    After the human approval node runs its body, check whether the reviewer
    approved or rejected the draft reply.

      - "approved"  → send_email node
      - "rejected"  → terminal (END) — no email sent

    Returns:
        One of: "send_email", "end"
    """
    approval = state.get("artifacts", {}).get("approval_status", "")

    if approval == "approved":
        return "send_email"
    else:
        # Rejected or missing approval — terminate gracefully without sending
        return "end"
