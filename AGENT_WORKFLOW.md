# AGENT_WORKFLOW.md

This document describes how AI tools were used in building this project, where they helped, where they failed, what was changed manually, and how a teammate should audit the work.

---

## AI Tools Used

| Tool | Role |
|---|---|
| **Kiro (Agentic IDE — Claude-based)** | Primary development assistant — architecture design, all code generation, debugging, documentation |
| **Nvidia Nemotron (`nemotron-3-ultra-550b-a55b`)** | Runtime LLM — ticket triage classification and draft reply generation inside the workflow |

---

## Where AI Helped

### 1. Architecture Design and Tech Stack Decisions

The initial problem statement was broad ("build a durable workflow orchestrator"). Kiro was prompted with the full rubric and the chosen workflow (customer support triage), and it produced:

- The full `AgentState` TypedDict schema — including the decision to make `artifacts` append-only and to separate `step_log` from `errors`
- The decision to use two separate PostgreSQL drivers (`asyncpg` for SQLAlchemy ORM, `psycopg3` for LangGraph's `AsyncPostgresSaver`) — this is a non-obvious requirement that the AI flagged proactively
- The `interrupt_before=["human_approval"]` approach vs. a manual pause flag — the AI correctly identified that `interrupt_before` is atomic with checkpointing and prevents race conditions

### 2. Boilerplate Code Generation

All of the following were AI-generated without manual changes:

- `app/db/database.py` — async engine, session factory, `get_db` dependency, context manager
- `app/db/models.py` — full `Run` ORM model with JSONB column, indexes, `to_summary_dict()`
- `app/tools/billing_api.py` — 20 deterministic billing scenarios with MD5 hash routing
- `app/tools/doc_search.py` — 30 knowledge base articles with keyword relevance scoring
- `app/graph/state.py` — `AgentState` with inline docstrings
- `app/graph/edges.py` — both conditional routing functions
- `app/db/init_db.py` — standalone DB init script

### 3. LangGraph Graph Assembly

The graph topology (`app/graph/graph.py`) was AI-generated including:

- All `add_node`, `add_edge`, `add_conditional_edges` calls
- The `interrupt_before` placement
- The `start_run`, `resume_run`, `get_run_state`, `get_run_history` helper functions
- The `make_config` function tying `run_id` to `thread_id`

### 4. Seed Data and Evaluation Script

- `seed_data.py` — all 50 records, step logs, varied artifacts and draft replies were AI-generated
- `evaluate.py` — all 5 scenarios with `assert_check` helpers and Rich terminal output

### 5. Demo CLI

`demo_cli.py` was fully AI-generated, including the layout of the LLM summary panels, the approval prompt flow, the pending approvals dashboard, and the polling loop.

---

## Where AI Failed or Needed Manual Correction

### Failure 1 — Model End-of-Life (410 Error)

**What happened:** The AI initially specified `nvidia/llama-3.3-nemotron-super-49b-v1` as the model. This model hit end-of-life on 2026-08-26 and returned HTTP 410 Gone on the first live test.

**How it was found:** Running `py demo_cli.py` → Option 1 → the step trace showed `triage: failed` with `Error code: 410`.

**Manual fix:** Updated the model to `nvidia/nemotron-3-ultra-550b-a55b` across `config.py`, `.env`, `.env.example`. The AI had no way to know the model had been deprecated after its training cutoff.

### Failure 2 — LangChain ChatOpenAI Doesn't Support Thinking Mode

**What happened:** The initial `nodes.py` used LangChain's `ChatOpenAI` client with `with_structured_output(TriageOutput)`. This works for standard models but the `nemotron-3-ultra-550b-a55b` model returns `reasoning_content` in the stream delta — a non-standard field that LangChain silently drops.

**How it was found:** The triage node ran without errors but the `triage_thinking` artifact was always empty. Inspecting the raw API response manually showed the thinking content was present at the API level.

**Manual fix:** Rewrote `nodes.py` to use the raw `openai` SDK with `stream=True` and explicit `reasoning_content` capture, exactly as shown in Nvidia's official example. Added `asyncio.to_thread()` wrapper to keep the blocking SDK call compatible with the async FastAPI/LangGraph event loop.

### Failure 3 — 409 Conflict on /approve After Normal Run

**What happened:** After a run completed Steps 1–3 and paused at `human_approval`, calling `POST /runs/{id}/approve` returned `409 Conflict — not paused`.

**Root cause:** LangGraph returns `status: "running"` in the state dict when it pauses at `interrupt_before` — because from the graph's perspective the thread is still "in progress". The original `create_run` handler wrote this `"running"` value to the `runs` table, and the approve endpoint checked for `"paused"` exactly.

**Manual fix:** Changed status detection in `create_run` to inspect `artifacts` directly — if `draft_reply` exists and `email_sent` is not `True`, the run is definitively paused regardless of the state `status` field. Also changed the approve endpoint to accept `"paused"` OR `"running"` and validate by checking the checkpoint for a `draft_reply` instead of relying on the DB status column alone.

### Failure 4 — SQLAlchemy Version Conflict During pip install

**What happened:** `pip install -r requirements.txt` failed with `OSError: No such file or directory` on `SQLAlchemy-2.0.36.dist-info`. The system had SQLAlchemy 2.1.3 installed; the requirements file hard-pinned `==2.0.36`.

**Manual fix:** Changed all hard pins in `requirements.txt` to compatible ranges (`>=2.0.36` for SQLAlchemy, `>=` for others). This resolved the conflict cleanly.

### Failure 5 — localhost vs 127.0.0.1 on Windows

**What happened:** After fixing the SQLAlchemy conflict, the DB init script failed with `[Errno 11003] getaddrinfo failed`.

**Root cause:** On Windows, `localhost` resolves to the IPv6 address `::1` first. The `asyncpg` and `psycopg3` drivers require IPv4 `127.0.0.1` explicitly.

Additionally, the password `Abhiram@123` contains `@` which is a URL delimiter — the connection strings were being parsed incorrectly.

**Manual fix:** Changed all DB URLs in `.env`, `.env.example`, and `app/config.py` to use `127.0.0.1` and URL-encode `@` as `%40`.

---

## What Was Changed Manually

| File | Manual change |
|---|---|
| `app/graph/nodes.py` | Full rewrite from LangChain `ChatOpenAI` to raw `openai` SDK with `reasoning_content` streaming |
| `app/config.py` | Model name updated to `nemotron-3-ultra-550b-a55b`; DB URLs use `127.0.0.1` and `%40` |
| `.env` / `.env.example` | Model name, DB host, password URL-encoding |
| `app/api/routes.py` | Status detection logic in `create_run`; approve/reject validation to accept `"running"` state |
| `requirements.txt` | Hard pins → compatible version ranges |
| `demo_cli.py` | Added `draft_thinking` panel display after initial version generated without it |

---

## How a Teammate Should Audit the Work

### 1. Verify the state machine is correct

Read `app/graph/graph.py` — the graph topology is the authoritative definition of the workflow. Check:
- All nodes are registered
- The conditional edges match the routing logic in `app/graph/edges.py`
- `interrupt_before=["human_approval"]` is present
- `AsyncPostgresSaver` is wired as the checkpointer

### 2. Verify idempotency

Open `app/graph/nodes.py` and find `send_email_node`. The guard is:
```python
if artifacts.get("email_sent") is True:
    # returns skipped entry, exits
```
Run `py evaluate.py` and check Scenario 3 output — the second call should show `status: skipped`.

### 3. Verify checkpoint persistence

After running a workflow, connect to PostgreSQL and run:
```sql
SELECT thread_id, checkpoint_id, created_at
FROM checkpoints
ORDER BY created_at DESC
LIMIT 10;
```
You should see one row per completed node for each run.

### 4. Verify the 409 fix is robust

Start a run. Before calling `/approve`, call `GET /runs/{id}` and confirm `status: "paused"`. Then call `/cancel`. Then call `/approve` — should return `409`. This verifies the terminal state guard works correctly.

### 5. Verify mock mode

Set `USE_MOCK_LLM=true` in `.env`, restart the server, run `py evaluate.py`. All 5 scenarios should pass without any API calls.

### 6. Review the LLM prompts

The triage prompt is in `triage_node` (search for `system =` in `nodes.py`). The draft reply prompt is in `draft_reply_node`. Both are system prompts passed as `{"role": "system", "content": ...}` — no hidden injection, no external prompt files.

---

## Redacted Prompt Excerpt That Influenced Architecture

The following is the prompt session that shaped the **state schema and idempotency design**. This was an early conversation before any code was written.

---

**Prompt (human):**

> I'm building a durable workflow orchestrator for customer support ticket triage. The workflow has 5 steps: triage, specialist routing, draft reply, human approval, send email. I need it to be resumable after failure and idempotent on retry. What should the state schema look like and how should I handle idempotency for the email send step specifically?

**AI response (excerpt — rephrased for compliance):**

> For idempotency on the email send, the most robust approach is a state flag guard checked at the start of the node itself — before any side-effecting code runs. Store `email_sent: bool` in the state's `artifacts` dict. At the top of `send_email_node`, check `if artifacts.get("email_sent") is True: return skipped_log_entry`. This is safe because the state is serialised to the checkpoint store after every node completes, so `email_sent=True` is durable across restarts.
>
> For the state schema, separate concerns cleanly: use an `artifacts` dict for step outputs (append-only), a `step_log` list for the audit trail (append-only), and an `errors` list for failure records. The `status` field at the top level controls lifecycle transitions. Never delete from these structures inside a node — only append. This makes every run fully auditable and simplifies checkpoint comparison.
>
> For the LangGraph checkpointer, you'll need two separate database drivers — `asyncpg` for your SQLAlchemy ORM queries (faster, fully async) and `psycopg3` for `AsyncPostgresSaver` (LangGraph's checkpointer requirement). Both can point to the same PostgreSQL database but need separate connection pools...

**Impact:** This exchange directly shaped:
1. The `artifacts` dict being append-only (enforced as a code convention)
2. The idempotency guard being inside the node rather than in the orchestrator layer
3. The two-driver PostgreSQL setup being documented explicitly rather than discovered late

---

## Limitations of AI-Generated Code in This Project

- The AI cannot test against live APIs (it generated correct code for the Nvidia NIM endpoint structure, but could not verify the model name was still active)
- The AI generated reasonable default timeout values (`LLM_TIMEOUT_SECONDS=30`) but these may need tuning for the `nemotron-3-ultra-550b-a55b` model which can take 30–60 seconds on complex prompts with `enable_thinking=True`
- The seed data draft replies are hand-crafted templates, not actual Nemotron outputs — they simulate what the model produces but a reviewer should run at least one live ticket to see the real output
- The AI suggested `langgraph-checkpoint-postgres==2.0.8` specifically — the actual installed version resolved to `2.0.25` due to range pinning, which is compatible but was not explicitly tested by the AI during generation
