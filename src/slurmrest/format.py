"""Rendering helpers.

Slurm's JSON nests a lot of things that are scalars in the classic CLI output,
so most of this file is about flattening those shapes back into something you
can read in a terminal.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def dump_json(data: Any) -> None:
    console.print_json(json.dumps(data, ensure_ascii=False))


def _flat(value: Any, sep: str = ",") -> str:
    """Slurm returns states and similar as lists; join them for display."""
    if value is None:
        return ""
    if isinstance(value, list):
        return sep.join(str(_flat(v)) for v in value)
    if isinstance(value, dict):
        # {"set": true, "number": 5, "infinite": false}
        if "number" in value:
            if value.get("infinite"):
                return "infinite"
            return "" if value.get("set") is False else str(value["number"])
        if "current" in value:
            return _flat(value["current"])
        return ",".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _tres(entries: Any, want: str | None = None) -> str:
    """Render a TRES list. ``want`` filters to a single type, e.g. ``gres``."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        kind = e.get("type") or ""
        name = e.get("name") or ""
        if want and want not in (kind, name):
            continue
        label = f"{kind}/{name}" if name and name != kind else kind
        out.append(f"{label}={e.get('count')}")
    return ",".join(out)


def tres_str_get(spec: Any, key: str) -> str:
    """Pull one value out of a ``cpu=64,gres/gpu=4`` style string."""
    if not spec:
        return ""
    for part in str(spec).split(","):
        name, sep, value = part.partition("=")
        if sep and name.strip() == key:
            return value.strip()
    return ""


def _tres_brief(spec: Any, keys: tuple[str, ...] = ("cpu", "mem", "node", "gres/gpu")) -> str:
    """Compact a long TRES string down to the parts people actually read."""
    if not spec:
        return ""
    picked = [f"{k}={tres_str_get(spec, k)}" for k in keys if tres_str_get(spec, k)]
    return " ".join(picked)


def _elapsed(job: dict) -> str:
    """Jobs from /slurm/* carry start/end stamps rather than an elapsed field."""
    direct = job.get("elapsed") or job.get("elapsed_time")
    if direct:
        return _secs(direct)
    start = _flat(job.get("start_time"))
    end = _flat(job.get("end_time"))
    if not start.isdigit() or int(start) == 0:
        return ""
    if end.isdigit() and int(end) > int(start):
        return _secs(str(int(end) - int(start)))
    import time as _time

    return _secs(str(int(_time.time()) - int(start)))


def _secs(value: Any) -> str:
    n = _flat(value)
    if not n or not n.isdigit():
        return n
    s = int(n)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}"


def table(title: str, columns: list[str]) -> Table:
    t = Table(title=title, header_style="bold", title_justify="left", expand=False)
    for c in columns:
        t.add_column(c, overflow="fold")
    return t


def nodes_table(nodes: list[dict]) -> Table:
    t = table(f"Nodes ({len(nodes)})", ["NAME", "STATE", "CPUS", "MEMORY", "GRES", "REASON"])
    for n in nodes:
        mem = _flat(n.get("real_memory"))
        t.add_row(
            str(n.get("name", "")),
            _flat(n.get("state")),
            f"{_flat(n.get('alloc_cpus'))}/{_flat(n.get('cpus'))}",
            f"{mem} MB" if mem else "",
            str(n.get("gres") or ""),
            str(n.get("reason") or ""),
        )
    return t


def partitions_table(parts: list[dict]) -> Table:
    t = table(f"Partitions ({len(parts)})", ["NAME", "STATE", "NODES", "TIME LIMIT"])
    for p in parts:
        t.add_row(
            str(p.get("name", "")),
            _flat((p.get("partition") or {}).get("state")),
            str((p.get("nodes") or {}).get("configured") or ""),
            _flat((p.get("maximums") or {}).get("time")),
        )
    return t


def jobs_table(jobs: list[dict], title: str = "Jobs") -> Table:
    t = table(
        f"{title} ({len(jobs)})",
        ["JOBID", "NAME", "USER", "STATE", "NODES", "GPUS", "ELAPSED", "REASON"],
    )
    for j in jobs:
        alloc = (j.get("job_resources") or {}).get("allocated_nodes")
        nodes = j.get("nodes") or (alloc and _flat(alloc)) or ""
        got = tres_str_get(j.get("tres_alloc_str"), "gres/gpu")
        want = tres_str_get(j.get("tres_req_str"), "gres/gpu")
        # Exclusive partitions hand you the whole node, so what you got can
        # exceed what you asked for. Show both when they disagree.
        gpus = got
        if want and got and want != got:
            gpus = f"[yellow]{got}[/yellow] (req {want})"
        t.add_row(
            str(j.get("job_id", "")),
            str(j.get("name", ""))[:22],
            str(j.get("user_name") or j.get("user") or ""),
            _flat(j.get("job_state") or j.get("state")),
            str(nodes),
            gpus,
            _elapsed(j),
            str(j.get("state_reason") or "").replace("None", "")[:20],
        )
    return t


def acct_table(jobs: list[dict]) -> Table:
    t = table(
        f"Accounting ({len(jobs)})",
        ["JOBID", "NAME", "STATE", "ELAPSED", "NODES", "GPUS", "ALLOC", "EXIT"],
    )
    for j in jobs:
        exit_code = (j.get("exit_code") or {}).get("return_code")
        alloc = (j.get("tres") or {}).get("allocated")
        gpus = _tres(alloc, want="gres").replace("gres/gpu=", "")
        t.add_row(
            str(j.get("job_id", "")),
            str(j.get("name", ""))[:20],
            _flat((j.get("state") or {}).get("current")),
            _secs((j.get("time") or {}).get("elapsed")),
            str(j.get("nodes") or ""),
            gpus,
            _tres_brief(",".join(f"{e.get('type')}={e.get('count')}" for e in (alloc or [])
                                if isinstance(e, dict) and e.get("type") in ("cpu", "node")),
                        keys=("cpu", "node")),
            _flat(exit_code),
        )
    return t
