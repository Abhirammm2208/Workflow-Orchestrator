# Architecture — Durable Workflow Orchestrator

## Design Philosophy

The system is built around three principles :

1. **State is the source of truth.** Every piece of information  — inputs, outputs, errors, decisions — lives in `AgentState`. No side-channel variables, no global state.
2. **The graph is append-only.** Nodes only add to `step_log` and `artifacts`. They never delete or overwrite existing data. This makes every run fully auditable.
3. **Persistence is structural, not bolted on.** PostgreSQL checkpoints are wired into the graph at construction time via `AsyncPostgresSaver`. The application code never manually saves state.

---

## System Architecture Diagram

```mermaid
graph TB
    subgraph Client["Client Layer"]
        CLI["demo_cli.py<br/>Interactive Rich CLI"]
        API_CLIENT["curl / Swagger UI<br/>http://localhost:8000/docs"]
        EVAL["evaluate.py<br/>Automated evaluation"]
    end

    subgraph FastAPI["FastAPI Application (Port 8000)"]
        R1["POST /runs"]
        R2["GET /runs / GET /runs/{id}"]
        R3["POST /runs/{id}/approve"]
        R4["POST /runs/{id}/reject"]
        R5["POST /runs/{id}/retry"]
        R6["POST /runs/{id}/cancel"]
    end

    subgraph LangGraph["LangGraph StateGraph (Workflow Engine)"]
        T["triage_node<br/>LLM: classify ticket"]
        BL["billing_lookup_node<br/>Mock Billing API"]
        DS["doc_search_node<br/>Mock Vector Search"]
        DR["draft_reply_node<br/>LLM: generate reply"]
        HA["human_approval_node<br/>⏸ INTERRUPT BEFORE"]
        SE["send_email_node<br/>Idempotent dispatch"]
        ET["error_terminal_node<br/>Safe failure state"]
    end

    subgraph PostgreSQL["PostgreSQL (Database: Workflow)"]
        RUNS["Table: runs<br/>Run metadata, status, artifacts_snapshot"]
        CKP["Table: checkpoints<br/>Full AgentState per node"]
        CKW["Table: checkpoint_writes<br/>In-flight write journal"]
    end

    subgraph LLM["Nvidia NIM Endpoint"]
        NEM["nemotron-3-ultra-550b-a55b<br/>enable_thinking=True<br/>streaming reasoning_content"]
    end

    CLI --> FastAPI
    API_CLIENT --> FastAPI
    EVAL --> FastAPI

    R1 --> LangGraph
    R3 --> LangGraph
    R4 --> LangGraph
    R5 --> LangGraph
    R2 --> PostgreSQL

    T --> NEM
    DR --> NEM

    LangGraph -->|"checkpoint after every node"| PostgreSQL
    FastAPI -->|"run metadata"| RUNS
```

---

## Workflow State Machine

```mermaid
stateDiagram-v2
    [*] --> running : POST /runs

    running --> paused : graph hits interrupt_before human_approval
    running --> failed : node raises exception
    running --> cancelled : POST /runs/{id}/cancel

    paused --> running : POST /runs/{id}/approve (graph resumes)
    paused --> rejected : POST /runs/{id}/reject
    paused --> cancelled : POST /runs/{id}/cancel

    failed --> running : POST /runs/{id}/retry (resumes from checkpoint)
    failed --> cancelled : POST /runs/{id}/cancel

    running --> completed : send_email_node completes

    completed --> [*]
    cancelled --> [*]
    rejected --> [*]
```

---

## Node Execution Flow

```mermaid
flowchart TD
    START([Ticket Arrives<br/>POST /runs]) --> T

    T["Step 1: triage_node<br/>─────────────────<br/>IN:  input_data<br/>OUT: category, urgency,<br/>     sentiment, reasoning<br/>LLM: Nvidia Nemotron"]

    T -->|category == Billing| BL
    T -->|category == Tech| DS
    T -->|unknown category| ET

    BL["Step 2a: billing_lookup_node<br/>─────────────────<br/>IN:  customer_id<br/>OUT: billing_info<br/>     invoice_id, status, amount<br/>TOOL: Mock Billing API"]

    DS["Step 2b: doc_search_node<br/>─────────────────<br/>IN:  input_data<br/>OUT: relevant_docs<br/>     top-3 KB articles + scores<br/>TOOL: Mock Vector Search"]

    ET["error_terminal_node<br/>─────────────────<br/>status → failed<br/>Safe terminal state"]

    BL --> DR
    DS --> DR

    DR["Step 3: draft_reply_node<br/>─────────────────<br/>IN:  billing_info OR relevant_docs<br/>OUT: draft_reply, word_count<br/>     draft_thinking<br/>LLM: Nvidia Nemotron"]

    DR -->|"⏸ GRAPH PAUSES HERE<br/>interrupt_before"| HA

    HA["Step 4: human_approval_node<br/>─────────────────<br/>Body runs ONLY after<br/>/approve or /reject API call<br/>IN:  approval_status, reviewer_note<br/>OUT: approved OR rejected"]

    HA -->|approved| SE
    HA -->|rejected| END_R([status: rejected<br/>No email sent])

    SE["Step 5: send_email_node<br/>─────────────────<br/>IDEMPOTENCY GUARD:<br/>if email_sent == True → skip<br/>IN:  draft_reply, customer_email<br/>OUT: email_sent=True, sent_at"]

    SE --> END_C([status: completed])

    style HA fill:#fff3cd,stroke:#ffc107,color:#000
    style ET fill:#f8d7da,stroke:#dc3545,color:#000
    style END_C fill:#d4edda,stroke:#28a745,color:#000
    style END_R fill:#f8d7da,stroke:#dc3545,color:#000
```

---

## Checkpoint Persistence Flow

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant Graph as LangGraph
    participant PG as PostgreSQL

    API->>Graph: ainvoke(initial_state, config={thread_id: run_id})

    Graph->>Graph: execute triage_node
    Graph->>PG: INSERT INTO checkpoints (state after triage)
    Note over PG: state = {status: running, artifacts: {category, urgency}}

    Graph->>Graph: execute billing_lookup_node
    Graph->>PG: INSERT INTO checkpoints (state after billing_lookup)
    Note over PG: state = {artifacts: {billing_info, ...}}

    Graph->>Graph: execute draft_reply_node
    Graph->>PG: INSERT INTO checkpoints (state after draft_reply)
    Note over PG: state = {artifacts: {draft_reply, ...}}

    Graph-->>API: return (paused at human_approval interrupt)
    API->>PG: UPDATE runs SET status='paused'

    Note over API,PG: Run is PAUSED. Server can restart here — no work lost.

    API->>Graph: aupdate_state + ainvoke(None, config)
    Note over API: triggered by POST /runs/{id}/approve

    Graph->>Graph: execute human_approval_node
    Graph->>PG: INSERT INTO checkpoints (state after approval)

    Graph->>Graph: execute send_email_node
    Graph->>PG: INSERT INTO checkpoints (state after email)
    Note over PG: state = {status: completed, email_sent: true}

    Graph-->>API: return final state
    API->>PG: UPDATE runs SET status='completed'
```

---

## Failure and Retry Flow

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI
    participant Graph as LangGraph
    participant PG as PostgreSQL

    Client->>API: POST /runs (inject_failure=true)
    API->>Graph: ainvoke(initial_state)

    Graph->>Graph: triage_node ✓
    Graph->>PG: checkpoint saved (triage complete)

    Graph->>Graph: billing_lookup_node → RAISES RuntimeError
    Graph->>PG: checkpoint saved (status=failed, errors=[...])
    Graph-->>API: return failed state

    API->>PG: UPDATE runs SET status='failed'
    API-->>Client: 201 {status: failed, run_id: ...}

    Note over Client,PG: Checkpoint preserved. Triage NOT re-run on retry.

    Client->>API: POST /runs/{id}/retry (clear_failure=true)
    API->>Graph: aupdate_state({inject_failure: false, status: running})
    API->>Graph: ainvoke(None, config)

    Note over Graph: Loads checkpoint AFTER triage — skips triage entirely

    Graph->>Graph: billing_lookup_node ✓ (no failure this time)
    Graph->>PG: checkpoint saved

    Graph->>Graph: draft_reply_node ✓
    Graph->>PG: checkpoint saved

    Graph-->>API: return (paused at human_approval)
    API-->>Client: 200 {status: paused, retry_count: 1}
```

---

## Idempotency Guard

```mermaid
flowchart TD
    START([send_email_node called]) --> CHECK

    CHECK{"artifacts.email_sent\n== True?"}

    CHECK -->|YES — already sent| SKIP["Return step_log entry:\nstatus: skipped\nevidence: email_sent=True\nidempotency guard triggered"]
    CHECK -->|NO — first execution| SEND["Mock email dispatch\nto customer_email"]

    SEND --> PERSIST["Set artifacts.email_sent = True\nSet artifacts.email_sent_at = now()\nReturn step_log: status=completed"]

    SKIP --> END([Node exits — no duplicate send])
    PERSIST --> END2([Node exits — email dispatched once])

    style SKIP fill:#cce5ff,stroke:#004085,color:#000
    style PERSIST fill:#d4edda,stroke:#28a745,color:#000
```

---

## Database Schema

```mermaid
erDiagram
    runs {
        VARCHAR run_id PK
        VARCHAR status
        VARCHAR category
        VARCHAR urgency
        TEXT ticket_text
        VARCHAR customer_email
        VARCHAR customer_id
        BOOLEAN inject_failure
        INTEGER retry_count
        TEXT reviewer_note
        VARCHAR reviewed_by
        JSONB artifacts_snapshot
        TEXT last_error
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
        TIMESTAMPTZ completed_at
    }

    checkpoints {
        TEXT thread_id PK
        TEXT checkpoint_ns PK
        TEXT checkpoint_id PK
        TEXT parent_id
        TEXT type
        JSONB checkpoint
        JSONB metadata
    }

    checkpoint_writes {
        TEXT thread_id PK
        TEXT checkpoint_ns PK
        TEXT checkpoint_id PK
        TEXT task_id PK
        INTEGER idx PK
        TEXT channel
        TEXT type
        JSONB value
    }

    runs ||--o{ checkpoints : "thread_id = run_id"
    checkpoints ||--o{ checkpoint_writes : "checkpoint_id"
```

> `runs` is managed by SQLAlchemy ORM (asyncpg driver). `checkpoints` and `checkpoint_writes` are managed by LangGraph's `AsyncPostgresSaver` (psycopg3 driver). Both point to the same PostgreSQL database.

---

## Component Breakdown

### 1. LangGraph Graph Engine

LangGraph models workflows as a directed graph:

- **Nodes** — Python async functions receiving `AgentState`, returning a partial update dict
- **Edges** — connect nodes; conditional edges use a routing function to pick the next node at runtime
- **Checkpointer** — intercepts every node completion and persists the full state snapshot

#### LLM — Nvidia Nemotron via NIM

Uses `nvidia/nemotron-3-ultra-550b-a55b` via Nvidia's [NIM inference platform](https://build.nvidia.com/). Uses the raw `openai` SDK (not LangChain) to capture `reasoning_content` from the streaming response:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=settings.nvidia_api_key,
)

stream = client.chat.completions.create(
    model="nvidia/nemotron-3-ultra-550b-a55b",
    messages=[...],
    temperature=0.6,
    extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    stream=True,
)

for chunk in stream:
    reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
    content   = getattr(chunk.choices[0].delta, "content", None)
```

Two temperature profiles:
- `temperature=0.3` for triage — deterministic classification
- `temperature=0.7` for draft reply — varied, human-sounding responses

#### Why raw `openai` SDK, not LangChain `ChatOpenAI`

LangChain's `ChatOpenAI` silently drops `reasoning_content` from the stream delta — it only surfaces the `content` field. The `nemotron-3-ultra-550b-a55b` model returns its internal reasoning in `reasoning_content` when `enable_thinking=True`. Using the raw SDK is the only way to capture the full thinking trace.

---

### 2. Persistence Layer — PostgreSQL+SQLAlchemy

#### Two distinct storage concernsn

| Store | Technology | Purpose |
|---|---|---|
| **LangGraph checkpoints** | `AsyncPostgresSaver` (psycopg3) | Serialised `AgentState` snapshots after every node |
| **Run metadata** | SQLAlchemy ORM (asyncpg) | Fast lookup of run_id → status without deserialising checkpoints |

#### Why two drivers

- `asyncpg` — fastest async PostgreSQL driver; used by SQLAlchemy for ORM queries (`SELECT runs WHERE run_id = $1`)
- `psycopg3` — LangGraph's `AsyncPostgresSaver` hard-requires a `psycopg.AsyncConnection`; not compatible with asyncpg

Both point to the same database, managed as independent connection pools.

#### `AsyncPostgresSaver` setup

```python
import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

connection = await psycopg.AsyncConnection.connect(
    settings.checkpoint_db_uri,
    autocommit=True,
)
checkpointer = AsyncPostgresSaver(connection)
await checkpointer.setup()  # creates checkpoints + checkpoint_writes tables
```

---

### 3. FastAPI API Layer

All endpoints are `async def`. The graph and DB session are dependency-injected.

**Status detection after `ainvoke()`:**

LangGraph returns `status: "running"` when the graph pauses at `interrupt_before` — because the thread is still "in progress" from the graph's perspective. The API layer detects a true pause by inspecting `artifacts` directly:

```python
if artifacts.get("email_sent") is True:
    current_status = "completed"
elif artifacts.get("draft_reply"):
    current_status = "paused"   # graph paused at human_approval
else:
    current_status = graph_status  # running or failed
```

---

### 4. Node Contract

Every node follows this contract:

```python
async def step_name(state: AgentState) -> dict:
    started = _now_iso()
    try:
        # ... do work ...
        ended = _now_iso()
        return {
            "artifacts": {**state["artifacts"], "new_key": value},
            "step_log":  state["step_log"] + [_log_entry(step, "completed", started, ended, evidence)],
            "errors":    state["errors"],
        }
    except Exception as exc:
        ended = _now_iso()
        return {
            "status":   "failed",
            "step_log": state["step_log"] + [_log_entry(step, "failed", started, ended, {"error": str(exc)})],
            "errors":   state["errors"] + [{"step": step, "error": str(exc), "timestamp": ended}],
            "artifacts": state["artifacts"],
        }
```

---

### 5. Idempotency Strategy

**Level 1 — State flag guard (Step 5)**

`send_email_node` checks `artifacts["email_sent"] == True` before dispatching. If True, returns a `"skipped"` log entry and exits. Safe to call any number of times.

**Level 2 — LangGraph checkpoint replay**

Completed nodes are never re-entered on retry. The graph resumes *after* the last completed checkpoint. Steps 1–4 are inherently idempotent at the orchestration level.

---

### 6. Key Design Decisions

| Decision | Rationale |
|---|---|
| `interrupt_before` vs manual pause flag | Atomic with checkpointing — no race condition between flag write and next node starting |
| Separate `runs` table vs checkpoints only | Checkpoint deserialization is expensive for list queries; `runs` table is a cheap index |
| Mock tools return realistic data structures | Swapping for real APIs requires only changing the tool module — node logic unchanged |
| `inject_failure` stored in AgentState | Persisted in checkpoint — failure scenario is reproducible and resumable across restarts |
| Append-only `artifacts` and `step_log` | Full audit trail; every state transition is visible; simplifies checkpoint diffing |
