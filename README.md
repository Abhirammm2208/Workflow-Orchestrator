# Workflow Orchestrator — Durable Customer Support Issue Triage

A production-grade, durable and resumable multi-step agent workflow orchestrator built around a **customer support ticket triage** use case. The system accepts an incoming support ticket, classifies it, routes it to a specialist tool, drafts a reply with an LLM, pauses for human approval, and dispatches the email — with full checkpoint persistence, idempotency, retry-from-failure, and cancellation at every step.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Users and Personas](#2-users-and-personas)
3. [Architecture](#3-architecture)
4. [Workflow — Five Steps](#4-workflow--five-steps)
5. [State Design](#5-state-design)
6. [Tech Stack and Trade-offs](#6-tech-stack-and-trade-offs)
7. [Setup and Installation](#7-setup-and-installation)
8. [Running the Project](#8-running-the-project)
9. [Mock / No-API-Key Mode](#9-mock--no-api-key-mode)
10. [API Reference](#10-api-reference)
11. [Demo CLI](#11-demo-cli)
12. [Evaluation Scenarios](#12-evaluation-scenarios)
13. [Sample Inputs and Outputs](#13-sample-inputs-and-outputs)
14. [Assumptions](#14-assumptions)
15. [Trade-offs and Limitations](#15-trade-offs-and-limitations)
16. [Next Steps](#16-next-steps)
17. [Docker](#17-docker)

---

## 1. Problem Statement

Customer support teams receive hundreds of tickets daily. A human agent must read each ticket, decide its urgency and category, pull supporting information from billing or documentation systems, draft a reply, review it, and send it. This pipeline is:

- **Slow** — each step is sequential and manual
- **Inconsistent** — triage quality varies by agent and shift
- **Fragile** — if the process is interrupted mid-way, work is lost
- **Risky** — a poorly worded reply going out without review causes damage

This project automates the full pipeline as a **durable, resumable orchestrated workflow** while keeping a human in control of the single most consequential step: approving the outgoing reply before it is sent. If the process fails at any step (network timeout, API error, restart), it resumes from the exact node that failed — no work is duplicated.

---

## 2. Users and Personas

| Persona | Role in the system |
|---|---|
| **Support Agent** | Triggers new runs, reviews and approves/rejects draft replies via the CLI or API |
| **Support Manager** | Monitors run list, views audit traces, cancels mis-triggered runs |
| **Developer / Evaluator** | Inspects checkpoint history, injects failures, runs the evaluation script |
| **Customer** | Receives the final email (mocked in this implementation) |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI (Port 8000)                    │
│  POST /runs  GET /runs  GET /runs/{id}  POST /runs/{id}/... │
└───────────────────────┬─────────────────────────────────────┘
                        │ ainvoke / aupdate_state / aget_state
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  LangGraph StateGraph                       │
│                                                             │
│   triage ──►  billing_lookup  ──►  draft_reply             │
│          └──► doc_search      ──►  (via conditional edge)  │
│          └──► error_terminal                               │
│                                    │                        │
│                             human_approval  ◄── INTERRUPT   │
│                             (pauses here)                   │
│                                    │                        │
│                    ┌───────────────┴───────────────┐        │
│                    ▼ approved                       ▼ rejected
│               send_email                          END        │
│                    │                                         │
│                   END                                        │
└──────────────────────────────┬──────────────────────────────┘
                               │ checkpoint after every node
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 PostgreSQL (Database: Workflow)              │
│                                                             │
│  Table: runs              ← application run metadata        │
│  Table: checkpoints       ← LangGraph serialised AgentState │
│  Table: checkpoint_writes ← in-flight write journal         │
└─────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| **FastAPI** | HTTP trigger, status queries, human approval/reject/retry/cancel actions |
| **LangGraph StateGraph** | Orchestrates node execution, conditional routing, checkpoint persistence |
| **AsyncPostgresSaver** | Serialises full `AgentState` to PostgreSQL after every node completion |
| **triage_node** | LLM (Nemotron) call — classifies ticket as Billing/Tech, High/Low, extracts sentiment |
| **billing_lookup / doc_search** | Mock specialist tools — deterministic, inject_failure-capable |
| **draft_reply_node** | LLM (Nemotron) call — generates context-aware customer reply |
| **human_approval_node** | Runs only after `/approve` or `/reject` API call — never executes autonomously |
| **send_email_node** | Idempotent mock email dispatch — skips silently if `email_sent=True` |
| **SQLAlchemy ORM** | `runs` table — fast status/category/urgency lookups without deserialising checkpoints |

### Two Database Clients — Why

LangGraph's `AsyncPostgresSaver` requires **psycopg3** directly (`psycopg.AsyncConnection`). The SQLAlchemy ORM uses **asyncpg** — a faster async driver for application queries. Both point to the same PostgreSQL database but use different connection pools managed independently.

---

## 4. Workflow — Five Steps

```
[Incoming Ticket]
       │
       ▼
  ┌─────────┐   LLM call (Nemotron)
  │ Step 1  │   Category: Billing | Tech
  │ Triage  │   Urgency:  High | Low
  └────┬────┘   Sentiment + Reasoning
       │
       │  conditional edge on artifacts["category"]
       ├──── Billing ──►  ┌──────────────────┐
       │                  │ Step 2a          │  Mock Billing API
       │                  │ Billing Lookup   │  Returns invoice, status,
       │                  └────────┬─────────┘  outstanding balance
       │                           │
       └──── Tech ──►  ┌───────────┴──────────┐
                       │ Step 2b              │  Mock Vector Search
                       │ Doc Search           │  Top-3 KB articles
                       └────────┬─────────────┘  with relevance scores
                                │
                                ▼
                       ┌────────────────┐   LLM call (Nemotron)
                       │    Step 3      │   Category-aware prompt
                       │  Draft Reply   │   Uses specialist data as context
                       └───────┬────────┘   Captures thinking trace
                               │
                               ▼   ◄── GRAPH PAUSES HERE
                    ┌──────────────────┐   interrupt_before=["human_approval"]
                    │     Step 4       │   Run status → "paused"
                    │  Human Approval  │   Draft shown to reviewer in CLI
                    └──────┬───────────┘   Waits for /approve or /reject
                           │
              ┌────────────┴──────────────────┐
              ▼ approved                       ▼ rejected
   ┌──────────────────┐              status → "rejected"
   │     Step 5       │              No email sent
   │   Send Email     │              Safe terminal state
   │  (Idempotent)    │
   └──────────────────┘
   email_sent=True persisted
   status → "completed"
```

---

## 5. State Design

```python
class AgentState(TypedDict):
    run_id:         str          # UUID4 — LangGraph thread_id
    status:         str          # running | paused | failed | completed | cancelled | rejected
    input_data:     str          # original ticket text (immutable after trigger)
    customer_email: str
    customer_id:    str
    inject_failure: bool         # debug flag — forces Step 2 failure

    artifacts: dict[str, Any]   # append-only step outputs:
                                 #   category, urgency, triage_reasoning, triage_thinking
                                 #   billing_info  OR  relevant_docs
                                 #   draft_reply, draft_word_count, draft_thinking
                                 #   approval_status, reviewer_note, reviewed_by
                                 #   email_sent, email_sent_at, email_subject

    step_log: list[dict]        # ordered execution trace — one entry per node:
                                 #   step, status, started_at, ended_at, duration_ms, evidence

    errors: list[dict]          # {step, error, timestamp} — appended on failure
```

**Design rules:**
- Nodes only ADD to `artifacts` and `step_log` — they never delete or overwrite
- The state object is the single source of truth — no side-channel variables
- `inject_failure` is stored in state (not env vars) so it survives restarts and retry flows

---

## 6. Tech Stack and Trade-offs

| Component | Choice | Reason |
|---|---|---|
| **Orchestration** | LangGraph 0.2.x | Native `interrupt_before`, TypedDict state, `AsyncPostgresSaver` — purpose-built for durable LLM workflows |
| **API** | FastAPI | Async-first, auto OpenAPI docs, type-safe request/response models |
| **Persistence** | PostgreSQL 17 | Production-grade; LangGraph's checkpointer writes directly; JSONB for artifact queries |
| **ORM** | SQLAlchemy 2.1 (asyncpg) | Fast async queries for the `runs` metadata table |
| **Checkpointer** | AsyncPostgresSaver (psycopg3) | LangGraph's required driver for async PostgreSQL checkpointing |
| **LLM** | Nvidia Nemotron `nemotron-3-ultra-550b-a55b` | OpenAI-compatible NIM endpoint, `enable_thinking=True` for reasoning trace, streams `reasoning_content` |
| **LLM Client** | Raw `openai` SDK | LangChain's `ChatOpenAI` does not support `reasoning_content` or `enable_thinking`; raw SDK needed |
| **Mock Fallback** | `USE_MOCK_LLM=true` | Full deterministic keyword-based path — runs without any API key |

### Why LangGraph over Airflow, Prefect, or Celery

- **Airflow**: No native state object or human-in-the-loop pause; overkill for single-service orchestration
- **Prefect**: Good for data pipelines; `interrupt_before` equivalent requires custom sensors
- **Celery**: No native state, no conditional edges, no checkpoint/resume semantics
- **LangGraph**: Designed for exactly this — stateful LLM workflows with human approval, conditional routing, and durable checkpoints

---

## 7. Setup and Installation

### Prerequisites

- Python 3.11 or 3.12
- PostgreSQL 14+ running locally (or Docker)
- Git

### Step 1 — Clone the repository

```bash
git clone https://github.com/Abhirammm2208/Workflow-Orchestrator.git
cd Workflow-Orchestrator
```

### Step 2 — Install dependencies

```bash
py -m pip install -r requirements.txt
```

> On Windows use `py` instead of `python`. On macOS/Linux use `python3`.

### Step 3 — Configure environment

```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS/Linux
```

Open `.env` and fill in two values:

```env
# Your Nvidia NIM API key from https://build.nvidia.com/
# Leave as-is and set USE_MOCK_LLM=true to run without a key
NVIDIA_API_KEY=nvapi-your-key-here

# If your PostgreSQL password contains special characters like @
# URL-encode them: @ → %40
DATABASE_URL=postgresql+asyncpg://postgres:your_password@127.0.0.1:5432/Workflow
PSYCOPG_URL=postgresql+psycopg://postgres:your_password@127.0.0.1:5432/Workflow
CHECKPOINT_DB_URI=postgresql://postgres:your_password@127.0.0.1:5432/Workflow
```

> **No API key?** Set `USE_MOCK_LLM=true` in `.env`. The full workflow runs with deterministic keyword-based responses — see [Mock Mode](#9-mock--no-api-key-mode).

### Step 4 — Create the database

Create a database named `Workflow` in PostgreSQL (via PgAdmin or psql):

```sql
CREATE DATABASE "Workflow";
```

### Step 5 — Initialise tables

```bash
py -m app.db.init_db
```

Expected output:
```
INFO | PostgreSQL connection OK — PostgreSQL 17.x
INFO | Tables created (or already exist).
INFO | ✓ runs
INFO | Database initialisation complete.
```

### Step 6 — Seed 50 demo records (optional)

```bash
py seed_data.py
```

Seeds 50 diverse workflow runs across all statuses — useful for demoing `GET /runs` and PgAdmin browsing.

---

## 8. Running the Project

### Start the API server

```bash
py -m uvicorn app.main:app --reload
```

The server starts at `http://localhost:8000`. On first startup it:
1. Creates/verifies the `runs` table
2. Initialises LangGraph's `AsyncPostgresSaver`
3. Creates `checkpoints` and `checkpoint_writes` tables
4. Compiles the graph with `interrupt_before=["human_approval"]`

### Interactive demo CLI (recommended for demos)

Open a second terminal:

```bash
py demo_cli.py
```

### Automated evaluation (covers all 5 rubric scenarios)

```bash
py evaluate.py
```

### API documentation

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health check: `http://localhost:8000/health`

---

## 9. Mock / No-API-Key Mode

Set `USE_MOCK_LLM=true` in `.env`. No Nvidia key required.

The mock path:
- **Triage**: keyword matching — tickets containing "billing", "invoice", "charge" → `Billing/High`; "api", "error", "login" → `Tech/High`; etc.
- **Draft reply**: template-based, references the specialist data retrieved
- **Thinking trace**: empty string (no LLM reasoning)
- **All other steps**: identical to the live path — billing lookup, doc search, approval gate, idempotency guard, checkpoint persistence all work exactly the same

This means a reviewer can run the full workflow, all 5 evaluation scenarios, and inspect every checkpoint without any paid service.

---

## 10. API Reference

All endpoints are documented interactively at `http://localhost:8000/docs`.

### POST /runs — Start a new workflow run

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_text": "I was charged twice for my Pro subscription this month. Please refund the duplicate.",
    "customer_email": "customer@example.com",
    "customer_id": "CUST-4421",
    "inject_failure": false
  }'
```

**Response 201:**
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "paused",
  "category": "Billing",
  "urgency": "High",
  "message": "Run paused — awaiting human approval. Call POST /runs/{run_id}/approve to continue."
}
```

> Set `inject_failure: true` to force a recoverable failure at Step 2 for testing the retry flow.

---

### GET /runs — List all runs

```bash
curl "http://localhost:8000/runs?status=paused&limit=10"
```

Query parameters: `status`, `category`, `limit` (1–100), `offset`

---

### GET /runs/{run_id} — Full run detail and step trace

```bash
curl http://localhost:8000/runs/550e8400-e29b-41d4-a716-446655440000
```

**Response 200:**
```json
{
  "run_id": "550e8400-...",
  "status": "paused",
  "input_data": "I was charged twice...",
  "artifacts": {
    "category": "Billing",
    "urgency": "High",
    "customer_sentiment": "frustrated",
    "triage_reasoning": "Duplicate charge reported — high urgency billing dispute.",
    "billing_info": {
      "invoice_id": "INV-10156",
      "plan": "Pro Monthly",
      "invoice_status": "overdue",
      "amount_due": "$99.98",
      "account_suspended": false
    },
    "draft_reply": "Dear Customer,\n\nWe sincerely apologise...",
    "email_sent": false
  },
  "step_log": [
    {
      "step": "triage",
      "status": "completed",
      "started_at": "2026-10-05T14:26:41.224Z",
      "ended_at": "2026-10-05T14:26:42.391Z",
      "duration_ms": 1167,
      "evidence": {"category": "Billing", "urgency": "High", "customer_sentiment": "frustrated"}
    },
    {
      "step": "billing_lookup",
      "status": "completed",
      "started_at": "2026-10-05T14:26:42.400Z",
      "ended_at": "2026-10-05T14:26:42.450Z",
      "duration_ms": 50,
      "evidence": {"invoice_id": "INV-10156", "invoice_status": "overdue", "amount_due": "$99.98"}
    },
    {
      "step": "draft_reply",
      "status": "completed",
      "started_at": "2026-10-05T14:26:42.460Z",
      "ended_at": "2026-10-05T14:27:03.213Z",
      "duration_ms": 20753,
      "evidence": {"word_count": 187, "preview": "Dear Customer, We sincerely apologise..."}
    },
    {
      "step": "human_approval",
      "status": "pending",
      "started_at": "2026-10-05T14:27:03.220Z",
      "ended_at": "2026-10-05T14:27:03.220Z",
      "duration_ms": 0,
      "evidence": {"awaiting": "human reviewer"}
    }
  ],
  "errors": [],
  "retry_count": 0
}
```

---

### POST /runs/{run_id}/approve — Approve a paused run

```bash
curl -X POST http://localhost:8000/runs/550e8400-.../approve \
  -H "Content-Type: application/json" \
  -d '{"reviewer_note": "Billing data confirmed. Tone is good.", "reviewed_by": "agent_sarah"}'
```

**Response 200:**
```json
{
  "run_id": "550e8400-...",
  "status": "completed",
  "email_sent": true,
  "email_sent_at": "2026-10-05T14:28:15.000Z",
  "reviewed_by": "agent_sarah",
  "message": "Run approved and completed — email dispatched."
}
```

---

### POST /runs/{run_id}/reject — Reject a paused run

```bash
curl -X POST http://localhost:8000/runs/550e8400-.../reject \
  -H "Content-Type: application/json" \
  -d '{"reviewer_note": "Reply needs a warmer tone. Do not send.", "reviewed_by": "agent_sarah"}'
```

**Response 200:**
```json
{
  "run_id": "550e8400-...",
  "status": "rejected",
  "email_sent": false,
  "message": "Run rejected — no email sent. Draft discarded."
}
```

---

### POST /runs/{run_id}/retry — Retry a failed run

```bash
curl -X POST http://localhost:8000/runs/550e8400-.../retry \
  -H "Content-Type: application/json" \
  -d '{"clear_failure": true}'
```

LangGraph loads the last checkpoint (after the last completed node) and continues from there. `clear_failure: true` removes the `inject_failure` flag from state before resuming.

---

### POST /runs/{run_id}/cancel — Cancel a run

```bash
curl -X POST http://localhost:8000/runs/550e8400-.../cancel
```

Sets `status: "cancelled"` in both the DB and the checkpoint. All subsequent `/approve`, `/reject`, `/retry` return `409 Conflict`.

---

### GET /runs/{run_id}/history — Checkpoint history (debug)

Returns all serialised `AgentState` snapshots for a run — one per completed node. Shows exactly what state was saved at each checkpoint for audit and debugging.

---

## 11. Demo CLI

The interactive CLI (`demo_cli.py`) is the recommended way to demonstrate the project live.

```bash
py demo_cli.py
```

```
╭──────────────────────────────────────────────────────────╮
│  Customer Support Triage Orchestrator                    │
│  Interactive Demo CLI                                    │
│  Powered by: LangGraph · Nvidia Nemotron · FastAPI · PG  │
╰──────────────────────────────────────────────────────────╯

  [1]  Start a new workflow run (normal)
  [2]  Start a run WITH injected failure (test retry)
  [3]  View pending approvals  ⬅ runs waiting for YOU
  [4]  List all runs
  [5]  Inspect / act on a specific run
  [6]  Run automated evaluation (all 5 scenarios)
  [H]  Health check
  [Q]  Quit
```

**What the CLI shows after each run:**

- Step 1 — LLM output panel: `category`, `urgency`, `sentiment`, `reasoning`, thinking trace preview
- Step 2 — Specialist data: full billing invoice record **or** top-3 docs with relevance scores
- Step 3 — Full draft reply in a yellow panel with word count; LLM thinking trace in a dim italic panel above it
- Human approval prompt — `[A] Approve / [R] Reject / [C] Cancel` — waits for your input before continuing
- Step 5 — Email confirmation: recipient, subject, sent timestamp
- Final step trace table: all steps with status, duration, and evidence

---

## 12. Success Metrics

A run is declared **successful** when all of the following conditions are true simultaneously. These are the exact conditions checked by `evaluate.py`.

| Metric | Condition | How Measured |
|---|---|---|
| **Run created** | `run_id` (UUID4) returned by `POST /runs` | Response body `run_id` is non-null and parseable as UUID |
| **Triage completed** | `artifacts.category` ∈ {`"Billing"`, `"Tech"`} | `GET /runs/{id}` → `artifacts.category` |
| **Urgency assigned** | `artifacts.urgency` ∈ {`"High"`, `"Low"`} | `GET /runs/{id}` → `artifacts.urgency` |
| **Specialist data retrieved** | `artifacts.billing_info` OR `artifacts.relevant_docs` is non-empty | Presence of correct key in `artifacts` |
| **Draft reply generated** | `artifacts.draft_reply` is non-empty string, `artifacts.draft_word_count` > 0 | `GET /runs/{id}` → `artifacts.draft_reply` |
| **Human gate held** | `email_sent` is absent/false AND `send_email` not in `step_log` BEFORE `/approve` | State inspection before calling `/approve` |
| **Email dispatched** | `artifacts.email_sent == True` AND `artifacts.email_sent_at` is set | After `/approve`, `GET /runs/{id}` |
| **Step trace complete** | `step_log` has exactly 5 entries: triage → specialist → draft → human_approval → send_email | `len(step_log) == 5` |
| **All steps timed** | Every `step_log` entry has `started_at`, `ended_at`, `duration_ms` | Each entry checked for all three fields |
| **Final status** | `status == "completed"` | Top-level `status` field |
| **No errors** | `errors == []` | `errors` list is empty on a clean run |

### What a Successful Run Looks Like in the CLI

```
╭─────────────────────── Step 1 — Triage (LLM) ───────────────────────────╮
│   Category  : Billing                                                   │
│   Urgency   : High                                                      │
│   Sentiment : frustrated                                                │
│   Reasoning : Duplicate charge reported with specific transaction date  │
╰─────────────────────────────────────────────────────────────────────────╯

╭──────────────────── Step 2a — Billing API Data ─────────────────────────╮
│   Invoice ID   : INV-10156                                              │
│   Plan         : Pro Monthly                                            │
│   Status       : overdue                                                │
│   Amount Due   : $99.98                                                 │
│   Suspended    : No                                                     │
╰─────────────────────────────────────────────────────────────────────────╯

╭──────────────── Step 3 — LLM Draft Reply  (187 words) ──────────────────╮
│   Dear Customer,                                                        │
│   We sincerely apologise for the double charge on your account...       │
╰─────────────────────────────────────────────────────────────────────────╯

  ⏸  Workflow paused — awaiting your approval.
  [A] Approve → email sent   [R] Reject → email blocked   [C] Cancel

  Your decision: A
  Reviewer note: Billing data confirmed. Safe to send.

╭──────────────────────── Step Trace ─────────────────────────────────────╮
│ Step            │ Status    │ Duration  │ Evidence                      │
│ triage          │ completed │ 1,167 ms  │ category=Billing urgency=High │
│ billing_lookup  │ completed │    50 ms  │ invoice_id=INV-10156          │
│ draft_reply     │ completed │ 36,490 ms │ word_count=187                │
│ human_approval  │ approved  │ 42,000 ms │ reviewed_by=demo_agent        │
│ send_email      │ completed │     8 ms  │ idempotency_check=passed      │
╰─────────────────────────────────────────────────────────────────────────╯
  ✓ status: completed   ✓ email_sent: true
```

### Failure and Partial-Run Conditions

| Condition | Expected `status` | Recovery path |
|---|---|---|
| Step 2 API timeout or injected error | `failed` | `POST /runs/{id}/retry` |
| LLM returns unparseable JSON during triage | `failed` | `POST /runs/{id}/retry` |
| Human rejects the draft | `rejected` | No recovery — create a new run |
| Run cancelled before approval | `cancelled` | No recovery — create a new run |
| Email already sent (idempotency guard triggered) | `completed` | No action needed — skipped silently |

---

## 13. Evaluation Scenarios

Run all five automatically:

```bash
py evaluate.py
```

Each scenario prints a coloured pass/fail for every assertion. A summary table shows total results at the end.

---

### Scenario 1 — Normal Run

**Purpose:** Verify the full happy path works end-to-end.

**Steps:**
1. `POST /runs` with a Billing ticket
2. `GET /runs/{id}` — inspect pre-approval state
3. `POST /runs/{id}/approve`
4. `GET /runs/{id}` — inspect final state

**Pass criteria (all must be true):**

| Check | Expected |
|---|---|
| `run_id` is UUID4 | ✓ |
| `status` after trigger | `"paused"` |
| `artifacts.category` | `"Billing"` |
| `artifacts.draft_reply` non-empty | ✓ |
| `email_sent` before approve | `false` / absent |
| `send_email` in `step_log` before approve | absent |
| `status` after approve | `"completed"` |
| `email_sent` after approve | `true` |
| `email_sent_at` timestamp set | ✓ |
| `len(step_log)` | `5` |

---

### Scenario 2 — Inject Failure + Resume from Checkpoint

**Purpose:** Prove checkpoints are saved correctly and retry resumes at the right node, not from the beginning.

**Steps:**
1. `POST /runs` with `inject_failure: true`
2. `GET /runs/{id}` — confirm failed state
3. `POST /runs/{id}/retry` with `clear_failure: true`
4. (Approve if paused) → `GET /runs/{id}` — confirm completion

**Pass criteria:**

| Check | Expected |
|---|---|
| `status` after injected failure | `"failed"` |
| `errors[]` non-empty | ✓ |
| Failed step in `errors[0].step` | `"billing_lookup"` or `"doc_search"` |
| `email_sent` during failed run | `false` |
| After retry: `status` | `"completed"` |
| Count of `"triage"` entries in `step_log` | `1` (not replayed) |
| `email_sent` after retry+approve | `true` |

---

### Scenario 3 — Idempotency Proof

**Purpose:** Prove `send_email_node` does not dispatch a second email if called again on a completed run.

**Steps:**
1. Use the completed run from Scenario 1
2. Inspect `step_log` and `artifacts`
3. Verify the state-flag guard fields

**Pass criteria:**

| Check | Expected |
|---|---|
| `artifacts.email_sent` | `true` |
| `artifacts.email_sent_at` | non-null ISO timestamp |
| `send_email` step in `step_log` has `status` | `"completed"` with `idempotency_check: "passed — email not previously sent"` |
| If `send_email_node` called a second time | Would return `status: "skipped"` and evidence `"email_sent=True — idempotency guard triggered"` |

> The guard is in `app/graph/nodes.py` at the top of `send_email_node`: `if artifacts.get("email_sent") is True: return skipped entry`.

---

### Scenario 4 — Human Approval Gate (Downstream Blocked)

**Purpose:** Prove Step 5 (`send_email`) is completely unreachable until `/approve` is explicitly called.

**Steps:**
1. `POST /runs` with a Tech ticket
2. Immediately `GET /runs/{id}` — before calling `/approve`
3. Assert `send_email` is absent
4. `POST /runs/{id}/approve`
5. `GET /runs/{id}` — assert `send_email` now present and completed
6. `POST /runs/{id}/cancel` on a different paused run
7. `POST /runs/{id}/approve` on that cancelled run — expect `409`

**Pass criteria:**

| Check | Expected |
|---|---|
| `status` before approve | `"paused"` |
| `email_sent` before approve | `false` / absent |
| `"send_email"` in `step_log` before approve | absent (0 entries) |
| `human_approval.status` in `step_log` before approve | `"pending"` |
| `status` after approve | `"completed"` |
| `email_sent` after approve | `true` |
| `"send_email"` in `step_log` after approve | present, `status: "completed"` |
| `/approve` on cancelled run | `409 Conflict` |

---

### Scenario 5 — Cancellation and Safe Terminal State

**Purpose:** Prove cancellation is immediate, terminal, and blocks all further actions.

**Steps:**
1. `POST /runs` — get `run_id`
2. `POST /runs/{id}/cancel`
3. `GET /runs/{id}` — confirm cancelled state
4. `POST /runs/{id}/approve` — expect `409`

**Pass criteria:**

| Check | Expected |
|---|---|
| `status` after cancel | `"cancelled"` |
| `email_sent` after cancel | `false` / absent |
| `cancelled_at` timestamp | non-null |
| `/approve` after cancel | `409 Conflict` |
| No `send_email` in `step_log` | ✓ |

---

## 13. Sample Inputs and Outputs

### Input — Billing ticket (High Urgency)

```json
{
  "ticket_text": "My account has been suspended but I never missed a payment. My entire team is blocked from accessing production data. This is an emergency. Account: CUST-2002.",
  "customer_email": "cto@startupventures.com",
  "customer_id": "CUST-2002",
  "inject_failure": false
}
```

### Output — Draft reply (Nemotron generated)

```
Dear CTO Team at Startup Ventures,

We sincerely apologise for the service disruption you are experiencing. We have reviewed your 
account and identified the cause of the suspension. Our records show invoice INV-10345 for 
$599.00 has an outstanding balance — however, we are currently investigating whether this 
reflects a payment processing error on our end.

Effective immediately, we have restored your account access so your team can resume work. 
Our billing team will complete a full audit within the next 2 hours and you will receive a 
detailed follow-up by end of day. If a payment processing error is confirmed, all late fees 
will be waived and the suspension will be formally removed from your account record.

We understand the impact this has had on your operations and have escalated this to our 
senior billing specialist. You will have a dedicated point of contact for resolution.

Sincerely,
Customer Support — Priority Team
```

### Output — Step trace

```
╭────────────────────┬───────────────┬─────────────┬──────────────────────────────────────────╮
│ Step               │ Status        │ Duration    │ Evidence                                 │
├────────────────────┼───────────────┼─────────────┼──────────────────────────────────────────┤
│ triage             │ completed     │ 1,167 ms    │ category=Billing  urgency=High            │
│ billing_lookup     │ completed     │ 50 ms       │ invoice_id=INV-10345  status=overdue      │
│ draft_reply        │ completed     │ 36,490 ms   │ word_count=187                           │
│ human_approval     │ approved      │ 42,000 ms   │ reviewed_by=agent_sarah                  │
│ send_email         │ completed     │ 8 ms        │ idempotency_check=passed                 │
╰────────────────────┴───────────────┴─────────────┴──────────────────────────────────────────╯
```

---

## 14. Assumptions

1. **One category per ticket** — each ticket is either Billing or Tech, never both. Real-world tickets can be ambiguous; the mock fallback defaults to `Tech/Low`.

2. **Mock email dispatch** — `send_email_node` prints to stdout and records the timestamp. No real SMTP/SES integration.

3. **Mock specialist tools** — `billing_api.py` returns deterministic data based on an MD5 hash of `customer_id`. `doc_search.py` uses keyword overlap scoring instead of real embeddings.

4. **Single-tenant** — no authentication on the FastAPI endpoints. Suitable for an internal demo; production would add API key auth or OAuth.

5. **Nvidia NIM free tier** — the free tier occasionally returns `503 Service temporarily overloaded`. The code does not auto-retry on 503; set `USE_MOCK_LLM=true` to avoid this during demos.

6. **Sequential steps** — all nodes are sequential. Steps 2a and 2b are mutually exclusive (routed by category), not parallel.

7. **PostgreSQL local** — the default `.env` points to `127.0.0.1:5432`. For Docker usage, see [Docker](#17-docker).

---

## 15. Trade-offs and Limitations

### Trade-offs made

| Decision | Trade-off |
|---|---|
| Raw `openai` SDK instead of LangChain `ChatOpenAI` | Gains `reasoning_content` streaming from Nemotron; loses LangChain's retry/callback ecosystem |
| Two separate DB clients (asyncpg + psycopg3) | `runs` table queries stay fast via asyncpg; LangGraph's checkpointer requirement forces psycopg3 |
| `interrupt_before` rather than a manual pause flag | Atomic with checkpointing — no race condition possible; slightly less flexible than a flag |
| Status stored in both DB and checkpoint | DB row enables fast list queries; checkpoint is the authoritative source for GET /runs/{id} |
| Append-only `artifacts` dict | Full audit trail; slight memory growth on very long workflows — acceptable for this use case |

### Current Limitations

- **No authentication** — all API endpoints are open
- **No real vector search** — doc relevance is keyword overlap, not embedding similarity
- **No streaming to the client** — the API blocks until the graph pauses or completes; a websocket or SSE endpoint would improve UX for long LLM calls
- **Single approval level** — production might need tiered approval (agent → manager → compliance)
- **No rate limiting** — the Nvidia NIM free tier throttles; the app has no backoff beyond the SDK's built-in 2 retries
- **No observability** — no metrics, tracing, or alerting; `step_log` is the only audit mechanism

---

## 16. Next Steps

1. **Real vector search** — replace `doc_search.py` with `pgvector` or Pinecone using ticket text embeddings
2. **Real email dispatch** — swap mock send for AWS SES or SendGrid via `httpx`
3. **Streaming responses** — add a `GET /runs/{id}/stream` SSE endpoint that tails `step_log` in real time
4. **Authentication** — API key middleware or OAuth2 for the FastAPI layer
5. **Confidence-based routing** — route to human review automatically when triage confidence is below a threshold (requires structured output with a `confidence` field)
6. **Parallel specialist steps** — LangGraph supports fan-out; billing + docs could run in parallel for cross-category tickets
7. **Alerting** — webhook or Slack notification when a run fails or exceeds a time threshold
8. **Multi-tenant** — partition checkpoints and runs by workspace/team ID

---

## 17. Docker

### Start PostgreSQL only (recommended for local development)

```bash
docker-compose up -d postgres
```

Then run the app locally with `py -m uvicorn app.main:app --reload`.

### Start everything (PostgreSQL + app)

```bash
docker-compose up --build
```

The `app` service uses the internal Docker network hostname `postgres` instead of `127.0.0.1`. Environment variables in `docker-compose.yml` override `.env` for container-to-container connectivity.

### Verify

```bash
curl http://localhost:8000/health
```

```json
{"status": "healthy", "checks": {"database": "ok", "graph": "ok"}}
```

---

## Repository Structure

```
Workflow-Orchestrator/
├── app/
│   ├── main.py              # FastAPI application, lifespan, middleware
│   ├── config.py            # pydantic-settings, all env vars
│   ├── api/
│   │   └── routes.py        # All 8 FastAPI endpoints
│   ├── db/
│   │   ├── database.py      # Async engine, session factory, get_db
│   │   ├── models.py        # Run ORM model (JSONB artifacts_snapshot)
│   │   └── init_db.py       # Standalone table creation script
│   ├── graph/
│   │   ├── state.py         # AgentState TypedDict
│   │   ├── nodes.py         # All 5 nodes + error_terminal (Nemotron LLM)
│   │   ├── edges.py         # Conditional routing functions
│   │   └── graph.py         # LangGraph assembly + AsyncPostgresSaver
│   └── tools/
│       ├── billing_api.py   # Mock Billing API (20 invoice scenarios)
│       └── doc_search.py    # Mock vector search (30 KB articles)
├── scripts/
│   └── init_extensions.sql  # PostgreSQL extensions
├── seed_data.py             # 50 diverse demo records
├── evaluate.py              # 5-scenario end-to-end evaluation
├── demo_cli.py              # Interactive Rich CLI for demonstrations
├── .env.example             # Environment variable template
├── requirements.txt         # Python dependencies (range-pinned)
├── Dockerfile               # Container build
├── docker-compose.yml       # PostgreSQL + app services
├── Project.md               # Workflow design and evaluation mapping
├── Architecture.md          # Deep technical architecture reference
└── AGENT_WORKFLOW.md        # AI tool usage, failures, manual changes
```
