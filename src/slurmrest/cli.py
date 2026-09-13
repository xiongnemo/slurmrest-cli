"""Command-line interface."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, config, format
from .client import Client, SlurmRestError

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help="A friendly command-line client for the Slurm REST API (slurmrestd).",
    rich_markup_mode="rich",
)

# Populated by the root callback so every command can reach it.
_state: dict = {}


def _client() -> Client:
    try:
        cfg = config.load(**_state["opts"])
    except config.ConfigError as exc:
        format.err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2) from None
    return Client(cfg)


def _run(fn):
    """Run *fn* with a client, turning API errors into tidy CLI failures."""
    try:
        with _client() as c:
            return fn(c)
    except SlurmRestError as exc:
        format.err_console.print(f"[red]error:[/red] {exc}")
        for e in exc.errors[1:]:
            detail = e.get("error") or e.get("description")
            if detail:
                format.err_console.print(f"  [dim]{detail}[/dim]")
        raise typer.Exit(1) from None


@app.callback()
def main(
    url: Annotated[str | None, typer.Option("--url", "-u", help="Base URL of slurmrestd.")] = None,
    token: Annotated[str | None, typer.Option("--token", "-t", help="JWT for X-SLURM-USER-TOKEN.")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p", help="Profile in the config file.")] = None,
    api_version: Annotated[str | None, typer.Option("--api-version", help="Pin an API version, e.g. v0.0.42.")] = None,
    header: Annotated[
        list[str] | None, typer.Option("--header", "-H", help="Extra header 'Name: value'. Repeatable.")
    ] = None,
    insecure: Annotated[bool, typer.Option("--insecure", "-k", help="Skip TLS verification.")] = False,
    timeout: Annotated[float | None, typer.Option("--timeout", help="Request timeout in seconds.")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit raw JSON instead of tables.")] = False,
) -> None:
    _state["opts"] = dict(
        url=url,
        token=token,
        profile=profile,
        api_version=api_version,
        headers=header,
        insecure=insecure,
        timeout=timeout,
    )
    _state["json"] = json_out


@app.command()
def version() -> None:
    """Show the client version, and the server's if reachable."""
    format.console.print(f"slurmrest-cli {__version__}")
    try:
        with _client() as c:
            versions = c.discover_versions()
            for ns, vs in versions.items():
                format.console.print(f"  server /{ns}: {', '.join(vs)}")
            format.console.print(f"  using: {c.version}")
    except (SlurmRestError, typer.Exit):
        pass


@app.command()
def ping() -> None:
    """Check that the controller is responding."""

    def go(c: Client):
        data = c.ping()
        if _state["json"]:
            return format.dump_json(data)
        for p in data.get("pings") or []:
            ok = p.get("responding") or p.get("pinged") == "UP"
            mark = "[green]up[/green]" if ok else "[red]down[/red]"
            latency = p.get("latency")
            extra = f"  latency {latency}us" if latency is not None else ""
            format.console.print(f"{p.get('hostname', '?')}: {mark}{extra}")

    _run(go)


@app.command(name="status")
def status() -> None:
    """One-screen overview: controller, nodes, GPUs and queue."""

    def go(c: Client):
        from collections import Counter

        pings = c.ping().get("pings") or []
        nodes = c.nodes()
        jobs = c.jobs()

        if _state["json"]:
            return format.dump_json({"pings": pings, "nodes": nodes, "jobs": jobs})

        for p in pings:
            ok = p.get("responding") or p.get("pinged") == "UP"
            mark = "[green]up[/green]" if ok else "[red]down[/red]"
            format.console.print(f"controller {p.get('hostname', '?')}: {mark}")

        states = Counter()
        gpu_used = gpu_total = 0
        for n in nodes:
            states[format._flat(n.get("state")).split(",")[0]] += 1
            total, _, _ = format.parse_gres(n.get("gres"))
            used, _, _ = format.parse_gres(n.get("gres_used"))
            gpu_total += total
            gpu_used += used
        breakdown = "  ".join(f"{k.lower()}={v}" for k, v in sorted(states.items()))
        format.console.print(f"nodes      {len(nodes)} total   {breakdown}")
        if gpu_total:
            style = format._pct_style(gpu_used, gpu_total)
            pct = 100 * gpu_used / gpu_total
            format.console.print(
                f"gpus       [{style}]{gpu_used}/{gpu_total}[/{style}] in use ({pct:.0f}%)"
            )

        jstates = Counter(format._flat(j.get("job_state")).split(",")[0] for j in jobs)
        summary = "  ".join(f"{k.lower()}={v}" for k, v in sorted(jstates.items())) or "empty"
        format.console.print(f"queue      {len(jobs)} job(s)   {summary}")
        format.console.print()
        format.console.print(format.nodes_table(nodes))

    _run(go)


@app.command(name="nodes")
def nodes(
    state: Annotated[str | None, typer.Option("--state", "-s", help="Filter by state substring, e.g. IDLE.")] = None,
) -> None:
    """List compute nodes. (Like `sinfo -N`.)"""

    def go(c: Client):
        items = c.nodes()
        if state:
            needle = state.upper()
            items = [n for n in items if needle in format._flat(n.get("state")).upper()]
        if _state["json"]:
            return format.dump_json(items)
        format.console.print(format.nodes_table(items))

    _run(go)


@app.command(name="partitions")
def partitions() -> None:
    """List partitions. (Like `sinfo`.)"""

    def go(c: Client):
        items = c.partitions()
        if _state["json"]:
            return format.dump_json(items)
        format.console.print(format.partitions_table(items))

    _run(go)


@app.command(name="jobs")
def jobs(
    user: Annotated[str | None, typer.Option("--user", "-u", help="Only this user's jobs.")] = None,
    state: Annotated[str | None, typer.Option("--state", "-s", help="Filter by state, e.g. RUNNING.")] = None,
) -> None:
    """Show the queue. (Like `squeue`.)"""

    def go(c: Client):
        items = c.jobs()
        if user:
            items = [j for j in items if j.get("user_name") == user]
        if state:
            needle = state.upper()
            items = [j for j in items if needle in format._flat(j.get("job_state")).upper()]
        if _state["json"]:
            return format.dump_json(items)
        format.console.print(format.jobs_table(items, "Queue"))

    _run(go)


@app.command(name="job")
def job_detail(job_id: Annotated[str, typer.Argument(help="Job ID.")]) -> None:
    """Show one job in detail. (Like `scontrol show job`.)"""

    def go(c: Client):
        items = c.job(job_id)
        if _state["json"] or not items:
            return format.dump_json(items)
        format.console.print(format.jobs_table(items, f"Job {job_id}"))

    _run(go)


@app.command(name="submit")
def submit(
    script: Annotated[Path, typer.Argument(help="Batch script. Use '-' to read stdin.")],
    name: Annotated[str | None, typer.Option("--name", "-J")] = None,
    partition: Annotated[str | None, typer.Option("--partition", "-P")] = None,
    nodes_: Annotated[str | None, typer.Option("--nodes", "-N", help="Node count or range.")] = None,
    ntasks: Annotated[int | None, typer.Option("--ntasks", "-n")] = None,
    cpus_per_task: Annotated[int | None, typer.Option("--cpus-per-task", "-c")] = None,
    gres: Annotated[str | None, typer.Option("--gres", help="Per node, e.g. 'gres/gpu:4'.")] = None,
    time_limit: Annotated[int | None, typer.Option("--time", help="Limit in minutes.")] = None,
    chdir: Annotated[str | None, typer.Option("--chdir", "-D", help="Working directory on the node.")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o")] = None,
    nodelist: Annotated[str | None, typer.Option("--nodelist", "-w", help="Request specific nodes.")] = None,
    env: Annotated[list[str] | None, typer.Option("--env", "-e", help="KEY=VALUE. Repeatable.")] = None,
    wait: Annotated[bool, typer.Option("--wait", help="Block until the job leaves the queue.")] = False,
) -> None:
    """Submit a batch job. (Like `sbatch`.)"""
    text = sys.stdin.read() if str(script) == "-" else Path(script).read_text(encoding="utf-8")
    if not text.startswith("#!"):
        text = "#!/bin/bash\n" + text

    # slurmrestd wants environment as a list of KEY=VALUE strings, not a mapping.
    environment = list(env or [])
    if not environment:
        keep = ("PATH", "HOME", "USER", "LANG")
        environment = [f"{k}={os.environ[k]}" for k in keep if os.environ.get(k)]
        if not environment:
            environment = ["PATH=/usr/local/bin:/usr/bin:/bin"]

    spec: dict = {"environment": environment}
    if name:
        spec["name"] = name
    if partition:
        spec["partition"] = partition
    if nodes_:
        spec["nodes"] = nodes_
    if ntasks is not None:
        spec["tasks"] = ntasks
    if cpus_per_task is not None:
        spec["cpus_per_task"] = cpus_per_task
    if gres:
        spec["tres_per_node"] = gres
    if time_limit is not None:
        spec["time_limit"] = {"number": time_limit, "set": True}
    if chdir:
        spec["current_working_directory"] = chdir
    if output:
        spec["standard_output"] = output
        spec["standard_error"] = output
    if nodelist:
        spec["required_nodes"] = [n.strip() for n in nodelist.split(",") if n.strip()]

    def go(c: Client):
        resp = c.submit(spec, text)
        job_id = resp.get("job_id")
        for w in resp.get("warnings") or []:
            desc = w.get("description")
            if desc:
                format.err_console.print(f"[yellow]warning:[/yellow] {desc}")
        if _state["json"]:
            return format.dump_json(resp)
        format.console.print(f"Submitted batch job [bold]{job_id}[/bold]")
        if wait and job_id:
            _wait_for(c, job_id)

    _run(go)


def _wait_for(c: Client, job_id: int | str) -> None:
    import time

    terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
    with format.console.status(f"waiting for job {job_id}…"):
        while True:
            items = c.job(job_id)
            if not items:
                break
            st = format._flat(items[0].get("job_state"))
            if any(t in st for t in terminal):
                format.console.print(f"Job {job_id} finished: [bold]{st}[/bold]")
                return
            time.sleep(5)
    format.console.print(f"Job {job_id} left the queue")


@app.command(name="cancel")
def cancel(job_ids: Annotated[list[str], typer.Argument(help="One or more job IDs.")]) -> None:
    """Cancel jobs. (Like `scancel`.)"""

    def go(c: Client):
        for jid in job_ids:
            c.cancel(jid)
            format.console.print(f"Cancelled job [bold]{jid}[/bold]")

    _run(go)


@app.command(name="acct")
def acct(
    since: Annotated[str | None, typer.Option("--since", help="Start time, e.g. 2026-01-01T00:00:00.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="End time.")] = None,
    users: Annotated[str | None, typer.Option("--users", help="Comma-separated user list.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Show at most N rows.")] = 50,
) -> None:
    """Query finished jobs from the accounting database. (Like `sacct`.)"""

    def go(c: Client):
        items = c.db_jobs(start_time=since, end_time=until, users=users)
        items = items[-limit:]
        if _state["json"]:
            return format.dump_json(items)
        format.console.print(format.acct_table(items))

    _run(go)


@app.command(name="config-path")
def config_path_cmd() -> None:
    """Print where the config file is looked up."""
    path = config.config_path()
    format.console.print(str(path))
    format.console.print(f"  exists: {path.is_file()}")


if __name__ == "__main__":  # pragma: no cover
    app()
