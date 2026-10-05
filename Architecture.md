# Architecture — Durable Workflow Orchestrator

## Design Philosophy

The system is built around three principles:

1. **State is the source of truth.** Every piece of information — inputs, outputs, errors, decisions — lives in `AgentState`. No side-channel variables, no global state.
2. **The graph is append-only.** Nodes only add to `step_log` and `artifacts`. They never delete or overwrite existing data. This makes every run fully auditable.
3. **Persistence is structural, not bolted on.** PostgreSQL checkpoints are wired into the graph at construction time via `PostgresSaver`. The application code never manually saves state.

---

## Component Breakdown

### 1. LangGraph Graph Engine

LangGraph is a stateful graph execution framework built on top of LangChain. It models workflows as a directed graph where:

- **Nodes** are Python functions that receive `AgentState` and return a partial update dict.
- **Edges** connect nodes. A **conditional edge** uses a routing function to decide the next node at runtime.
- **Checkpointer** intercepts every node completion and persists the full state snapshot to the configured backend.

#### LLM — Nvidia Nemotron via NIM

The system uses `nvidia/llama-3.3-nemotron-super-49b-v1` accessed through Nvidia's [NIM inference platform](https://build.nvidia.com/). The NIM endpoint is fully OpenAI-compatible, so LangChain's `ChatOpenAI` client is used with a custom `base_url` and `api_key`:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="nvidia/llama-3.3-nemotron-super-49b-v1",
    api_key=settings.nvidia_api_key,         # nvapi-... key from build.nvidia.com
    base_url="https://integrate.api.nvidia.com/v1",
    temperature=0.3,
    timeout=30,
)
```

Two temperature profiles are used:
- **temperature=0.2** for triage (deterministic category/urgency classification)
- **temperature=0.65** for draft reply generation (varied, human-sounding responses)

Structured output (Pydantic model) is used for triage via `llm.with_structured_output(TriageOutput)`.

A `USE_MOCK_LLM=true` flag bypasses real API calls for local testing without an API key.

| Feature | LangGraph | Airflow | Celery |
|---|---|---|---|
| Human-in-the-loop (interrupt) | Native `interrupt_before` | Requires custom sensor | Not built-in |
| State as first-class object | TypedDict state | XCom (limited) | No native state |
| Resume from checkpoint | Native | Partial (task instance) | No |
| Conditional routing | Native conditional edges | Branching operators | No |
| LLM-native | Yes (LangChain integration) | No | No |

#### Graph Definition (simplified)

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

builder = StateGraph(AgentState)

builder.add_node("triage",            nodes.triage_step)
builder.add_node("billing_lookup",    nodes.billing_lookup_step)
builder.add_node("doc_search",        nodes.doc_search_step)
builder.add_node("draft_reply",       nodes.draft_reply_step)
builder.add_node("human_approval",    nodes.human_approval_step)
builder.add_node("send_email",        nodes.send_email_step)

builder.set_entry_point("triage")

builder.add_conditional_edges(
    "triage",
    edges.route_to_specialist,      # returns "billing_lookup" | "doc_search" | "error"
    {
        "billing_lookup": "billing_lookup",
        "doc_search":     "doc_search",
        "error":          END,
    }
)

builder.add_edge("billing_lookup", "draft_reply")
builder.add_edge("doc_search",     "draft_reply")
builder.add_edge("draft_reply",    "human_approval")
builder.add_edge("human_approval", "send_email")
builder.add_edge("send_email",     END)

graph = builder.compile(
    checkpointer=checkpointer,          # AsyncPostgresSaver
    interrupt_before=["human_approval"] # Pause here for human review
)
```

---

### 2. Persistence Layer — PostgreSQL + SQLAlchemy

#### Two distinct storage concerns

| Store | Technology | Purpose |
|---|---|---|
| **LangGraph checkpoints** | `AsyncPostgresSaver` (uses `psycopg` under the hood) | Serialized `AgentState` snapshots after every node |
| **Run metadata** | SQLAlchemy ORM (`runs` table) | Fast lookup of run_id → status without deserializing checkpoints |

#### Why PostgreSQL over SQLite?

- Concurrent API requests — SQLite has write-lock contention; PostgreSQL handles parallel writers cleanly.
- Production deployability — any cloud provider (RDS, Cloud SQL, Supabase) works as a drop-in.
- `AsyncPostgresSaver` is LangGraph's recommended checkpointer for production use.
- JSON/JSONB column type for native artifact storage and querying.

#### `AsyncPostgresSaver` Integration

```python
# app/graph/graph.py
import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def create_graph():
    conn = await psycopg.AsyncConnection.connect(settings.DATABASE_URL)
    checkpointer = AsyncPostgresSaver(conn)
    await checkpointer.setup()   # Creates langgraph_checkpoints table
    return compile_graph(checkpointer)
```

LangGraph creates two tables automatically:
- `checkpoints` — one row per (thread_id, checkpoint_id), stores full state JSON
- `checkpoint_writes` — pending writes mid-node (crash recovery)

#### SQLAlchemy ORM — Run Metadata Table

```python
# app/db/models.py
class Run(Base):
    __tablename__ = "runs"

    run_id       = Column(String, primary_key=True)   # Same as LangGraph thread_id
    status       = Column(String, nullable=False)      # running|paused|failed|completed|cancelled
    category     = Column(String)                      # Billing | Tech (denormalized for fast filter)
    urgency      = Column(String)                      # High | Low
    created_at   = Column(DateTime, default=func.now())
    updated_at   = Column(DateTime, onupdate=func.now())
    completed_at = Column(DateTime)
```

This table is the source for `GET /runs` (list view) without touching checkpoint data.

---

### 3. FastAPI API Layer

All endpoints are `async def`. The graph and DB session are dependency-injected.

#### Starting a Run — `POST /runs`

```
Request Body:
{
  "ticket_text": "I was charged twice for my subscription.",
  "customer_email": "user@example.com",
  "customer_id": "CUST-4421",
  "inject_failure": false
}

Response 201:
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running"
}
```

Internally:
1. Generate `uuid4` → `run_id`
2. Insert row into `runs` table (status=`running`)
3. Call `graph.ainvoke(initial_state, config={"configurable": {"thread_id": run_id}})`
4. Graph runs until `human_approval` node → pauses → API returns immediately

#### Fetching a Run Trace — `GET /runs/{run_id}`

```
Response 200:
{
  "run_id": "550e8400-...",
  "status": "paused",
  "artifacts": {
    "category": "Billing",
    "urgency": "High",
    "billing_info": { "invoice_id": "INV-882", "status": "overdue" },
    "draft_reply": "Dear Customer, we've reviewed your account..."
  },
  "step_log": [
    {
      "step": "triage",
      "status": "completed",
      "started_at": "2026-10-05T10:00:00Z",
      "ended_at":   "2026-10-05T10:00:02Z",
      "evidence":   { "category": "Billing", "urgency": "High" }
    },
    {
      "step": "billing_lookup",
      "status": "completed",
      "started_at": "2026-10-05T10:00:02Z",
      "ended_at":   "2026-10-05T10:00:03Z",
      "evidence":   { "invoice_id": "INV-882" }
    },
    {
      "step": "draft_reply",
      "status": "completed",
      ...
    },
    {
      "step": "human_approval",
      "status": "pending",
      ...
    }
  ],
  "errors": []
}
```

The state is read directly from the latest LangGraph checkpoint via `graph.aget_state(config)`.

#### Approving a Run — `POST /runs/{run_id}/approve`

```
Request Body:
{
  "reviewer_note": "Looks good, send it."
}
```

Internally:
1. Load current state from checkpoint
2. Inject `approval_status: "approved"` and `reviewer_note` into `artifacts`
3. Call `graph.ainvoke(Command(resume=state), config)` — graph continues from `human_approval` to `send_email`

#### Retrying a Failed Run — `POST /runs/{run_id}/retry`

1. Load last checkpoint — graph knows exactly which node failed
2. Update `inject_failure: false` in state (or caller provides a corrected payload)
3. Call `graph.ainvoke(None, config)` — LangGraph replays from the last successful checkpoint

#### Cancelling a Run — `POST /runs/{run_id}/cancel`

1. Update `runs` table → `status = "cancelled"`
2. No further `graph.ainvoke` calls — the thread is abandoned
3. `GET /runs/{run_id}` returns `status: "cancelled"` from checkpoint metadata

---

### 4. Node Implementations

#### Node Contract

Every node function follows this contract:

```python
async def step_name(state: AgentState) -> dict:
    step_entry = {
        "step": "step_name",
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
    }
    try:
        # ... do work ...
        step_entry["status"] = "completed"
        step_entry["ended_at"] = datetime.utcnow().isoformat()
        step_entry["evidence"] = { ... }  # key outputs
        return {
            "artifacts": { ... },          # new artifact keys
            "step_log": state["step_log"] + [step_entry],
        }
    except Exception as e:
        step_entry["status"] = "failed"
        step_entry["error"] = str(e)
        return {
            "status": "failed",
            "errors": state["errors"] + [{"step": "step_name", "error": str(e), "timestamp": ...}],
            "step_log": state["step_log"] + [step_entry],
        }
```

#### Step 1 — Triage Node (LLM)

Uses a structured output call with Gemini to return a Pydantic model:

```python
class TriageOutput(BaseModel):
    category: Literal["Billing", "Tech"]
    urgency: Literal["High", "Low"]
    reasoning: str
```

If `inject_failure` is `True`, raises `InjectedFailureError` to simulate a recoverable crash.

#### Step 2 — Conditional Edge (Router)

```python
def route_to_specialist(state: AgentState) -> str:
    category = state["artifacts"].get("category")
    if category == "Billing":
        return "billing_lookup"
    elif category == "Tech":
        return "doc_search"
    else:
        return "error"   # Routes to END
```

#### Step 2a — Billing API (Mock Tool)

```python
async def billing_lookup_step(state: AgentState) -> dict:
    # Mock: returns deterministic data based on customer_id
    billing_data = {
        "customer_id": state["customer_id"],
        "invoice_id": "INV-882",
        "status": "overdue",
        "amount_due": "$49.99",
        "last_payment": "2026-08-15"
    }
    ...
```

#### Step 2b — Doc Search (Mock Vector Search)

```python
async def doc_search_step(state: AgentState) -> dict:
    # Mock: returns 3 fixed snippets relevant to common Tech issues
    docs = [
        {"title": "Reset 2FA", "snippet": "Go to Settings > Security > ..."},
        {"title": "API Rate Limits", "snippet": "Free tier allows 100 req/min..."},
        {"title": "Webhook Setup", "snippet": "Configure endpoint at dashboard..."},
    ]
    ...
```

#### Step 4 — Human Approval Node

This node is listed in `interrupt_before`, so LangGraph **never actually executes the node body**. It intercepts before entry. The node body runs only when resumed via the API:

```python
async def human_approval_step(state: AgentState) -> dict:
    # This body runs AFTER /approve is called
    approval = state["artifacts"].get("approval_status")
    if approval != "approved":
        raise ValueError(f"Run rejected by reviewer: {state['artifacts'].get('reviewer_note')}")
    return {
        "step_log": state["step_log"] + [{
            "step": "human_approval",
            "status": "approved",
            ...
        }]
    }
```

#### Step 5 — Send Email (Idempotent)

```python
async def send_email_step(state: AgentState) -> dict:
    # Idempotency guard
    if state["artifacts"].get("email_sent") is True:
        return {
            "step_log": state["step_log"] + [{
                "step": "send_email",
                "status": "skipped",
                "evidence": {"reason": "email already sent — idempotency check passed"}
            }]
        }

    # Mock send
    print(f"[MOCK EMAIL] Sending to {state['customer_email']}...")
    sent_at = datetime.utcnow().isoformat()

    return {
        "artifacts": {
            **state["artifacts"],
            "email_sent": True,
            "email_sent_at": sent_at,
        },
        "status": "completed",
        "step_log": state["step_log"] + [{
            "step": "send_email",
            "status": "completed",
            "ended_at": sent_at,
            "evidence": {"recipient": state["customer_email"], "sent_at": sent_at}
        }]
    }
```

---

### 5. Checkpointing & Resumability

#### How It Works

```
Node executes → returns partial state dict
      │
      ▼
LangGraph merges update into full AgentState
      │
      ▼
PostgresSaver serializes state → INSERT INTO checkpoints
      │
      ▼
Next node executes (or graph pauses/ends)
```

If the process crashes mid-node (before the node returns), LangGraph uses `checkpoint_writes` to detect incomplete nodes and replays them from the last **completed** checkpoint.

#### Resume Flow

```
POST /runs/{run_id}/retry
      │
      ▼
graph.aget_state(config)   →  loads latest checkpoint
      │
      ▼
graph.ainvoke(None, config) →  continues from last completed node
      │
      ▼
Failed node re-executes (with corrected state or without inject_failure)
```

The config `thread_id` is the run's UUID. LangGraph uses it to look up the correct checkpoint lineage.

---

### 6. Failure & Error Handling

#### Failure Taxonomy

| Failure Type | How Handled |
|---|---|
| LLM transient error (timeout, rate limit) | Caught in node, written to `errors[]`, status → `failed`, retryable |
| Injected failure (debug mode) | Same as above — `inject_failure=True` triggers `InjectedFailureError` |
| Human rejection (Step 4) | `approval_status = "rejected"`, graph ends at `human_approval`, status → `rejected` |
| Unrecoverable logic error | Written to `errors[]`, status → `failed`, no further nodes |
| Cancellation | External API call → marks run cancelled, no graph continuation |

#### `errors` list format

```json
[
  {
    "step": "specialist_routing",
    "error": "Injected failure at Step 2 for testing",
    "timestamp": "2026-10-05T10:00:03.412Z"
  }
]
```

---

### 7. Idempotency Strategy

Idempotency in this system is handled at two levels:

**Level 1 — State flag guard (Step 5)**

Before executing any side effect (email send), the node checks `state["artifacts"]["email_sent"]`. If `True`, the node returns a "skipped" log entry and exits. This makes the node safe to call any number of times.

**Level 2 — LangGraph checkpoint replay**

LangGraph's checkpointer ensures that if a node completed successfully, re-invoking the graph will not re-run that node. The graph resumes **after** the last completed checkpoint. This makes Steps 1–4 inherently idempotent at the orchestration level — they simply won't re-execute unless the checkpoint is explicitly invalidated.

---

### 8. Sequence Diagrams

#### Normal Run

```
Client          FastAPI         LangGraph         PostgreSQL         Gemini
  │                │                │                  │                │
  │  POST /runs    │                │                  │                │
  │───────────────>│                │                  │                │
  │                │  ainvoke()     │                  │                │
  │                │───────────────>│                  │                │
  │                │                │── triage ────────────────────────>│
  │                │                │<─ {category,urgency} ─────────────│
  │                │                │── save checkpoint ──>│            │
  │                │                │── billing_lookup (mock)           │
  │                │                │── save checkpoint ──>│            │
  │                │                │── draft_reply ───────────────────>│
  │                │                │<─ {draft} ────────────────────────│
  │                │                │── save checkpoint ──>│            │
  │                │                │── INTERRUPT (human_approval)      │
  │<── 201 run_id ─│                │                  │                │
  │                │                │                  │                │
  │  POST /approve │                │                  │                │
  │───────────────>│                │                  │                │
  │                │  ainvoke(      │                  │                │
  │                │  Command(      │                  │                │
  │                │  resume))      │                  │                │
  │                │───────────────>│                  │                │
  │                │                │── human_approval (runs body)      │
  │                │                │── send_email (mock)               │
  │                │                │── save checkpoint ──>│            │
  │<── 200 ────────│                │                  │                │
```

#### Failure & Retry

```
Client          FastAPI         LangGraph         PostgreSQL
  │                │                │                  │
  │  POST /runs    │                │                  │
  │  inject_failure│                │                  │
  │───────────────>│                │                  │
  │                │  ainvoke()     │                  │
  │                │───────────────>│                  │
  │                │                │── triage ✓ ──────────────────────>
  │                │                │── save checkpoint ──>│
  │                │                │── billing_lookup → EXCEPTION
  │                │                │── write error to state
  │                │                │── save checkpoint (status=failed) ─>│
  │<── 201 run_id ─│                │                  │
  │                │                │                  │
  │  POST /retry   │                │                  │
  │───────────────>│                │                  │
  │                │  aget_state()  │                  │
  │                │  ainvoke()     │                  │
  │                │───────────────>│                  │
  │                │                │  load checkpoint (triage completed)
  │                │                │── billing_lookup ✓ (no inject)
  │                │                │  ... continues to completion
  │<── 200 ────────│                │                  │
```

---

### 9. Database Schema (Full)

#### LangGraph-managed tables (auto-created by `checkpointer.setup()`)

```sql
-- Stores serialized AgentState snapshots
CREATE TABLE checkpoints (
    thread_id       TEXT    NOT NULL,
    checkpoint_ns   TEXT    NOT NULL DEFAULT '',
    checkpoint_id   TEXT    NOT NULL,
    parent_id       TEXT,
    type            TEXT,
    checkpoint      JSONB   NOT NULL,
    metadata        JSONB   NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- Pending writes for crash recovery
CREATE TABLE checkpoint_writes (
    thread_id       TEXT    NOT NULL,
    checkpoint_ns   TEXT    NOT NULL DEFAULT '',
    checkpoint_id   TEXT    NOT NULL,
    task_id         TEXT    NOT NULL,
    idx             INTEGER NOT NULL,
    channel         TEXT    NOT NULL,
    type            TEXT,
    value           JSONB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
```

#### Application-managed table (SQLAlchemy)

```sql
CREATE TABLE runs (
    run_id       VARCHAR PRIMARY KEY,
    status       VARCHAR NOT NULL,          -- running|paused|failed|completed|cancelled|rejected
    category     VARCHAR,
    urgency      VARCHAR,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP,
    completed_at TIMESTAMP
);
```

---

### 10. Configuration (`config.py`)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str          # postgresql+asyncpg://...
    PSYCOPG_URL: str           # postgresql+psycopg://... (for AsyncPostgresSaver)
    GOOGLE_API_KEY: str
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"

settings = Settings()
```

Two DB URLs are needed because:
- `asyncpg` driver → used by SQLAlchemy ORM (faster, fully async)
- `psycopg` driver → used by `AsyncPostgresSaver` (LangGraph's checkpointer requirement)

Both point to the same PostgreSQL database.

---

### 11. Key Design Decisions & Trade-offs

#### Decision 1: LangGraph `interrupt_before` vs. a manual pause flag

**Chosen:** `interrupt_before=["human_approval"]`

LangGraph's interrupt mechanism is atomic with the checkpointing system. When the graph pauses, the checkpoint is written with all prior state. A manual flag approach would require careful coordination to avoid race conditions between the flag write and the next node starting.

#### Decision 2: Separate `runs` table vs. querying checkpoints only

**Chosen:** Maintain both

Querying checkpoints requires deserializing JSON blobs for every row — expensive for a list endpoint. The `runs` table is a lightweight index that makes `GET /runs` an O(1) query per run.

#### Decision 3: Mock tools vs. real integrations

**Chosen:** Mock with realistic structure

The mock tools (`billing_api.py`, `doc_search.py`) return plausible data structures that real APIs would return. Swapping them for real implementations requires only changing the tool module — the node and state logic are unchanged.

#### Decision 4: `inject_failure` in state vs. out-of-band

**Chosen:** In state as a field on `AgentState`

This means the failure flag is persisted in the checkpoint, making the failure scenario fully reproducible and resumable. Out-of-band flags (env vars, headers) would be lost after the process restarts.

---

### 12. Non-Functional Properties

| Property | Implementation |
|---|---|
| **Observability** | Every node writes to `step_log` with timestamps. `GET /runs/{id}` exposes the full trace. |
| **Durability** | PostgreSQL checkpoint after every node. Crash between nodes → replay from last good checkpoint. |
| **Idempotency** | State-flag guard in Step 5 + LangGraph checkpoint replay prevents double execution. |
| **Auditability** | `errors[]` captures every failure with step name, message, and timestamp. |
| **Extensibility** | Adding a new step = add a node function + wire an edge. State schema handles it via `artifacts` dict. |
| **Testability** | Mock tools are pure functions. LangGraph graph can be tested in-process with `MemorySaver`. |
