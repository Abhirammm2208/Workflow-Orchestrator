# Customer Support Issue Triage — Durable Workflow Orchestrator

## Overview

A production-grade, durable and resumable multi-step agent workflow that triages incoming customer support tickets. Built on **LangGraph** for orchestration, **FastAPI** for the API layer, and **PostgreSQL + SQLAlchemy** for persistence.

The system is designed to satisfy every evaluation criterion: unique run identity, explicit step I/O, checkpoint-based resumability, idempotency, conditional routing, human-in-the-loop approval, full audit trail, and safe cancellation.

---

## Problem Statement

Customer support tickets arrive in an unstructured form. A human agent must read each ticket, decide its urgency and category, pull supporting information, draft a reply, and send it — a process that is slow, inconsistent, and error-prone at scale.

This orchestrator automates that pipeline while keeping a human in control of the most consequential step: approving the outgoing reply before it is sent.

---

## Workflow: 5 Steps

```
[Ticket Arrives]
      │
      ▼
┌─────────────┐
│  Step 1     │  Categorize & detect sentiment
│  Triage     │  Output: category (Billing/Tech), urgency (High/Low)
└──────┬──────┘
       │
       ▼  (conditional edge)
┌──────────────────────────────────────────────┐
│  Step 2a: Billing API (mock)                  │  if category == Billing
│  Step 2b: Vector Search / Docs (mock)         │  if category == Tech
└──────────────────────┬───────────────────────┘
                       │
                       ▼
              ┌────────────────┐
              │  Step 3        │  Draft resolution reply (LLM)
              │  Draft Reply   │
              └───────┬────────┘
                      │
                      ▼
             ┌─────────────────┐
             │  Step 4         │  PAUSE — Human reviews and approves/rejects
             │  Human Approval │
             └───────┬─────────┘
                     │  (only after approval)
                     ▼
            ┌─────────────────┐
            │  Step 5         │  Send email (idempotent)
            │  Send Email     │
            └─────────────────┘
```

---

## Step-by-Step Details

### Step 1 — Triage (Categorization & Sentiment)

| | |
|---|---|
| **Input** | Raw ticket text (`input_data`) |
| **Action** | LLM call: extract `category` (Billing / Tech) and `urgency` (High / Low) |
| **Output** | `artifacts.category`, `artifacts.urgency`, `artifacts.triage_reasoning` |
| **On Failure** | Retryable — pure LLM inference, no side effects |

### Step 2a — Billing Specialist (conditional)

| | |
|---|---|
| **Input** | Customer ID extracted from ticket |
| **Action** | Mock HTTP call to "Billing API" → returns invoice status, last payment date |
| **Output** | `artifacts.billing_info` |
| **Routing Condition** | `artifacts.category == "Billing"` |

### Step 2b — Tech Docs Search (conditional)

| | |
|---|---|
| **Input** | Keywords extracted from ticket |
| **Action** | Mock vector search → returns top 3 relevant documentation snippets |
| **Output** | `artifacts.relevant_docs` |
| **Routing Condition** | `artifacts.category == "Tech"` |

### Step 3 — Draft Resolution

| | |
|---|---|
| **Input** | Ticket text + specialist output (billing info or docs) |
| **Action** | LLM drafts a customer-facing reply |
| **Output** | `artifacts.draft_reply` |
| **On Failure** | Retryable |

### Step 4 — Human Approval (INTERRUPT)

| | |
|---|---|
| **Input** | `artifacts.draft_reply` |
| **Action** | Graph **pauses**. Status set to `"paused"`. Human reviews via `POST /runs/{run_id}/approve` or `POST /runs/{run_id}/reject` |
| **Output** | `artifacts.approval_status` (`approved` / `rejected`), `artifacts.reviewer_note` |
| **Downstream** | Step 5 only executes **after** explicit approval |

### Step 5 — Send Email (Idempotent)

| | |
|---|---|
| **Input** | `artifacts.draft_reply`, customer email |
| **Action** | Mock email dispatch |
| **Idempotency Guard** | Checks `artifacts.email_sent == True` before sending. If already sent, skips silently. |
| **Output** | `artifacts.email_sent = True`, `artifacts.email_sent_at` timestamp |

---

## State Schema

```python
class AgentState(TypedDict):
    run_id: str                  # UUID, immutable
    status: str                  # running | paused | failed | completed | cancelled
    input_data: str              # Original ticket text
    customer_email: str          # Recipient
    customer_id: str             # For billing lookup
    inject_failure: bool         # Debug flag — forces failure at Step 2
    artifacts: dict              # All step outputs accumulated here
    errors: list[dict]           # {step, error, timestamp} per failure
    step_log: list[dict]         # {step, status, started_at, ended_at, evidence}
```

### `artifacts` keys (populated progressively)

| Key | Set by |
|---|---|
| `category` | Step 1 |
| `urgency` | Step 1 |
| `triage_reasoning` | Step 1 |
| `billing_info` | Step 2a |
| `relevant_docs` | Step 2b |
| `draft_reply` | Step 3 |
| `approval_status` | Step 4 (via API) |
| `reviewer_note` | Step 4 (via API) |
| `email_sent` | Step 5 |
| `email_sent_at` | Step 5 |

---

## Evaluation Criteria Mapping

| Requirement | How It Is Met |
|---|---|
| Accept trigger, create unique run | `POST /runs` generates `uuid4`, stores initial state in PostgreSQL |
| 3+ steps with explicit I/O and state | 5 nodes, each reads from and writes to `AgentState` |
| Persist checkpoints, resume on failure | LangGraph `PostgresSaver` — every node completion writes a checkpoint row |
| Retry safe steps without duplicate effects | Idempotency guard in Step 5; retryable LLM steps have no side effects |
| Route to mock tool/specialist | Conditional edge after Step 1 routes to Billing API or Doc Search |
| Pause for human approval | `interrupt_before=["human_approval"]` halts graph; `/approve` resumes |
| Record status, evidence, errors, timing | `step_log` list in state; each entry has `started_at`, `ended_at`, `evidence`, `status` |
| Support cancellation | `POST /runs/{run_id}/cancel` sets status to `"cancelled"`, no further nodes run |
| Normal run | Demonstrated in `evaluate.py` |
| Inject recoverable failure, resume | `inject_failure: true` flag raises exception at Step 2; `/retry` resumes |
| Retry one step, prove idempotency | Calling Step 5 twice; second call logs "skipped — already sent" |
| Pause for approval, prove no early execution | Step 5 unreachable until `/approve` called — demonstrated in `evaluate.py` |
| Readable run trace | `GET /runs/{run_id}` returns full `step_log` with timestamps and evidence |

---

## API Surface

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/runs` | Start a new workflow run |
| `GET` | `/runs/{run_id}` | Get full state + step trace |
| `GET` | `/runs` | List all runs (id, status, created_at) |
| `POST` | `/runs/{run_id}/approve` | Resume a paused run (human approved) |
| `POST` | `/runs/{run_id}/reject` | Resume a paused run (human rejected) |
| `POST` | `/runs/{run_id}/retry` | Re-trigger a failed run from last checkpoint |
| `POST` | `/runs/{run_id}/cancel` | Force terminal `cancelled` state |

---

## Tech Stack

| Component | Technology | Reason |
|---|---|---|
| Orchestration | LangGraph | Native support for state, conditional edges, checkpointing, and `interrupt_before` for human-in-the-loop |
| API Layer | FastAPI | Async, type-safe, auto-generates OpenAPI docs |
| Persistence | PostgreSQL + SQLAlchemy | Production-grade; LangGraph's `AsyncPostgresSaver` writes checkpoints directly |
| LLM | **Nvidia Nemotron** (`nvidia/llama-3.3-nemotron-super-49b-v1`) via LangChain | OpenAI-compatible NIM API endpoint — enterprise-grade reasoning, structured output support for triage |
| LangChain Client | `langchain-openai` (`ChatOpenAI`) | Nvidia NIM exposes an OpenAI-compatible REST API; `ChatOpenAI` with custom `base_url` + `api_key` connects to it without a separate SDK |
| Async Tasks | Python `asyncio` / LangGraph async | Graph runs non-blocking; API stays responsive during long LLM calls |
| Configuration | `pydantic-settings` + `.env` | Clean env-based config (DB URL, API keys) |
| Evaluation | `evaluate.py` (Python script) | End-to-end walkthrough of all 5 evaluation scenarios with rich terminal output |

---

## Project Structure

```
workflow-orchestrator/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── api/
│   │   └── routes.py        # All FastAPI route handlers
│   ├── graph/
│   │   ├── state.py         # AgentState TypedDict definition
│   │   ├── nodes.py         # All 5 node functions
│   │   ├── edges.py         # Conditional routing logic
│   │   └── graph.py         # LangGraph graph assembly + checkpointer
│   ├── tools/
│   │   ├── billing_api.py   # Mock Billing API tool
│   │   └── doc_search.py    # Mock vector doc search tool
│   ├── db/
│   │   ├── database.py      # SQLAlchemy engine + session factory
│   │   └── models.py        # ORM models for run metadata
│   └── config.py            # pydantic-settings Config class
├── evaluate.py              # End-to-end evaluation script
├── .env.example             # Environment variable template
├── requirements.txt         # Python dependencies
├── docker-compose.yml       # PostgreSQL + app container setup
├── Project.md               # This file
└── Architecture.md          # Technical deep-dive
```

---

## Environment Variables

```env
# Nvidia Nemotron (via NIM endpoint — OpenAI-compatible)
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxx
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres:Abhiram@123@localhost:5432/Workflow
PSYCOPG_URL=postgresql+psycopg://postgres:Abhiram@123@localhost:5432/Workflow
CHECKPOINT_DB_URI=postgresql://postgres:Abhiram@123@localhost:5432/Workflow

APP_ENV=development
LOG_LEVEL=INFO
USE_MOCK_LLM=false
```

---

## Running the Project

```bash
# 1. Start PostgreSQL
docker-compose up -d postgres

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run database migrations
python -m app.db.init_db

# 4. Start the API server
uvicorn app.main:app --reload

# 5. Run the full evaluation
python evaluate.py
```

---

## Evaluation Script Preview (`evaluate.py`)

```
[1/5] Starting normal run...
      → POST /runs  →  run_id: abc-123
      → GET  /runs/abc-123  →  status: completed
      → Trace: triage ✓ | billing_lookup ✓ | draft_reply ✓ | approved ✓ | email_sent ✓

[2/5] Injecting recoverable failure at Step 2...
      → POST /runs (inject_failure=true)  →  run_id: def-456
      → GET  /runs/def-456  →  status: failed, step: specialist_routing
      → POST /runs/def-456/retry  →  status: completed

[3/5] Testing idempotency (re-running Step 5)...
      → Step 5 called twice on run abc-123
      → Second call: step_log shows "email_send skipped — already sent"

[4/5] Testing human approval gate...
      → POST /runs  →  run_id: ghi-789
      → GET  /runs/ghi-789  →  status: paused (Step 5 NOT executed yet)
      → POST /runs/ghi-789/approve
      → GET  /runs/ghi-789  →  status: completed (Step 5 NOW executed)

[5/5] Testing cancellation...
      → POST /runs  →  run_id: jkl-000
      → POST /runs/jkl-000/cancel  →  status: cancelled
      → GET  /runs/jkl-000  →  no downstream steps executed
```
