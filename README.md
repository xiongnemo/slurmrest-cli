# slurmrest-cli

A friendly command-line client for the [Slurm](https://slurm.schedmd.com/) REST API
(`slurmrestd`). It gives you `sinfo`/`squeue`/`sbatch`-shaped commands that speak
HTTP + JWT, so you can drive a cluster from anywhere you can reach the API —
no Slurm client install, no shared MUNGE key, no SSH hop.

```console
$ srest status
controller head-01: up
nodes      4 total   idle=3  allocated=1
gpus       4/16 in use (25%)
queue      1 job(s)   running=1

Nodes (4)
+--------------------------------------------------------------------------------+
|NAME     | STATE     | GPUS     | IDX | CPUS  | MEM(GB) | LOAD | PART | REASON  |
|---------+-----------+----------+-----+-------+---------+------+------+---------|
|node-01  | IDLE      | 0/4 a100 |     | 0/64  | 0/488   | 3    | gpu  |         |
|node-02  | ALLOCATED | 4/4 a100 | 0-3 | 64/64 | 488/488 | 2    | gpu  |         |
+--------------------------------------------------------------------------------+

$ srest submit train.sbatch --gres gres/gpu:4 --time 120 --wait
Submitted batch job 1234
Job 1234 finished: COMPLETED
```


## Why

`squeue`/`sacct` print tables meant for humans; scripts and agents end up parsing
them with `awk`. `slurmrestd` already serves clean JSON — this is a thin, typed
client over it with sensible tables for when a human is looking.

It is also handy when the cluster is behind a reverse proxy or zero-trust gateway:
everything is plain HTTPS with headers you control.

## Install

Requires Python 3.10+.

```bash
# one-off, no install
uvx --from git+https://github.com/xiongnemo/slurmrest-cli srest nodes

# or install as a tool
uv tool install git+https://github.com/xiongnemo/slurmrest-cli
```

Development:

```bash
git clone https://github.com/xiongnemo/slurmrest-cli
cd slurmrest-cli
uv sync
uv run srest --help
```

## Configure

Three ways, highest precedence first: flags, environment, config file.
**Nothing is hard-coded** — there are no default hosts or tokens.

```bash
export SLURMREST_URL=https://slurm.example.org
export SLURMREST_TOKEN="$(ssh headnode 'scontrol token lifespan=86400' | cut -d= -f2)"
srest ping
```

`SLURM_JWT` is also accepted, since that is the variable `scontrol token` prints.

### Config file

`~/.config/slurmrest/config.toml` (honours `XDG_CONFIG_HOME`). Profiles let you
keep several clusters side by side:

```toml
[default]
url = "https://slurm.example.org"
token = "eyJhbGciOi..."

[staging]
url = "http://127.0.0.1:6820"
api_version = "v0.0.42"

# Arbitrary extra headers — useful behind an authenticating proxy.
[staging.headers]
"X-Some-Gateway-Token" = "..."
```

```bash
srest --profile staging nodes
```

### Extra headers from the command line

```bash
srest -H "X-Gateway-Id: abc" -H "X-Gateway-Secret: xyz" jobs
```

## Commands

| Command | Classic equivalent |
| --- | --- |
| `srest status` | — (an at-a-glance overview; closest to `bhosts`) |
| `srest ping` | `scontrol ping` — the **controller**, not the compute nodes |
| `srest nodes [--state IDLE]` | `sinfo -N` — GPUs used/total, allocated indices, load |
| `srest partitions` | `sinfo` |
| `srest jobs [--user U] [--state RUNNING]` | `squeue` |
| `srest job <id>` | `scontrol show job` |
| `srest submit <script>` | `sbatch` |
| `srest cancel <id>...` | `scancel` |
| `srest acct [--since ...]` | `sacct` |

Add `--json` to any command to get the raw API payload instead of a table —
that is the mode to use from scripts and agents.

```bash
srest --json jobs | jq '.[] | select(.job_state[] == "RUNNING") | .job_id'
```

### Submitting

```bash
srest submit job.sh \
  --name train --partition gpu --nodes 1 --ntasks 4 \
  --gres gres/gpu:4 --time 240 \
  --chdir /scratch/me --output /scratch/me/train-%j.out
```

`--env KEY=VALUE` is repeatable. If you pass none, a small safe set
(`PATH`, `HOME`, `USER`, `LANG`) is forwarded from your shell.

Read from stdin with `-`:

```bash
echo 'hostname; nvidia-smi -L' | srest submit - --gres gres/gpu:1
```

## Notes and gotchas

These bit us; they are properties of `slurmrestd`, not of this client.

- **The token's user must exist on the server side.** `slurmrestd` resolves the
  UID to a name; if that user does not exist in the environment the daemon runs
  in, submission fails with `Rejecting authentication of user nobody`.
- **`environment` must be a list**, not a mapping — `["PATH=/usr/bin"]`. This
  client always sends the list form.
- **`/slurmdb/*` needs the accounting daemon to accept the same auth.** If
  `srest acct` returns *Unable to connect to database* while `srest jobs` works,
  the JWT settings are usually missing from `slurmdbd.conf`.
- **API versions are negotiated**, not assumed. `srest version` shows what the
  server offers; the newest is used unless you pin `--api-version`.
- **`LOAD` is the host's load average, not the Slurm node's.** Slurm sends
  `cpu_load` as the 1-minute load average multiplied by 100 (so `5` means
  `0.05`); this client scales it back. If several Slurm nodes are carved out of
  one physical machine they will all report the same figure, because they share
  `/proc/loadavg`.

## License

MIT
