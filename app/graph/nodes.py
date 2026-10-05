"""
app/graph/nodes.py
All LangGraph node functions for the customer support triage workflow.

LLM: nvidia/nemotron-3-ultra-550b-a55b via Nvidia NIM (OpenAI-compatible).
Uses the raw `openai` client directly with enable_thinking=True and streaming,
so we capture both the reasoning_content (thinking trace) and the final answer.

Node contract:
  - Accepts full AgentState, returns a PARTIAL dict (only updated keys).
  - Never raises exceptions past the node boundary; errors go into state["errors"].
  - Appends one step_log entry: step, status, started_at, ended_at, duration_ms, evidence.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.graph.state import AgentState
from app.tools.billing_api import fetch_billing_info
from app.tools.doc_search import search_docs


# ---------------------------------------------------------------------------
# Nvidia NIM client — raw OpenAI SDK, same pattern as official example
# ---------------------------------------------------------------------------
def _get_client() -> OpenAI:
    return OpenAI(
        base_url=settings.nvidia_base_url,
        api_key=settings.nvidia_api_key,
    )


def _call_llm(
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 4096,
    enable_thinking: bool = True,
) -> tuple[str, str]:
    """
    Call Nvidia nemotron-3-ultra-550b-a55b with streaming.
    Returns (thinking_trace, final_answer) both as strings.

    Uses stream=True + reasoning_content exactly as the official example.
    Runs synchronously — called from async node via asyncio.to_thread.
    """
    client = _get_client()

    extra: dict = {}
    if enable_thinking:
        extra = {"chat_template_kwargs": {"enable_thinking": True}}

    stream = client.chat.completions.create(
        model=settings.nvidia_model,
        messages=messages,
        temperature=temperature,
        top_p=0.95,
        max_tokens=max_tokens,
        extra_body=extra if enable_thinking else {},
        stream=True,
    )

    thinking_parts: list[str] = []
    answer_parts: list[str] = []

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            thinking_parts.append(reasoning)

        content = getattr(delta, "content", None)
        if content:
            answer_parts.append(content)

    thinking = "".join(thinking_parts).strip()
    answer   = "".join(answer_parts).strip()
    return thinking, answer


import asyncio as _asyncio


async def _call_llm_async(
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 4096,
    enable_thinking: bool = True,
) -> tuple[str, str]:
    """Async wrapper — runs the blocking OpenAI stream in a thread pool."""
    return await _asyncio.to_thread(
        _call_llm, messages, temperature, max_tokens, enable_thinking
    )


# ---------------------------------------------------------------------------
# Pydantic model for triage structured output
# ---------------------------------------------------------------------------
class TriageOutput(BaseModel):
    category: str           # "Billing" | "Tech"
    urgency: str            # "High" | "Low"
    reasoning: str
    customer_sentiment: str # frustrated | anxious | neutral | confused | angry | satisfied


def _parse_triage(raw: str) -> TriageOutput:
    """
    Parse the LLM's text response into a TriageOutput.
    Tries JSON first, then regex fallback so a slightly malformed response
    still works rather than crashing the node.
    """
    # Try JSON block
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            cat = data.get("category", "")
            urg = data.get("urgency", "")
            # Normalise
            cat = "Billing" if "bill" in cat.lower() else "Tech"
            urg = "High" if "high" in urg.lower() else "Low"
            return TriageOutput(
                category=cat,
                urgency=urg,
                reasoning=data.get("reasoning", raw[:200]),
                customer_sentiment=data.get("customer_sentiment", "neutral"),
            )
        except Exception:
            pass

    # Regex fallback — scan the raw text for keywords
    raw_lower = raw.lower()
    cat = "Billing" if any(w in raw_lower for w in ["billing", "invoice", "payment", "charge", "refund"]) else "Tech"
    urg = "High" if any(w in raw_lower for w in ["high", "urgent", "critical", "production", "suspended"]) else "Low"
    sentiments = ["frustrated", "angry", "anxious", "confused", "satisfied", "neutral"]
    sent = next((s for s in sentiments if s in raw_lower), "neutral")

    return TriageOutput(
        category=cat,
        urgency=urg,
        reasoning=raw[:300],
        customer_sentiment=sent,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(start: str, end: str) -> int:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    return max(0, int((e - s).total_seconds() * 1000))


def _log_entry(step: str, status: str, started: str, ended: str, evidence: dict) -> dict:
    return {
        "step":        step,
        "status":      status,
        "started_at":  started,
        "ended_at":    ended,
        "duration_ms": _duration_ms(started, ended),
        "evidence":    evidence,
    }


# ---------------------------------------------------------------------------
# Mock LLM helpers (USE_MOCK_LLM=true — no API key needed)
# ---------------------------------------------------------------------------
_MOCK_KEYWORDS = {
    "billing": ("Billing", "High"),  "invoice": ("Billing", "High"),
    "charge":  ("Billing", "High"),  "payment": ("Billing", "Low"),
    "refund":  ("Billing", "High"),  "subscription": ("Billing", "Low"),
    "api":     ("Tech",    "High"),  "error":   ("Tech",    "High"),
    "login":   ("Tech",    "High"),  "password":("Tech",    "Low"),
    "crash":   ("Tech",    "High"),  "slow":    ("Tech",    "Low"),
    "webhook": ("Tech",    "High"),  "export":  ("Tech",    "Low"),
    "sso":     ("Tech",    "High"),  "ssl":     ("Tech",    "Low"),
}

def _mock_triage(ticket: str) -> TriageOutput:
    tl = ticket.lower()
    for kw, (cat, urg) in _MOCK_KEYWORDS.items():
        if kw in tl:
            return TriageOutput(
                category=cat, urgency=urg,
                reasoning=f"Keyword '{kw}' → {cat}/{urg}.",
                customer_sentiment="frustrated" if urg == "High" else "neutral",
            )
    return TriageOutput(category="Tech", urgency="Low",
                        reasoning="Default fallback.", customer_sentiment="neutral")


_MOCK_INTROS = [
    "Thank you for reaching out.",
    "We appreciate you bringing this to our attention.",
    "Thank you for contacting support — we've reviewed your case.",
    "We're sorry to hear you've experienced this issue.",
    "Thank you for your patience while we investigated.",
]
_MOCK_CLOSES = [
    "Please don't hesitate to reach out if you need further help.",
    "We're here to help — feel free to reply if anything is unclear.",
    "Your satisfaction is our priority. Let us know if we can do more.",
    "Thank you for being a valued customer.",
    "We appreciate your continued trust in our service.",
]

def _mock_draft(ticket: str, category: str, urgency: str, specialist: dict) -> str:
    import hashlib
    i = int(hashlib.md5(ticket[:20].encode()).hexdigest()[:2], 16)
    intro = _MOCK_INTROS[i % len(_MOCK_INTROS)]
    close = _MOCK_CLOSES[i % len(_MOCK_CLOSES)]
    urg_note = " We have flagged this as high priority." if urgency == "High" else ""

    if category == "Billing":
        inv = specialist.get("invoice_id", "N/A")
        st  = specialist.get("invoice_status", "unknown")
        body = (
            f"We have reviewed your account and located invoice {inv}. "
            f"The current status is: {st}. Our billing team will process any necessary "
            f"adjustments within 2–3 business days. You will receive a confirmation email once done."
        )
    else:
        docs = specialist.get("relevant_docs", [])
        if docs:
            doc = docs[0]
            body = (
                f"We found a solution for your technical issue. "
                f"Please refer to '{doc.get('title', 'our documentation')}': "
                f"{doc.get('snippet', '')[:200]} "
                f"Full details: {doc.get('url', 'https://docs.example.com')}."
            )
        else:
            body = (
                "Our technical team reviewed your issue. "
                "Please clear your cache and retry. If it persists, "
                "reply with your browser version and a screenshot."
            )
    return f"{intro}{urg_note}\n\n{body}\n\n{close}"


# ===========================================================================
# NODE 1: Triage
# ===========================================================================
async def triage_node(state: AgentState) -> dict[str, Any]:
    step = "triage"
    started = _now_iso()

    try:
        ticket = state["input_data"]

        if settings.use_mock_llm:
            result = _mock_triage(ticket)
            thinking = ""
        else:
            system = (
                "You are a senior customer support triage specialist.\n\n"
                "Analyse the ticket and respond with a JSON object ONLY — no prose before or after.\n\n"
                "JSON schema:\n"
                "{\n"
                '  "category": "Billing" | "Tech",\n'
                '  "urgency": "High" | "Low",\n'
                '  "reasoning": "<1-2 sentences explaining your decision>",\n'
                '  "customer_sentiment": "frustrated" | "anxious" | "neutral" | "confused" | "angry" | "satisfied"\n'
                "}\n\n"
                "CATEGORY rules:\n"
                "  Billing → charges, invoices, payments, refunds, subscriptions, pricing, account suspension due to payment.\n"
                "  Tech    → errors, bugs, login issues, API problems, slow performance, crashes, integrations, data issues.\n\n"
                "URGENCY rules:\n"
                "  High → account suspended, production down, data loss, security breach, customer very angry.\n"
                "  Low  → general questions, non-blocking issues, minor inconveniences."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": f"Ticket:\n\"\"\"\n{ticket}\n\"\"\""},
            ]
            thinking, raw = await _call_llm_async(
                messages, temperature=0.3, max_tokens=1024, enable_thinking=True
            )
            result = _parse_triage(raw)

        ended = _now_iso()
        evidence = {
            "category":          result.category,
            "urgency":           result.urgency,
            "customer_sentiment":result.customer_sentiment,
            "reasoning":         result.reasoning[:120],
        }
        if not settings.use_mock_llm and thinking:
            evidence["llm_thinking_preview"] = thinking[:200]

        return {
            "status": "running",
            "artifacts": {
                **state.get("artifacts", {}),
                "category":           result.category,
                "urgency":            result.urgency,
                "triage_reasoning":   result.reasoning,
                "customer_sentiment": result.customer_sentiment,
                "triage_thinking":    thinking if not settings.use_mock_llm else "",
            },
            "step_log": state.get("step_log", []) + [_log_entry(step, "completed", started, ended, evidence)],
            "errors":   state.get("errors", []),
        }

    except Exception as exc:
        ended = _now_iso()
        err = {"step": step, "error": str(exc), "timestamp": ended}
        return {
            "status":   "failed",
            "artifacts": state.get("artifacts", {}),
            "step_log": state.get("step_log", []) + [_log_entry(step, "failed", started, ended, {"error": str(exc)[:200]})],
            "errors":   state.get("errors", []) + [err],
        }


# ===========================================================================
# NODE 2a: Billing Lookup
# ===========================================================================
async def billing_lookup_node(state: AgentState) -> dict[str, Any]:
    step = "billing_lookup"
    started = _now_iso()

    try:
        if state.get("inject_failure", False):
            raise RuntimeError(
                "Injected failure at billing_lookup — simulating external API timeout. "
                "Set inject_failure=False and retry to resume."
            )

        cid = state.get("customer_id", "UNKNOWN")
        billing = await fetch_billing_info(cid)
        ended = _now_iso()

        evidence = {
            "customer_id":    cid,
            "invoice_id":     billing.get("invoice_id"),
            "invoice_status": billing.get("invoice_status"),
            "amount_due":     billing.get("amount_due"),
            "account_suspended": billing.get("account_suspended"),
        }

        return {
            "status": "running",
            "artifacts": {**state.get("artifacts", {}), "billing_info": billing},
            "step_log": state.get("step_log", []) + [_log_entry(step, "completed", started, ended, evidence)],
            "errors":   state.get("errors", []),
        }

    except Exception as exc:
        ended = _now_iso()
        err = {"step": step, "error": str(exc), "timestamp": ended}
        return {
            "status":   "failed",
            "artifacts": state.get("artifacts", {}),
            "step_log": state.get("step_log", []) + [_log_entry(step, "failed", started, ended, {"error": str(exc)[:200]})],
            "errors":   state.get("errors", []) + [err],
        }


# ===========================================================================
# NODE 2b: Doc Search
# ===========================================================================
async def doc_search_node(state: AgentState) -> dict[str, Any]:
    step = "doc_search"
    started = _now_iso()

    try:
        if state.get("inject_failure", False):
            raise RuntimeError(
                "Injected failure at doc_search — simulating vector DB connection error. "
                "Set inject_failure=False and retry to resume."
            )

        docs = await search_docs(state["input_data"], top_k=3)
        ended = _now_iso()

        evidence = {
            "docs_found":    len(docs),
            "top_doc_title": docs[0]["title"] if docs else None,
            "top_doc_score": docs[0]["relevance_score"] if docs else None,
        }

        return {
            "status": "running",
            "artifacts": {**state.get("artifacts", {}), "relevant_docs": docs},
            "step_log": state.get("step_log", []) + [_log_entry(step, "completed", started, ended, evidence)],
            "errors":   state.get("errors", []),
        }

    except Exception as exc:
        ended = _now_iso()
        err = {"step": step, "error": str(exc), "timestamp": ended}
        return {
            "status":   "failed",
            "artifacts": state.get("artifacts", {}),
            "step_log": state.get("step_log", []) + [_log_entry(step, "failed", started, ended, {"error": str(exc)[:200]})],
            "errors":   state.get("errors", []) + [err],
        }


# ===========================================================================
# NODE 3: Draft Reply
# ===========================================================================
async def draft_reply_node(state: AgentState) -> dict[str, Any]:
    step = "draft_reply"
    started = _now_iso()

    try:
        artifacts  = state.get("artifacts", {})
        ticket     = state["input_data"]
        category   = artifacts.get("category", "Tech")
        urgency    = artifacts.get("urgency",  "Low")
        sentiment  = artifacts.get("customer_sentiment", "neutral")
        thinking   = artifacts.get("triage_thinking", "")

        # Build specialist context block
        if category == "Billing":
            b = artifacts.get("billing_info", {})
            specialist_ctx = (
                f"Billing record retrieved:\n"
                f"  Invoice: {b.get('invoice_id')}  Status: {b.get('invoice_status')}\n"
                f"  Plan: {b.get('plan')}  Amount due: {b.get('amount_due')}\n"
                f"  Outstanding: {b.get('outstanding_balance')}  "
                f"Last payment: {b.get('last_payment_date')} ({b.get('last_payment_amount')})\n"
                f"  Account suspended: {b.get('account_suspended', False)}\n"
                f"  Notes: {b.get('notes', '')}"
            )
        else:
            docs = artifacts.get("relevant_docs", [])
            if docs:
                lines = []
                for i, d in enumerate(docs[:3], 1):
                    lines.append(
                        f"  {i}. [{d['title']}] — {d['snippet'][:200]}... (URL: {d['url']})"
                    )
                specialist_ctx = "Relevant documentation:\n" + "\n".join(lines)
            else:
                specialist_ctx = "No specific documentation found."

        if settings.use_mock_llm:
            specialist_data = (
                artifacts.get("billing_info", {}) if category == "Billing"
                else {"relevant_docs": artifacts.get("relevant_docs", [])}
            )
            draft = _mock_draft(ticket, category, urgency, specialist_data)
            draft_thinking = ""
        else:
            urg_instruction = (
                "HIGH URGENCY: Acknowledge severity immediately. Offer escalation to a senior specialist."
                if urgency == "High"
                else "Standard priority: be warm, efficient, and helpful."
            )
            sent_map = {
                "frustrated": "Customer is frustrated — be extra empathetic and apologetic first.",
                "angry":      "Customer is angry — validate their frustration before solutions.",
                "anxious":    "Customer is anxious — be reassuring, give clear timelines.",
                "confused":   "Customer is confused — use plain language, avoid jargon.",
                "neutral":    "Customer is neutral — be professional and concise.",
                "satisfied":  "Customer seems satisfied — keep tone warm and positive.",
            }
            sent_instruction = sent_map.get(sentiment, "Be professional and helpful.")

            # Optionally include triage reasoning as context for better drafts
            thinking_ctx = ""
            if thinking:
                thinking_ctx = f"\nTriage reasoning (internal — do not repeat verbatim):\n{thinking[:400]}\n"

            system = (
                "You are a skilled customer support specialist writing a professional reply to a customer ticket.\n\n"
                f"Category: {category}  |  Urgency: {urgency}\n"
                f"Urgency guidance: {urg_instruction}\n"
                f"Sentiment guidance: {sent_instruction}\n"
                f"{thinking_ctx}\n"
                "Writing rules:\n"
                "1. Open with a personalised acknowledgement of their specific issue — never generic.\n"
                "2. Reference the actual data retrieved — invoice numbers, doc titles, etc.\n"
                "3. Give concrete next steps with realistic timelines.\n"
                "4. Tone: warm, professional, human — not robotic or template-like.\n"
                "5. Length: 150–250 words. Natural prose — no bullet points.\n"
                "6. Close with an offer to follow up.\n"
                "7. Do NOT use placeholders like [Customer Name]. Write as if sending directly now."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": (
                    f"Customer ticket:\n\"\"\"\n{ticket}\n\"\"\"\n\n"
                    f"{specialist_ctx}\n\n"
                    "Write the customer reply:"
                )},
            ]
            draft_thinking, draft = await _call_llm_async(
                messages, temperature=0.7, max_tokens=2048, enable_thinking=True
            )
            draft = draft.strip()

        ended = _now_iso()
        wc = len(draft.split())

        return {
            "status": "running",
            "artifacts": {
                **artifacts,
                "draft_reply":       draft,
                "draft_word_count":  wc,
                "draft_thinking":    draft_thinking if not settings.use_mock_llm else "",
            },
            "step_log": state.get("step_log", []) + [
                _log_entry(step, "completed", started, ended, {
                    "word_count": wc,
                    "preview":    draft[:100] + "...",
                })
            ],
            "errors": state.get("errors", []),
        }

    except Exception as exc:
        ended = _now_iso()
        err = {"step": step, "error": str(exc), "timestamp": ended}
        return {
            "status":   "failed",
            "artifacts": state.get("artifacts", {}),
            "step_log": state.get("step_log", []) + [_log_entry(step, "failed", started, ended, {"error": str(exc)[:200]})],
            "errors":   state.get("errors", []) + [err],
        }


# ===========================================================================
# NODE 4: Human Approval (body runs ONLY after /approve or /reject is called)
# ===========================================================================
async def human_approval_node(state: AgentState) -> dict[str, Any]:
    step = "human_approval"
    started = _now_iso()

    artifacts       = state.get("artifacts", {})
    approval_status = artifacts.get("approval_status", "pending")
    reviewer_note   = artifacts.get("reviewer_note",   "")

    ended = _now_iso()

    if approval_status == "approved":
        return {
            "status": "running",
            "step_log": state.get("step_log", []) + [
                _log_entry(step, "approved", started, ended, {
                    "approval_status": "approved",
                    "reviewer_note":   reviewer_note,
                    "reviewed_at":     ended,
                })
            ],
            "errors":   state.get("errors", []),
            "artifacts": artifacts,
        }
    else:
        return {
            "status": "rejected",
            "step_log": state.get("step_log", []) + [
                _log_entry(step, "rejected", started, ended, {
                    "approval_status": approval_status,
                    "reviewer_note":   reviewer_note,
                    "reviewed_at":     ended,
                })
            ],
            "errors":   state.get("errors", []),
            "artifacts": artifacts,
        }


# ===========================================================================
# NODE 5: Send Email — IDEMPOTENT
# ===========================================================================
async def send_email_node(state: AgentState) -> dict[str, Any]:
    step = "send_email"
    started = _now_iso()
    artifacts = state.get("artifacts", {})

    # ── Idempotency guard ────────────────────────────────────────────────────
    if artifacts.get("email_sent") is True:
        ended = _now_iso()
        return {
            "step_log": state.get("step_log", []) + [
                _log_entry(step, "skipped", started, ended, {
                    "reason":             "email_sent=True — idempotency guard triggered, skipping duplicate send",
                    "originally_sent_at": artifacts.get("email_sent_at"),
                })
            ],
            "errors":   state.get("errors", []),
            "artifacts": artifacts,
        }

    try:
        recipient = state.get("customer_email", "unknown@example.com")
        draft     = artifacts.get("draft_reply", "")
        urgency   = artifacts.get("urgency",   "Low")
        category  = artifacts.get("category",  "Support")
        subject   = (
            f"[{'URGENT — ' if urgency == 'High' else ''}Support Reply] "
            f"Your {category} Request Has Been Reviewed"
        )

        # Mock send
        print(f"\n[MOCK EMAIL] → {recipient}  |  {subject}\n")

        sent_at = _now_iso()

        return {
            "status": "completed",
            "artifacts": {
                **artifacts,
                "email_sent":      True,
                "email_sent_at":   sent_at,
                "email_subject":   subject,
                "email_recipient": recipient,
            },
            "step_log": state.get("step_log", []) + [
                _log_entry(step, "completed", started, sent_at, {
                    "recipient":          recipient,
                    "subject":            subject,
                    "sent_at":            sent_at,
                    "idempotency_check":  "passed — email not previously sent",
                })
            ],
            "errors": state.get("errors", []),
        }

    except Exception as exc:
        ended = _now_iso()
        err = {"step": step, "error": str(exc), "timestamp": ended}
        return {
            "status":   "failed",
            "artifacts": artifacts,
            "step_log": state.get("step_log", []) + [_log_entry(step, "failed", started, ended, {"error": str(exc)[:200]})],
            "errors":   state.get("errors", []) + [err],
        }


# ===========================================================================
# NODE: Error Terminal
# ===========================================================================
async def error_terminal_node(state: AgentState) -> dict[str, Any]:
    step = "error_terminal"
    now  = _now_iso()
    cat  = state.get("artifacts", {}).get("category", "unknown")
    msg  = (
        f"Unroutable category '{cat}' — cannot determine specialist. "
        "Supported: Billing, Tech."
    )
    return {
        "status":   "failed",
        "step_log": state.get("step_log", []) + [_log_entry(step, "failed", now, now, {"error": msg})],
        "errors":   state.get("errors", []) + [{"step": step, "error": msg, "timestamp": now}],
        "artifacts": state.get("artifacts", {}),
    }
