# AGENT_WORKFLOW.md

This document describes the architectural decisions made in building this project, how AI coding tools were used as an accelerator, where they required correction, and how a teammate or reviewer should audit the work . 

---

## Core Design Philosophy

The initial workflow and architecture were developed through a deliberate review process. AI coding tools were used to generate implementation options, but all architectural decisions were made by reviewing those proposals against the assignment requirements and rejecting or modifying anything that conflicted with the core principles below.

The key principle throughout: **AI handles probabilistic reasoning (classification, text generation). Deterministic application code controls execution, state transitions, persistence, and side effects.**

---

## Key Architectural Decisions (Made During Review)

### Decision 1 — LLM scope is strictly bounded

An early approach would have allowed the LLM to determine the entire workflow path — what action to take, which tool to call, what to do next. This was rejected.

**The reasoning:** In a durable workflow, non-determinism in routing creates untestable, non-reproducible execution paths. If the LLM decides routing, you cannot guarantee the same input produces the same workflow execution. You also cannot checkpoint and resume reliably because the resume path depends on what the model decides to do next.

**What was decided instead:** The LLM is used only for:
- Semantic classification (`category: Billing | Tech`, `urgency: High | Low`) in Step 1
- Natural language generation (draft reply) in Step 3

LangGraph executes the actual routing through explicit `add_conditional_edges()` calls based on the structured output from Step 1. The model provides a value; the application decides what to do with it.

```python
# The model outputs this:
{"category": "Billing", "urgency": "High"}

# The application routes this — not the model:
def route_to_specialist(state):
    if state["artifacts"]["category"] == "Billing":
        return "billing_lookup"   # deterministic
    return "doc_search"
```

This separation means the workflow is fully testable without an LLM (`USE_MOCK_LLM=true`) and fully reproducible across retries.

---

### Decision 2 — Human approval is a hard interrupt, not a soft flag

Two approaches were considered for the human approval gate:

**Option A (rejected):** A boolean flag `awaiting_approval=True` in state. The next invocation checks the flag and decides whether to continue.

**Option B (chosen):** LangGraph's `interrupt_before=["human_approval"]` — the graph engine physically stops before the node runs. Nothing executes until the API explicitly resumes it.

**Why Option A was rejected:** A flag-based approach creates a window between the flag being checked and the email being sent where a race condition or retry could bypass the gate. It also means the email node is technically reachable — just conditionally skipped. The checkpoint-based interrupt is atomic: the email node is unreachable until `/approve` is called. There is no bypass path.

```python
# Hard interrupt — enforced by the graph engine itself
graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["human_approval"]   # send_email is unreachable until this is resumed
)
```

This satisfies the requirement "show that downstream work does not continue early" as a structural guarantee, not just a test assertion.

---

### Decision 3 — Idempotency lives inside the node, not the orchestrator

When designing `send_email_node`, two placement options were considered for the idempotency guard:

**Option A (rejected):** Check `email_sent` in the orchestrator before invoking the node.

**Option B (chosen):** Check `email_sent` as the first line inside `send_email_node` itself.

**Why Option B:** If the check lives in the orchestrator, it only protects against duplicate calls from the same code path. If the node is ever called from a different path, a retry mechanism, or a future code change, the guard is bypassed. Placing it inside the node makes the guard unconditional — the node itself is safe to call any number of times from any context.

```python
async def send_email_node(state: AgentState) -> dict:
    # Guard is first — before any side-effecting code
    if state["artifacts"].get("email_sent") is True:
        return {"step_log": [...skipped entry...]}  # exits immediately

    # Only reaches here on first execution
    # ... send email ...
```

This is also why LangGraph's checkpoint replay alone is not sufficient — the guard needs to work even if a node is re-entered by mistake.

---

### Decision 4 — Two database drivers for the same database

PostgreSQL is used for both application data (SQLAlchemy ORM) and LangGraph checkpoints (AsyncPostgresSaver). The decision to use two separate connection pools with different drivers was explicit:

- **asyncpg** for SQLAlchemy: fastest async driver, purpose-built for application queries
- **psycopg3** for AsyncPostgresSaver: LangGraph's checkpointer hard-requires a `psycopg.AsyncConnection` — asyncpg is not compatible

The alternative — using only psycopg3 for everything — was considered and rejected because asyncpg is significantly faster for the application-side `SELECT/UPDATE/INSERT` queries that run on every API request.

This means the `runs` table (fast metadata queries) uses asyncpg, and the `checkpoints` table (heavier JSON serialisation) uses psycopg3. Both point to the same PostgreSQL database.

---

### Decision 5 — Separate `runs` metadata table alongside LangGraph checkpoints

LangGraph checkpoints store complete state snapshots — but querying them requires deserialising the entire JSON blob for every row. For `GET /runs` (list view), this would be expensive.

The decision: maintain a lightweight `runs` table in SQLAlchemy as an index over the checkpoint store. It holds only: `run_id`, `status`, `category`, `urgency`, `timestamps`, `last_error`. The `GET /runs/{id}` (detail view) reads from the checkpoint for accuracy; `GET /runs` (list) reads from the `runs` table for speed.

This dual-store approach adds a sync responsibility (the API layer must keep them consistent) but that trade-off was accepted because list performance at demo scale matters more than the complexity of the sync.

---

### Decision 6 — Mock tools must be deterministic, not random

The specialist tools (`billing_api.py`, `doc_search.py`) are mocks. The design choice was to make them deterministic — the same `customer_id` always returns the same billing record — rather than randomly varying.

**Why:** Deterministic mocks make the system fully reproducible. If a run fails and is retried, the retry returns the same specialist data, which means the LLM draft will be based on the same inputs. This is a requirement for idempotency to work correctly across the whole pipeline, not just the email step.

The billing mock uses an MD5 hash of `customer_id` to select from 20 invoice scenarios. The doc search mock scores articles by keyword overlap, which is also deterministic for a given ticket text.

---

## AI Tools Used

| Tool | Role |
|---|---|
| **Kiro (AI coding assistant)** | Implementation accelerator — generated code scaffolding based on architectural decisions already made |
| **Nvidia Nemotron (`nemotron-3-ultra-550b-a55b`)** | Runtime LLM — ticket triage classification and draft reply generation inside the live workflow |

---

## Where AI Assisted with Implementation

Once architectural decisions were made (sections above), AI tooling was used to accelerate implementation of:

- Boilerplate code: SQLAlchemy async engine setup, session factory, FastAPI dependency injection patterns
- ORM model definition: `Run` model fields, JSONB column, index declarations
- LangGraph graph assembly: `add_node`, `add_edge`, `add_conditional_edges` wiring
- Seed data: 50 diverse ticket records across all statuses
- Rich CLI layout: panel rendering, table formatting, polling loop structure
- Documentation drafts: README sections, Architecture.md Mermaid diagrams

In each case, the generated output was reviewed against the decisions above and modified where needed.

---

## Where the Generated Suggestions Were Wrong or Rejected

### Issue 1 — AI initially allowed LLM to control routing

The first generated architecture routed nodes based on LLM output without explicit conditional edges. This was rejected — see Decision 1 above.

### Issue 2 — Model end-of-life (410 Error on first run)

The AI specified `nvidia/llama-3.3-nemotron-super-49b-v1`. This model hit end-of-life on 2026-08-26 and returned HTTP 410 on the first live test.

**Fix:** Updated to `nvidia/nemotron-3-ultra-550b-a55b` — found by reading the Nvidia NIM platform documentation and selecting the current production model with `enable_thinking` support.

### Issue 3 — LangChain client silently dropped thinking traces

The initial implementation used LangChain's `ChatOpenAI` with `with_structured_output()`. The Nemotron model returns its reasoning in `reasoning_content` (a non-standard stream delta field). LangChain drops this field silently — the triage ran without errors but `triage_thinking` was always empty.

**Fix:** Replaced with raw `openai` SDK streaming, capturing `reasoning_content` explicitly in the stream loop. Wrapped the synchronous stream in `asyncio.to_thread()` to keep the async event loop unblocked.

```python
for chunk in stream:
    reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
    content   = getattr(chunk.choices[0].delta, "content", None)
```

### Issue 4 — 409 Conflict on /approve (status sync bug)

LangGraph returns `status: "running"` in the state dict when the graph pauses at an interrupt — from the graph's perspective the thread is still active. The generated `create_run` handler wrote this value to the `runs` table. The approve endpoint then checked `run.status == "paused"` and returned 409.

**Fix:** Changed status detection to inspect `artifacts` directly — if `draft_reply` is present and `email_sent` is not True, the run is paused regardless of the graph's reported status. The approve endpoint was updated to validate by checkpoint inspection rather than DB column value.

### Issue 5 — Windows-specific environment issues

Two issues found during local setup:

1. `localhost` on Windows resolves to IPv6 `::1` first. asyncpg and psycopg3 require IPv4 `127.0.0.1` explicitly.
2. The password `Abhiram@123` contains `@` which is a URL delimiter — connection strings were being parsed incorrectly.

**Fix:** Changed all DB URLs to use `127.0.0.1` and URL-encode `@` as `%40`. This is a Windows-specific issue not present on Linux/macOS.

### Issue 6 — Hard-pinned package versions

Requirements initially used `==` pins (e.g. `sqlalchemy==2.0.36`). The local environment had `2.1.3` installed, causing pip install failures.

**Fix:** Changed all pins to compatible ranges (`>=2.0.36`) to allow pip to resolve against what's installed.

---

## What Was Changed Directly in Code (Not AI-Generated)

| File | Change and reason |
|---|---|
| `app/graph/nodes.py` | Full rewrite from LangChain `ChatOpenAI` to raw `openai` SDK — LangChain didn't support `reasoning_content` |
| `app/api/routes.py` | Status detection logic — LangGraph's `"running"` vs `"paused"` mismatch required artifact-level inspection |
| `app/api/routes.py` | Approve/reject validation — check checkpoint for `draft_reply` instead of trusting DB status column |
| `app/config.py` | Model name, DB URLs (`127.0.0.1`, `%40` encoding) |
| `requirements.txt` | Hard pins → compatible ranges |
| `docker-compose.yml` | `USE_MOCK_LLM=true` hardcoded in both services — ensures demo works without API key by default |
| `demo_cli.py` | `draft_thinking` panel added — initial version omitted it |

---

## How to Audit This Work

### Verify the LLM boundary is respected

Open `app/graph/edges.py`. The routing functions take `state` as input and return a string. They read `state["artifacts"]["category"]` — a value set by the LLM in Step 1. They do not call the LLM. The LLM produces data; the application makes decisions.

### Verify the human approval gate is structural

Open `app/graph/graph.py`. Find `interrupt_before=["human_approval"]`. Then read `app/api/routes.py` `approve_run()` and `reject_run()` — these are the only paths that resume the graph. There is no timer, no auto-approval, no fallback. The gate cannot be bypassed.

### Verify idempotency is inside the node

Open `app/graph/nodes.py`. Find `send_email_node`. The first executable line after the function signature checks `artifacts.get("email_sent") is True`. This check runs before any email dispatch code. Run `py evaluate.py` Scenario 3 and observe `status: skipped` in the step log.

### Verify checkpoint-based resume (not full replay)

Run `py evaluate.py` Scenario 2. After the injected failure and retry, the step log for the completed run should contain exactly one `triage` entry. If triage had been replayed, there would be two. The checkpoint store is what prevents replay — LangGraph loads the state after the last completed node and continues from there.

### Verify mock mode is complete

Set `USE_MOCK_LLM=true` (already the default in Docker). Run `py evaluate.py`. All 5 scenarios pass. This confirms the workflow logic is entirely independent of the LLM — the LLM only provides values that deterministic code acts on.

---

## Redacted Design Session Excerpt

The following exchange happened early in the design phase and directly shaped the state schema decision.

**Question asked:**

> I'm building a durable workflow orchestrator for customer support triage. I need resumability after failure and idempotency on retry. Should the idempotency guard live in the orchestrator layer or inside the node itself? And should artifacts be mutable across nodes or append-only?

**Key response points (paraphrased):**

> The guard belongs inside the node. If it lives in the orchestrator, any new code path that calls the node bypasses it. Inside the node, it is unconditional.
>
> Append-only artifacts make every state transition auditable. If you overwrite, you lose the history of what each step produced. This matters for debugging and for idempotency — you need to know what was set and when, not just the current value.

**Decision taken from this:** `artifacts` is append-only by convention. The idempotency guard is the first line of `send_email_node`. Both decisions held through to the final implementation with no modifications.
