"""Command line for Elix. Entry points `python -m elix` and `elix`.

    python -m elix demo                  seed, start vendor servers and serve the API and console on 8400
    python -m elix seed [--seed 7]       rebuild the Kestrel world with history July 1 to October 23
    python -m elix serve [--vendors]     serve an existing world
    python -m elix vendors               run the local x402 vendor servers in the foreground
    python -m elix scenario list | run <id>
    python -m elix eval [--seed 11]      run the eval and write eval/report.json and eval/report.md
    python -m elix why <subject>         print the audit trail for an item, entry, invoice number or policy
    python -m elix verify-chain          recompute the audit hash chain
    python -m elix agents run <agent> --task "..." [--scripted]
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from elix.engine.settings import REPO_ROOT, default_db_path, eval_dir


def load_dotenv() -> None:
    """Read KEY=VALUE lines from .env in the repo root or the current folder. Existing variables win."""
    for path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
        break


load_dotenv()

app = typer.Typer(help="Elix is accounts payable for x402. Kestrel Labs is fictional and its data is synthetic.",
                  no_args_is_help=True, add_completion=False)
scenario_app = typer.Typer(help="Demo moments from specs/03-world.md.", no_args_is_help=True)
agents_app = typer.Typer(help="Run Kestrel's agents against a running Elix API.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")
app.add_typer(agents_app, name="agents")
console = Console()

VENDOR_PORT = int(os.environ.get("ELIX_VENDOR_PORT", "8402"))


def _vendors(port: int = VENDOR_PORT) -> str:
    from elix import vendors

    base = vendors.ensure_running(port)
    os.environ.setdefault("ELIX_VENDOR_BASE_URL", base)
    return base


def _offline_registry(fast: bool, port: int = VENDOR_PORT):
    from elix.rails import make_fake_registry, make_registry

    if fast:
        return make_fake_registry()
    return make_registry("offline", _vendors(port))


def _seed(db: Path, seed: int, fast: bool, port: int = VENDOR_PORT) -> None:
    from elix.world.seed import seed_world

    registry = _offline_registry(fast, port)
    if not fast:
        from elix.rails import reset_offline_state

        reset_offline_state(registry.vendor_base_url)
    console.print(f"Seeding Kestrel Labs into {db} with seed {seed}"
                  f"{' on fake rails' if fast else ' through the offline x402 rail'}.")
    result = seed_world(db, registry, seed=seed, progress=lambda m: console.print(f"  {m}", style="dim"))
    elix = result.elix
    check = elix.verify_chain()
    inbox = {a: len(elix.inbox(a)) for a in ("human:priya", "human:dev", "human:maya", "human:sam")}
    console.print(f"Seeded {sum(result.counts.values())} events in {result.seconds:.1f} seconds. "
                  f"Audit chain {'verified' if check.ok else 'BROKEN'} ({check.checked} events).")
    console.print("Periods " + ", ".join(f"{p.id} {p.status}" for p in elix.periods()) + ".")
    console.print("Open inbox items " + ", ".join(f"{elix.config.actor_name(a)} {n}" for a, n in inbox.items()) + ".")
    elix.close()


def _open(fast: bool = False):
    from elix.engine import Elix

    db = default_db_path()
    if not db.exists():
        console.print("No world yet. Run python -m elix seed first.", style="red")
        raise typer.Exit(1)
    if fast:
        from elix.rails import make_fake_registry

        return Elix(db, make_fake_registry())
    from elix.rails import make_registry

    base = _vendors()
    _restore_payouts(db, base)
    return Elix(db, make_registry(None, base))


def _restore_payouts(db: Path, base: str) -> None:
    """Kept worlds may have rotated payees. Point the in memory vendor servers at the vendor master again."""
    if os.environ.get("ELIX_RAIL_MODE", "offline") != "offline":
        return
    from elix.world.seed import restore_vendor_payouts

    changed = restore_vendor_payouts(db, base)
    if changed:
        console.print(f"Vendor servers now ask for the current payout address of {', '.join(changed)}.", style="dim")


@app.command()
def seed(db: Optional[Path] = typer.Option(None, help="Database path. Defaults to ELIX_DATA_DIR/elix.db."),
         seed: int = typer.Option(7, help="Random seed for the history."),
         fast: bool = typer.Option(False, help="Use in-memory fake rails instead of the offline x402 rail.")) -> None:
    """Rebuild the Kestrel Labs world with history from July 1 to October 23."""
    _seed(db or default_db_path(), seed, fast)


@app.command()
def serve(port: int = typer.Option(8400), host: str = typer.Option("127.0.0.1"),
          vendors: bool = typer.Option(False, "--vendors", help="Also start the local x402 vendor servers."),
          db: Optional[Path] = typer.Option(None)) -> None:
    """Serve the API and console for an existing world (seeds one first if none exists)."""
    import uvicorn

    from elix.rails import make_registry
    from elix.server import create_app

    path = db or default_db_path()
    base = _vendors() if (vendors or not path.exists()) else None
    if not path.exists():
        _seed(path, 7, fast=False)
    elif base:
        _restore_payouts(path, base)
    app_ = create_app(path, make_registry(None, base))
    _print_urls(host, port, base)
    uvicorn.run(app_, host=host, port=port, log_level="warning")


@app.command("vendors")
def vendors_cmd(port: int = typer.Option(VENDOR_PORT), host: str = typer.Option("127.0.0.1")) -> None:
    """Run the local x402 vendor servers and facilitator in the foreground."""
    from elix.vendors.__main__ import main

    main(["--port", str(port), "--host", host])


@app.command()
def demo(port: int = typer.Option(8400), host: str = typer.Option("127.0.0.1"),
         vendor_port: int = typer.Option(VENDOR_PORT),
         keep: bool = typer.Option(False, help="Keep the existing world instead of reseeding.")) -> None:
    """Seed the world, start the vendor servers, and serve the API and console. Works offline with no keys."""
    import uvicorn

    from elix.rails import make_registry
    from elix.server import create_app

    base = _vendors(vendor_port)
    path = default_db_path()
    if not (keep and path.exists()):
        _seed(path, 7, fast=False, port=vendor_port)
    else:
        _restore_payouts(path, base)
    mode = os.environ.get("ELIX_RAIL_MODE", "offline")
    app_ = create_app(path, make_registry(mode, base), api_base=f"http://{host}:{port}")
    _print_urls(host, port, base)
    uvicorn.run(app_, host=host, port=port, log_level="warning")


def _print_urls(host: str, port: int, vendor_base: Optional[str]) -> None:
    from elix.agents import runner

    console.print()
    console.print(f"Console     http://{host}:{port}/", style="bold")
    console.print(f"Site        http://{host}:{port}/site")
    console.print(f"API         http://{host}:{port}/api  (docs at http://{host}:{port}/docs)")
    if vendor_base:
        console.print(f"Vendors     {vendor_base}  (local x402 vendor servers, synthetic data)")
    console.print(f"Rail mode   {os.environ.get('ELIX_RAIL_MODE', 'offline')}.  Agents {runner.mode_for()}.")
    console.print("Tokens      tok_priya tok_dev tok_maya tok_sam tok_jordan, agents tok_atlas tok_forge tok_scout")
    console.print("Kestrel Labs is a fictional company. All data is synthetic.", style="dim")
    console.print()


@scenario_app.command("list")
def scenario_list(fast: bool = typer.Option(False, help="Use fake rails.")) -> None:
    """List the demo scenarios and whether each is ready to run."""
    from elix.scenarios import list_scenarios

    elix = _open(fast)
    table = Table("id", "title", "ready")
    for s in list_scenarios(elix):
        table.add_row(s["id"], s["title"], "yes" if s["ready"] else f"no. {s['not_ready_reason']}")
    console.print(table)
    elix.close()


@scenario_app.command("run")
def scenario_run(scenario_id: str, as_: str = typer.Option("human:priya", "--as", help="Who presses Run."),
                 params: Optional[str] = typer.Option(None, help="JSON parameters for the custom scenario."),
                 fast: bool = typer.Option(False, help="Use fake rails.")) -> None:
    """Run one demo scenario against the current world."""
    from elix.contracts import Denied
    from elix.engine.errors import Conflict, Invalid, NotFound
    from elix.scenarios import run_scenario

    elix = _open(fast)
    try:
        result = run_scenario(elix, scenario_id, as_, json.loads(params) if params else None)
    except Conflict as exc:
        console.print(exc.message, style="yellow")
        elix.close()
        raise typer.Exit(1)
    except (Invalid, NotFound, Denied) as exc:
        console.print(exc.message, style="red")
        elix.close()
        raise typer.Exit(1)
    console.print(result["message"])
    console.print(f"Items {', '.join(result['result_ids'])}.  Next {elix.config.actor_name(result['next_actor'])}."
                  if result.get("next_actor") else f"Items {', '.join(result['result_ids'])}.")
    elix.close()


def _decide(item_id: str, actor: str, note: str, approve: bool) -> None:
    from elix.contracts import Denied
    from elix.engine.errors import Conflict, NotFound

    actor = actor if ":" in actor else f"human:{actor}"
    elix = _open()
    try:
        item = elix.approve(actor, item_id, note) if approve else elix.reject(actor, item_id, note)
    except (Denied, Conflict, NotFound) as exc:
        console.print(exc.message, style="red")
        elix.close()
        raise typer.Exit(1)
    console.print(f"{item.id} is now {item.state.value}. {elix.work_item(item.id).explanation}")
    elix.close()


@app.command()
def approve(item_id: str, as_: str = typer.Option(..., "--as", help="The person approving, for example human:sam."),
            note: str = typer.Option("", help="Approval note.")) -> None:
    """Approve a work item as a person, the same as the console's approve button."""
    _decide(item_id, as_, note, True)


@app.command()
def reject(item_id: str, as_: str = typer.Option(..., "--as", help="The person rejecting."),
           note: str = typer.Option("", help="Rejection note.")) -> None:
    """Reject a work item as a person."""
    _decide(item_id, as_, note, False)


@app.command()
def intake(pdf: Path = typer.Argument(..., help="An invoice PDF, for example docs/samples/lumen-LUM-2026-10A.pdf."),
           as_: str = typer.Option("human:sam", "--as", help="The accountant or controller uploading it."),
           offline: bool = typer.Option(False, help="Use the offline parser even when ANTHROPIC_API_KEY is set.")) -> None:
    """Upload an invoice PDF as a person. Elix reads it, checks it, and reconciles it like a structured invoice."""
    from elix import intake as pdf_intake
    from elix.contracts import Denied

    actor = as_ if ":" in as_ else f"human:{as_}"
    if not pdf.is_file():
        console.print(f"There is no file at {pdf}.", style="red")
        raise typer.Exit(1)
    data = pdf.read_bytes()
    elix = _open()
    try:
        from elix.engine.permissions import can

        verdict = can(elix.config, actor, "intake_invoice")
        if not verdict.allowed:
            from elix.engine.workflow import deny

            raise deny(elix.ctx, actor=actor, action="intake_invoice", subject=pdf.name, reason=verdict.reason,
                       rule_id=verdict.rule_id)
        extraction = pdf_intake.extract(data, pdf.name, force_offline=offline)
        item, result = elix.intake_invoice(actor, extraction, filename=pdf.name, sha256=pdf_intake.sha256(data),
                                           size=len(data))
    except (pdf_intake.ParseError, Denied) as exc:
        console.print(getattr(exc, "message", str(exc)), style="red")
        elix.close()
        raise typer.Exit(1)
    console.print(f"{pdf.name} read by {extraction.extractor_label}.", style="bold")
    for note in extraction.notes:
        console.print(f"  {note}", style="dim")
    for check in result.checks:
        console.print(f"  {'pass' if check.passed else 'FAIL'}  {check.rule_id}  {check.name}. {check.detail}")
    console.print(f"{item.id} is now {item.state.value}. {elix.work_item(item.id).explanation}")
    elix.close()


@app.command("eval")
def eval_cmd(seed: int = typer.Option(11), out: Optional[Path] = typer.Option(None),
             fast: bool = typer.Option(False, help="Fake rails, for quick iteration. Never publish a fast report.")) -> None:
    """Run the eval with planted errors and an answer key. Writes eval/report.json and eval/report.md."""
    from elix.eval.run import run_eval

    base = None if fast else _vendors()
    started = time.perf_counter()
    report = run_eval(seed=seed, fast=fast, out=out, vendor_base_url=base,
                      progress=lambda status, f: console.print(f"  {status} {f * 100:.0f}%", style="dim")
                      if status == "running" and int(f * 100) % 20 == 0 else None)
    console.print(f"Eval seed {seed} in {time.perf_counter() - started:.1f} seconds, mode {report['mode']}.")
    console.print(f"Anomalies planted {report['anomalies_total']}, caught {report['anomalies_caught']}, "
                  f"paid without a person {report['anomalies_auto_paid']}.")
    console.print(f"Categories fully caught {report['categories_caught']} of {report['categories_total']}. "
                  f"Automation precision {report['automation_precision'] * 100:.1f}%.")
    console.print(f"Clean purchases automated {report['automation_rate_before_learning'] * 100:.1f}% before learning "
                  f"and {report['automation_rate_after_learning'] * 100:.1f}% after.")
    console.print(f"Audit chain {'verified' if report['chain_ok'] else 'BROKEN'}. "
                  f"Report at {(out or eval_dir() / 'report.json')}.")


@app.command()
def why(subject: str, fast: bool = typer.Option(True, hidden=True)) -> None:
    """Print the audit trail that explains a work item, journal entry, invoice number, policy or period."""
    from elix.engine.errors import NotFound

    elix = _open(fast=True)
    try:
        report = elix.why(subject)
    except NotFound as exc:
        console.print(exc.message, style="red")
        raise typer.Exit(1)
    console.print(report.title, style="bold")
    console.print(report.answer)
    console.print()
    for step in report.steps:
        rules = f" [{', '.join(step.rule_ids)}]" if step.rule_ids else ""
        console.print(f"{step.seq:>6}  {step.at}  {step.actor_name:<20} {step.sentence}{rules}")
    elix.close()


@app.command("verify-chain")
def verify_chain() -> None:
    """Recompute every hash in the audit log."""
    elix = _open(fast=True)
    check = elix.verify_chain()
    if check.ok:
        console.print(f"Audit chain ok. {check.checked} events verified. Head {check.head_hash}.")
    else:
        console.print(f"Audit chain BROKEN at event {check.first_bad_seq} after {check.checked} good events.",
                      style="red")
    elix.close()
    raise typer.Exit(0 if check.ok else 1)


@agents_app.command("run")
def agents_run(agent: str, task: str = typer.Option("", help="Task text. Defaults to the agent's demo task."),
               scripted: bool = typer.Option(False, help="Force the scripted agent even if a key is set."),
               api: str = typer.Option(os.environ.get("ELIX_API_URL", "http://127.0.0.1:8400"))) -> None:
    """Run an agent against a running Elix API and print its transcript."""
    from elix.agents import runner, sessions

    agent_id = agent if agent.startswith("agent:") else f"agent:{agent}"
    token = runner.AGENT_TOKENS.get(agent_id)
    if token is None:
        console.print(f"There is no agent called {agent}.", style="red")
        raise typer.Exit(1)
    session_id = runner.run_blocking(agent_id, task, api, token, force_scripted=scripted)
    found = sessions.get_session(session_id) or {}
    label = "Scripted agent (offline)" if found.get("mode") == "scripted" else f"Claude {found.get('model')}"
    console.print(f"{session_id}  {label}  {found.get('status')}", style="bold")
    for turn in found.get("transcript", []):
        text = turn.get("text") or json.dumps(turn.get("args") or turn.get("result"), default=str)[:300]
        console.print(f"  {turn.get('role'):<12} {turn.get('tool') or ''} {text}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
