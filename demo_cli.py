"""
demo_cli.py
Interactive CLI demo for the Customer Support Triage Orchestrator.

Features:
  - Start new workflow runs (normal or with injected failure)
  - Live progress display as each step completes
  - Shows LLM output summary (category, urgency, sentiment, draft reply)
  - Waits for your Approve / Reject decision on paused runs
  - Cancel a run mid-flight and resume from checkpoint
  - List all runs with status

Usage:
    py demo_cli.py

Requirements: API server must be running first:
    py -m uvicorn app.main:app --reload
"""

import asyncio
import json
import sys
import time
from typing import Any, Optional

import httpx
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()
BASE_URL = "http://localhost:8000"
CLIENT_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
async def api_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
        r = await client.post(f"{BASE_URL}{path}", json=body or {})
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text}


async def api_get(path: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
        r = await client.get(f"{BASE_URL}{path}")
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text}


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
STATUS_COLOURS = {
    "running":   "cyan",
    "paused":    "yellow",
    "completed": "green",
    "failed":    "red",
    "cancelled": "dark_orange",
    "rejected":  "magenta",
    "pending":   "dim yellow",
    "approved":  "green",
    "skipped":   "blue",
}

URGENCY_COLOURS = {"High": "bold red", "Low": "green"}


def coloured_status(s: str) -> Text:
    return Text(s, style=STATUS_COLOURS.get(s, "white"))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def show_step_log(step_log: list[dict]) -> None:
    if not step_log:
        return
    table = Table(
        title="Step-by-Step Trace",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Step", style="cyan", no_wrap=True, min_width=18)
    table.add_column("Status", min_width=10)
    table.add_column("Duration", justify="right", min_width=10)
    table.add_column("Evidence", overflow="fold")

    for entry in step_log:
        st = entry.get("status", "")
        ev = entry.get("evidence", {})
        ev_str = "  ".join(f"[dim]{k}:[/dim] {v}" for k, v in list(ev.items())[:4])
        dur = entry.get("duration_ms", "")
        dur_str = f"{dur} ms" if isinstance(dur, int) and dur > 0 else "—"
        table.add_row(
            entry.get("step", ""),
            Text(st, style=STATUS_COLOURS.get(st, "white")),
            dur_str,
            ev_str[:120],
        )
    console.print(table)


def show_llm_summary(artifacts: dict) -> None:
    """Show a clean, readable summary of what the LLM returned."""
    if not artifacts:
        return

    console.print()
    console.print(Rule("[bold yellow] LLM / Pipeline Output Summary [/bold yellow]", style="yellow"))

    # ---- Triage output -------------------------------------------------
    cat   = artifacts.get("category", "—")
    urg   = artifacts.get("urgency", "—")
    sent  = artifacts.get("customer_sentiment", "—")
    reason = artifacts.get("triage_reasoning", "")
    thinking = artifacts.get("triage_thinking", "")

    triage_lines = [
        f"  [bold]Category  :[/bold] [{URGENCY_COLOURS.get(cat, 'white')}]{cat}[/{URGENCY_COLOURS.get(cat, 'white')}]",
        f"  [bold]Urgency   :[/bold] [{URGENCY_COLOURS.get(urg, 'white')}]{urg}[/{URGENCY_COLOURS.get(urg, 'white')}]",
        f"  [bold]Sentiment :[/bold] {sent}",
    ]
    if reason:
        triage_lines.append(f"  [bold]Reasoning :[/bold] [dim]{reason[:180]}[/dim]")
    if thinking:
        triage_lines.append(f"  [bold]Thinking  :[/bold] [dim italic]{thinking[:300]}...[/dim italic]")
    console.print(Panel("\n".join(triage_lines), title="[bold]Step 1 — Triage (LLM)[/bold]", border_style="cyan"))

    # ---- Specialist data -----------------------------------------------
    billing = artifacts.get("billing_info")
    docs    = artifacts.get("relevant_docs")

    if billing:
        lines = [
            f"  [bold]Invoice ID   :[/bold] {billing.get('invoice_id', '—')}",
            f"  [bold]Plan         :[/bold] {billing.get('plan', '—')}",
            f"  [bold]Status       :[/bold] {billing.get('invoice_status', '—')}",
            f"  [bold]Amount Due   :[/bold] {billing.get('amount_due', '—')}",
            f"  [bold]Outstanding  :[/bold] {billing.get('outstanding_balance', '—')}",
            f"  [bold]Last Payment :[/bold] {billing.get('last_payment_date', '—')} "
                                           f"({billing.get('last_payment_amount', '—')})",
            f"  [bold]Suspended    :[/bold] {'[red]YES[/red]' if billing.get('account_suspended') else '[green]No[/green]'}",
            f"  [bold]Notes        :[/bold] [dim]{billing.get('notes', '')}[/dim]",
        ]
        console.print(Panel("\n".join(lines), title="[bold]Step 2a — Billing API Data[/bold]", border_style="blue"))

    if docs:
        lines = []
        for i, d in enumerate(docs[:3], 1):
            score = d.get("relevance_score", 0)
            lines.append(
                f"  [bold]{i}.[/bold] [cyan]{d.get('title', '')}[/cyan]  "
                f"[dim](score: {score:.2f})[/dim]"
            )
            snippet = d.get("snippet", "")[:200]
            lines.append(f"     [dim]{snippet}...[/dim]")
            lines.append(f"     [link]{d.get('url', '')}[/link]")
            lines.append("")
        console.print(Panel("\n".join(lines), title="[bold]Step 2b — Doc Search Results[/bold]", border_style="blue"))

    # ---- Draft reply ---------------------------------------------------
    draft = artifacts.get("draft_reply")
    draft_thinking = artifacts.get("draft_thinking", "")
    if draft:
        wc = artifacts.get("draft_word_count", len(draft.split()))
        panels = []
        if draft_thinking:
            console.print(Panel(
                f"[dim italic]{draft_thinking[:500]}{'...' if len(draft_thinking) > 500 else ''}[/dim italic]",
                title="[bold dim]LLM Thinking Trace (internal reasoning)[/bold dim]",
                border_style="dim",
                padding=(0, 2),
            ))
        console.print(Panel(
            draft,
            title=f"[bold yellow]Step 3 — LLM Draft Reply  [dim]({wc} words)[/dim][/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ))

    # ---- Approval info -------------------------------------------------
    approval = artifacts.get("approval_status")
    if approval:
        colour = "green" if approval == "approved" else "red"
        note = artifacts.get("reviewer_note", "")
        by   = artifacts.get("reviewed_by", "")
        console.print(Panel(
            f"  [{colour}]{approval.upper()}[/{colour}]  by [bold]{by}[/bold]\n"
            f"  Note: [dim]{note}[/dim]",
            title="[bold]Step 4 — Human Approval[/bold]",
            border_style=colour,
        ))

    # ---- Email outcome -------------------------------------------------
    if artifacts.get("email_sent"):
        console.print(Panel(
            f"  [green]✓ Email dispatched[/green]\n"
            f"  To      : {artifacts.get('email_recipient', '—')}\n"
            f"  Subject : {artifacts.get('email_subject', '—')}\n"
            f"  Sent at : {artifacts.get('email_sent_at', '—')}",
            title="[bold]Step 5 — Email Sent[/bold]",
            border_style="green",
        ))
    console.print()


# ---------------------------------------------------------------------------
# Poll run until it leaves "running" state
# ---------------------------------------------------------------------------
async def poll_until_stable(run_id: str, poll_interval: float = 1.5) -> dict:
    """
    Polls GET /runs/{run_id} until status is no longer 'running'.
    Shows a spinner while waiting.
    """
    spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    while True:
        code, data = await api_get(f"/runs/{run_id}")
        if code != 200:
            break
        st = data.get("status", "running")
        if st != "running":
            return data
        # Still running — show spinner
        console.print(
            f"\r  {spinner_chars[i % len(spinner_chars)]} Processing steps...",
            end="",
            highlight=False,
        )
        i += 1
        await asyncio.sleep(poll_interval)
    return data


# ---------------------------------------------------------------------------
# Core: run a new workflow
# ---------------------------------------------------------------------------
async def run_workflow(inject_failure: bool = False) -> Optional[str]:
    console.print()
    console.print(Rule("[bold cyan] New Workflow Run [/bold cyan]"))
    console.print()

    # Collect ticket info
    ticket = Prompt.ask(
        "[bold]Enter customer ticket text[/bold]",
        default=(
            "I was charged $49.99 twice this month for my Pro subscription. "
            "My bank shows two identical transactions on Oct 1st. "
            "Please refund the duplicate. Customer ID: CUST-4421."
        ),
    )
    email = Prompt.ask("[bold]Customer email[/bold]", default="demo@customer.com")
    cid   = Prompt.ask("[bold]Customer ID[/bold]", default="CUST-4421")

    if inject_failure:
        console.print("\n  [bold red]⚡ inject_failure=True — Step 2 will raise a recoverable error[/bold red]")

    console.print()
    console.print("[dim]Starting workflow...[/dim]")

    code, resp = await api_post("/runs", {
        "ticket_text": ticket,
        "customer_email": email,
        "customer_id": cid,
        "inject_failure": inject_failure,
    })

    if code not in (200, 201):
        console.print(f"[red]Failed to create run: {resp}[/red]")
        return None

    run_id = resp.get("run_id")
    console.print(f"\n  [bold]Run ID :[/bold] [cyan]{run_id}[/cyan]")
    console.print(f"  [bold]Status :[/bold] {coloured_status(resp.get('status', '?'))}")
    console.print()

    # Poll until stable
    state = await poll_until_stable(run_id)
    console.print()  # clear spinner line

    current_status = state.get("status", "unknown")
    console.print(f"  [bold]Final status :[/bold] {coloured_status(current_status)}")
    console.print()

    # Show full LLM output summary
    show_llm_summary(state.get("artifacts", {}))

    # Show step trace
    show_step_log(state.get("step_log", []))

    # Show errors if any
    if state.get("errors"):
        console.print(Panel(
            "\n".join(
                f"  [red]● {e.get('step')}[/red] — {e.get('error', '')[:200]}"
                for e in state["errors"]
            ),
            title="[bold red]Errors[/bold red]",
            border_style="red",
        ))

    # Handle states
    if current_status == "paused":
        await _handle_approval(run_id, state)
    elif current_status == "failed":
        await _handle_failure(run_id)
    elif current_status == "completed":
        console.print(Panel(
            "[green]✓ Workflow completed successfully.[/green]\n"
            "Email has been dispatched to the customer.",
            border_style="green",
        ))

    return run_id


# ---------------------------------------------------------------------------
# Handle human approval
# ---------------------------------------------------------------------------
async def _handle_approval(run_id: str, state: dict) -> None:
    console.print()
    console.print(Panel(
        "[yellow]⏸  Workflow paused — awaiting your approval.[/yellow]\n\n"
        "Review the [bold]LLM Draft Reply[/bold] above.\n"
        "  [green][A][/green] Approve → email will be sent to the customer\n"
        "  [red][R][/red]  Reject  → email blocked, run marked rejected\n"
        "  [dark_orange][C][/dark_orange] Cancel  → run cancelled entirely",
        title="[bold yellow]Human Approval Required[/bold yellow]",
        border_style="yellow",
    ))

    choice = Prompt.ask(
        "\n[bold]Your decision[/bold]",
        choices=["A", "R", "C", "a", "r", "c"],
        default="A",
    ).upper()

    if choice == "A":
        note = Prompt.ask(
            "[bold]Reviewer note (optional)[/bold]",
            default="Looks good — approved.",
        )
        reviewer = Prompt.ask("[bold]Your name/ID[/bold]", default="demo_agent")
        console.print("\n  [dim]Approving and resuming workflow...[/dim]")
        code, resp = await api_post(f"/runs/{run_id}/approve", {
            "reviewer_note": note,
            "reviewed_by": reviewer,
        })
        if code == 200:
            console.print()
            console.print(Panel(
                f"[green]✓ Approved by {reviewer}[/green]\n"
                f"Email sent: {resp.get('email_sent')}\n"
                f"Sent at: {resp.get('email_sent_at', '—')}",
                title="[bold green]Approval Accepted — Run Completed[/bold green]",
                border_style="green",
            ))
            # Show final state
            _, final = await api_get(f"/runs/{run_id}")
            show_step_log(final.get("step_log", []))
        else:
            console.print(f"[red]Approval failed ({code}): {resp.get('detail', resp)}[/red]")

    elif choice == "R":
        note = Prompt.ask(
            "[bold]Rejection reason[/bold] (required)",
            default="Draft needs revision before sending.",
        )
        reviewer = Prompt.ask("[bold]Your name/ID[/bold]", default="demo_agent")
        console.print("\n  [dim]Rejecting run...[/dim]")
        code, resp = await api_post(f"/runs/{run_id}/reject", {
            "reviewer_note": note,
            "reviewed_by": reviewer,
        })
        if code == 200:
            console.print(Panel(
                f"[magenta]✗ Rejected by {reviewer}[/magenta]\n"
                f"Reason: {note}\n"
                f"Email NOT sent.",
                title="[bold magenta]Run Rejected[/bold magenta]",
                border_style="magenta",
            ))
        else:
            console.print(f"[red]Rejection failed ({code}): {resp.get('detail', resp)}[/red]")

    else:  # Cancel
        console.print("\n  [dim]Cancelling run...[/dim]")
        code, resp = await api_post(f"/runs/{run_id}/cancel")
        if code == 200:
            console.print(Panel(
                f"[dark_orange]⊘ Run cancelled.[/dark_orange]\n"
                f"Cancelled at: {resp.get('cancelled_at', '—')}\n"
                f"No email sent. Run can NOT be resumed from this state.",
                title="[bold dark_orange]Run Cancelled[/bold dark_orange]",
                border_style="dark_orange",
            ))
        else:
            console.print(f"[red]Cancel failed ({code}): {resp.get('detail', resp)}[/red]")


# ---------------------------------------------------------------------------
# Handle failure — offer retry
# ---------------------------------------------------------------------------
async def _handle_failure(run_id: str) -> None:
    console.print()
    console.print(Panel(
        "[red]✗ Workflow stopped at a failed step.[/red]\n\n"
        "LangGraph has preserved the checkpoint — completed steps will NOT re-run.\n"
        "  [cyan][Y][/cyan] Retry from last checkpoint\n"
        "  [dark_orange][C][/dark_orange] Cancel this run",
        title="[bold red]Run Failed[/bold red]",
        border_style="red",
    ))

    choice = Prompt.ask(
        "\n[bold]Retry?[/bold]",
        choices=["Y", "N", "C", "y", "n", "c"],
        default="Y",
    ).upper()

    if choice in ("Y",):
        console.print("\n  [dim]Retrying from last checkpoint...[/dim]")
        code, resp = await api_post(f"/runs/{run_id}/retry", {"clear_failure": True})
        if code == 200:
            console.print(f"\n  Retry status: {coloured_status(resp.get('status', '?'))}")
            console.print(f"  Retry count : {resp.get('retry_count', '?')}")

            # Re-fetch state to show updated trace
            state = await poll_until_stable(run_id)
            console.print()
            show_llm_summary(state.get("artifacts", {}))
            show_step_log(state.get("step_log", []))

            if state.get("status") == "paused":
                await _handle_approval(run_id, state)
            elif state.get("status") == "completed":
                console.print(Panel(
                    "[green]✓ Run completed after retry.[/green]",
                    border_style="green",
                ))
        else:
            console.print(f"[red]Retry failed ({code}): {resp.get('detail', resp)}[/red]")
    else:
        code, _ = await api_post(f"/runs/{run_id}/cancel")
        if code == 200:
            console.print("[dark_orange]Run cancelled.[/dark_orange]")


# ---------------------------------------------------------------------------
# List all runs
# ---------------------------------------------------------------------------
async def list_runs(status_filter: str = "") -> None:
    console.print()
    console.print(Rule("[bold cyan] All Workflow Runs [/bold cyan]"))

    params = f"?limit=50"
    if status_filter:
        params += f"&status={status_filter}"

    code, data = await api_get(f"/runs{params}")
    if code != 200:
        console.print(f"[red]Failed to list runs: {data}[/red]")
        return

    runs = data.get("runs", [])
    total = data.get("total", 0)

    if not runs:
        console.print(f"[dim]No runs found{' with status=' + status_filter if status_filter else ''}.[/dim]")
        return

    table = Table(
        title=f"Runs ({total} total{', filter: ' + status_filter if status_filter else ''})",
        box=box.ROUNDED,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Run ID", style="dim", min_width=36, no_wrap=True)
    table.add_column("Status", min_width=10)
    table.add_column("Category", min_width=8)
    table.add_column("Urgency", min_width=6)
    table.add_column("Email", overflow="fold")
    table.add_column("Retries", justify="right", min_width=7)
    table.add_column("Created", min_width=20)

    for r in runs:
        st = r.get("status", "")
        urg = r.get("urgency") or "—"
        created = r.get("created_at", "—")
        if created and "T" in created:
            created = created[:19].replace("T", " ")
        table.add_row(
            r.get("run_id", ""),
            coloured_status(st),
            r.get("category") or "—",
            Text(urg, style=URGENCY_COLOURS.get(urg, "white")),
            r.get("customer_email", ""),
            str(r.get("retry_count", 0)),
            created,
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Inspect a specific run
# ---------------------------------------------------------------------------
async def inspect_run(run_id: str = "") -> None:
    if not run_id:
        run_id = Prompt.ask("[bold]Enter Run ID to inspect[/bold]").strip()

    code, data = await api_get(f"/runs/{run_id}")
    if code != 200:
        console.print(f"[red]Run not found ({code}): {data.get('detail', data)}[/red]")
        return

    console.print()
    console.print(Rule(f"[bold cyan] Run Detail: {run_id[:8]}... [/bold cyan]"))

    st = data.get("status", "")
    console.print(Panel(
        f"  [bold]Run ID  :[/bold] {data.get('run_id')}\n"
        f"  [bold]Status  :[/bold] {coloured_status(st)}\n"
        f"  [bold]Category:[/bold] {data.get('artifacts', {}).get('category', '—')}\n"
        f"  [bold]Urgency :[/bold] {data.get('artifacts', {}).get('urgency', '—')}\n"
        f"  [bold]Retries :[/bold] {data.get('retry_count', 0)}\n"
        f"  [bold]Ticket  :[/bold] [dim]{data.get('input_data', '')[:120]}...[/dim]",
        border_style=STATUS_COLOURS.get(st, "white"),
    ))

    show_llm_summary(data.get("artifacts", {}))
    show_step_log(data.get("step_log", []))

    if data.get("errors"):
        console.print(Panel(
            "\n".join(
                f"  [red]● {e.get('step')}[/red] — {e.get('error', '')[:200]}"
                for e in data["errors"]
            ),
            title="[bold red]Errors[/bold red]",
            border_style="red",
        ))

    # Offer actions based on status
    if st in ("paused", "running"):
        artifacts = data.get("artifacts", {})
        if artifacts.get("draft_reply"):
            if Confirm.ask("\n[yellow]This run is paused. Take action now?[/yellow]"):
                await _handle_approval(run_id, data)
    elif st == "failed":
        if Confirm.ask("\n[red]This run failed. Retry from checkpoint?[/red]"):
            await _handle_failure(run_id)


# ---------------------------------------------------------------------------
# Paused runs dashboard — show all pending approvals
# ---------------------------------------------------------------------------
async def pending_approvals() -> None:
    console.print()
    console.print(Rule("[bold yellow] Pending Approvals Dashboard [/bold yellow]"))
    console.print()

    code, data = await api_get("/runs?status=paused&limit=50")
    if code != 200:
        console.print(f"[red]Error: {data}[/red]")
        return

    runs = data.get("runs", [])
    if not runs:
        code2, data2 = await api_get("/runs?status=running&limit=50")
        runs2 = [r for r in data2.get("runs", []) if r.get("status") == "running"]
        if not runs2:
            console.print("[green]✓ No runs pending approval right now.[/green]")
            return
        runs = runs2

    console.print(f"[yellow]{len(runs)} run(s) awaiting approval:[/yellow]\n")

    for i, r in enumerate(runs, 1):
        run_id = r.get("run_id", "")
        console.print(
            f"  [bold]{i}.[/bold] [cyan]{run_id}[/cyan]  "
            f"[dim]{r.get('customer_email')}[/dim]  "
            f"Category: [bold]{r.get('category', '?')}[/bold]  "
            f"Urgency: [bold]{r.get('urgency', '?')}[/bold]"
        )

    console.print()
    pick = Prompt.ask(
        "[bold]Enter number to review (or 0 to skip)[/bold]",
        default="0",
    )

    try:
        idx = int(pick) - 1
        if 0 <= idx < len(runs):
            await inspect_run(runs[idx]["run_id"])
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Health check banner
# ---------------------------------------------------------------------------
async def check_health() -> bool:
    try:
        code, data = await api_get("/health")
        if code != 200:
            raise ConnectionError(f"HTTP {code}")
        db_ok    = data.get("checks", {}).get("database") == "ok"
        graph_ok = data.get("checks", {}).get("graph") == "ok"
        model    = data.get("llm_model", "?")
        mock     = data.get("mock_llm", False)

        status_text = "[green]healthy[/green]" if (db_ok and graph_ok) else "[red]degraded[/red]"
        console.print(Panel(
            f"  API          : {status_text}\n"
            f"  Database     : {'[green]ok[/green]' if db_ok else '[red]error[/red]'}\n"
            f"  Graph engine : {'[green]ok[/green]' if graph_ok else '[red]not ready[/red]'}\n"
            f"  LLM model    : [cyan]{model}[/cyan]\n"
            f"  Mock LLM     : {'[yellow]YES (no real API calls)[/yellow]' if mock else '[green]No (real Nvidia Nemotron)[/green]'}",
            title="[bold]System Health[/bold]",
            border_style="green" if (db_ok and graph_ok) else "red",
        ))
        return db_ok and graph_ok
    except (httpx.ConnectError, ConnectionError):
        console.print(Panel(
            "[bold red]Cannot reach the API server.[/bold red]\n\n"
            "Start it first with:\n"
            "  [cyan]py -m uvicorn app.main:app --reload[/cyan]\n\n"
            "Then re-run this CLI.",
            title="[bold red]Connection Error[/bold red]",
            border_style="red",
        ))
        return False


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
def print_banner() -> None:
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Customer Support Triage Orchestrator[/bold cyan]\n"
        "[dim]Interactive Demo CLI[/dim]\n\n"
        "[white]Powered by:[/white] LangGraph · Nvidia Nemotron · FastAPI · PostgreSQL",
        border_style="cyan",
    ))
    console.print()


def print_menu() -> None:
    console.print(Panel(
        "  [bold cyan][1][/bold cyan]  Start a new workflow run (normal)\n"
        "  [bold red][2][/bold red]  Start a run WITH injected failure (test retry)\n"
        "  [bold yellow][3][/bold yellow]  View pending approvals  ⬅ runs waiting for YOU\n"
        "  [bold blue][4][/bold blue]  List all runs\n"
        "  [bold magenta][5][/bold magenta]  Inspect / act on a specific run\n"
        "  [bold green][6][/bold green]  Run automated evaluation (all 5 scenarios)\n"
        "  [bold dim][H][/bold dim]  Health check\n"
        "  [bold dim][Q][/bold dim]  Quit",
        title="[bold]Menu[/bold]",
        border_style="dim",
    ))


async def main() -> None:
    print_banner()

    # Pre-flight health check
    healthy = await check_health()
    if not healthy:
        sys.exit(1)

    while True:
        console.print()
        print_menu()
        choice = Prompt.ask("[bold]Choose[/bold]", default="3").strip().upper()
        console.print()

        if choice == "1":
            await run_workflow(inject_failure=False)

        elif choice == "2":
            await run_workflow(inject_failure=True)

        elif choice == "3":
            await pending_approvals()

        elif choice == "4":
            status_f = Prompt.ask(
                "[dim]Filter by status? (leave blank for all)[/dim]",
                default="",
            ).strip()
            await list_runs(status_f)

        elif choice == "5":
            await inspect_run()

        elif choice == "6":
            console.print("[dim]Launching evaluate.py...[/dim]")
            import subprocess
            subprocess.run(["py", "evaluate.py"], check=False)

        elif choice == "H":
            await check_health()

        elif choice in ("Q", "EXIT", "QUIT"):
            console.print("\n[dim]Goodbye.[/dim]\n")
            break

        else:
            console.print("[dim]Unknown option — please choose 1–6, H, or Q.[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
