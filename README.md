# Workflow-Orchestrator

A durable, resumable multi-step agent workflow orchestrator for customer support ticket triage.

Built with **LangGraph**, **FastAPI**, **PostgreSQL**, and **Nvidia Nemotron** (nemotron-3-ultra-550b-a55b).

## Quick Start

```bash
# 1. Copy env and fill in your Nvidia API key
copy .env.example .env

# 2. Install dependencies
py -m pip install -r requirements.txt

# 3. Initialise database
py -m app.db.init_db

# 4. Seed 50 demo records
py seed_data.py

# 5. Start API server
py -m uvicorn app.main:app --reload

# 6. Run interactive CLI demo
py demo_cli.py

# 7. Run automated evaluation
py evaluate.py
```

## Architecture

See [Architecture.md](Architecture.md) and [Project.md](Project.md) for full design documentation.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /runs | Start a new workflow run |
| GET | /runs | List all runs |
| GET | /runs/{run_id} | Get full run detail + step trace |
| POST | /runs/{run_id}/approve | Resume paused run (approved) |
| POST | /runs/{run_id}/reject | Resume paused run (rejected) |
| POST | /runs/{run_id}/retry | Retry a failed run from checkpoint |
| POST | /runs/{run_id}/cancel | Cancel a run |

Interactive docs: http://localhost:8000/docs
