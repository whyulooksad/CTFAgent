# CTF Agent

A multi-agent automated CTF (Capture The Flag) solving system: a primary solver agent (Codex/Claude) + a Hermes supervisor + parallel subagent exploration.
Supports multi-challenge scheduling, time-slice round-robin rotation, cross-round session resume, full flag collection with human review.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│        Master Scheduler (master/master.py)                         │
│  adapter: live(panel)/tsec(hosted)/none/mock → challenge list + flag submit │
│  backend: docker(local container)/process(hosted child)/fake(test)│
│  state machine/priority/time-slice rotation/timeout budget | panel :8081   │
│  cross-round: .cc_session/.hermes_session → next round --resume    │
│  flag collection: scan work_dir → flag_candidates.jsonl → Hermes review    │
└──────────────┬───────────────────────────────────────────────────┘
               │ docker run (local) or bash run.sh (hosted/local process)
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Solver (ctf-solver container or run.sh subprocess)                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Hermes (supervisor / external brain)                   │  │
│  │  monitor.py incrementally reads agent.log → hermes agent    │  │
│  │  writes guidance.md / dead_ends.md / board.md               │  │
│  │  reviews flag_candidates → commands fix-up / marks rejected │  │
│  └──────────────────┬─────────────────────────────────────────┘  │
│                     │ md docs + PostToolUse hook                  │
│                     ▼                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Solver agent (primary, Codex/Claude selectable)        │  │
│  │  --resume original session | per-type prompt ≤10 rounds     │  │
│  │  guidance/dead_ends injected live via hook (cleared after)  │  │
│  └──────────────────┬─────────────────────────────────────────┘  │
│                     │ branch.py (daemon, async)                   │
│                     ▼                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │      Subagents (probe agents, managed by branch.py daemon)  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

Four roles:

- **Master Scheduler** — pulls challenges from the platform, schedules solvers, rotates challenges on a time-slice basis (switching challenges while keeping breakpoints), reaps and submits flags, persists state
- **Solver agent (Codex/Claude)** — the only decision maker inside the solver container/subprocess, handles recon, analysis, decision and exploitation
- **Hermes** — supervisor / external brain, continuously reads the solver log to track progress, gives suggestions (`guidance.md`) and hard constraints (`dead_ends.md`), maintains the board (`board.md`)
- **Subagent** — probe agents, managed asynchronously by the `branch.py` daemon to explore decision forks in parallel

Supported challenge types: Web (target URL), Crypto (local attachments), Misc (local attachments), Binary (remote service/artifact).
Multi-flag challenges (`flag_count > 1`): must collect all flags before moving on; time-slice budget = base × flag_count × 0.7.

### Engine selection

The solver engine is selected via the `AGENT_CLI` environment variable (default `codex`):

| AGENT_CLI | Engine |
|-----------|--------|
| `codex` (default) | Codex CLI, bypass mode |
| `claude` | Claude Code with `deepseek-v4-pro` model, `--resume` supported |
| `hermes` | Hermes agent as the primary engine (hosted fallback) |

## Running modes

**Local mode = panel-driven**: the solver runs in a Docker container (DockerBackend + ctf-solver image).

```bash
cd ~/ctf-agent
python3 master/master.py --config master/master_config.json
# open http://localhost:8081 → "Platform connect" enter token → hot-switch to pull challenges
```

No token env vars are needed at startup; connection info is hot-switched through the panel `connect_platform` API.

**Hosted mode = panel-less**: the platform injects `BENCHMARK_BASE_URL`/`BENCHMARK_TOKEN`/`DEEPSEEK_API_KEY`, the entrypoint connects directly, and the solver runs as an in-container `run.sh` subprocess (`backend=process`).

```bash
# build the hosted image (builds the local base image automatically)
bash docker/hosted/build-hosted.sh   # → ctf-solver-hosted:latest + tar.gz
```

**Test mode**: `backend: fake` (no real agent) + `adapter: mock` (built-in fake challenges), runs scheduling logic in seconds.

## Core mechanisms

### Time-slice rotation + cross-round session resume

- Each challenge gets a per-round time budget: `round_time_base + (round-1)×round_time_step`; multi-flag challenges multiply by 0.7
- Timeout → switch challenge (breakpoint preserved) → next round continues the **original solver session** with `--resume <sid>` (not a degraded board-based restart)
- Key files: `work_dir/.cc_session` (solver agent), `.hermes_session` (hermes); master stores `cc_session_id` when rotating
- `run.sh` uses a process-replaced pipeline `>(grep --line-buffered ...)` to filter thinking tokens and write the agent log in real time

### Full flag collection (no direct submission)

- Master scans all text files in `work_dir` (board/agent log/artifacts) for `flag{...}` candidates
- Writes `flag_candidates.jsonl` (with source, `pending`) → triggers Hermes review via monitor
- Hermes reads the source to confirm: real flag → orders the solver to write `progress.md` via `dead_ends.md`; noise → marked `rejected`
- After the fix-up, master submits through the normal `_read_flags` path (does not trust the agent to write `progress.md`)

### Multi-flag long runs

- Challenges with `flag_count > 1` run until all flags are collected, with a full budget per round and resume on the next round
- `_round_timeout` computes the budget from `started_round` (the round at dispatch time) to avoid budget drift across rounds

## Project structure

```
~/ctf-agent/
├── master/                   # multi-challenge scheduling
│   ├── master.py             # scheduler main loop + Config + round advance/rotation
│   ├── solver_pool.py        # solver backends (process/docker/fake) + stop kills process group
│   ├── challenge_state.py    # challenge state machine + extract_flags_all
│   ├── prioritizer.py        # challenge selection priority (0.7×solve-rate + 0.3×score)
│   ├── submitter.py          # flag submission
│   ├── cred_snapshot.py      # credential snapshot (docker mount, incl. hooks.json rewrite)
│   ├── master_dashboard.py/.html  # panel (:8081) + platform connect API
│   ├── adapters/             # none/mock/tsec/live platform adapters
│   └── master_config*.json   # scenario configs: .json(local panel) .hosted .demo/.smoke/.tsec
├── solver/                   # per-challenge Solver (in container or subprocess)
│   ├── run.sh                # startup script + agent background call + cleanup session extraction
│   ├── AGENTS.md / TOOLS.md  # solving instructions / tool manual
│   ├── monitor.py            # Hermes's eyes (incremental agent-log reader + flag candidate trigger)
│   ├── hermes_monitor.md     # Hermes supervision prompt
│   ├── branch.py             # Subagent daemon + CLI
│   ├── dashboard.py/.html    # per-challenge panel
│   └── hooks/                # PostToolUse hook (guidance/dead_ends injection)
├── docker/
│   ├── solver/               # ctf-solver image (local): Dockerfile / build.sh
│   └── hosted/               # ctf-solver-hosted (hosted): Dockerfile.hosted / build-hosted.sh / entrypoint
├── challenges/               # per-challenge work dirs (manual_web_<hash>/, auto-created)
├── cred_snapshots/           # credential snapshots (sensitive, gitignored)
├── master_logs/              # run.sh stdout collection (written by master at dispatch)
├── att/                      # attachment cache
├── docs/                     # design docs + competition log analyses
├── scripts/                  # switch-api.sh and other helper scripts
└── tests/                    # tests (see below)
```

## Testing

All in `tests/`, no real APIs needed (except where noted):

```bash
python3 tests/test_master.py              # scheduler regression 9/9 (fake backend)
python3 tests/test_rotation.py            # rotation/round advance 103/103
python3 tests/test_round_resume.py        # cross-round session mechanism 22/22 (incl. SIGINT cleanup)
python3 tests/test_flag_collection.py     # flag collection 36/36
python3 tests/test_flag_collection_edges.py # edges: dedup/seen/corrupt/rejected 18/18
python3 tests/test_flag_collection_e2e.py # collection→review→fix-up→submit closed loop
python3 tests/test_session_e2e_real.py    # real agent/hermes resume (costs API money)
python3 tests/test_sim_live.py            # real agent simulation (costs API money, no solving)
```

Note: `test_rotation.py` takes ~5-8 minutes alone (many scenarios).

## Key parameters (master_config.json)

| Parameter | Default | Description |
|-----------|---------|-------------|
| adapter | live | none=manual / mock=fake / tsec=Tencent / live=panel connect |
| backend | docker | process=subprocess(hosted) / docker=container(local) / fake=test |
| max_solvers | 3 | parallel solver count |
| max_challenges | 100 | max challenges to attempt |
| round_time_base | 1200 | per-challenge seconds in round 1 |
| round_time_step | 600 | seconds added per round |
| max_rounds | 5 | max rotation rounds |
| resident | true | panel mode stays resident waiting for connect |
| solver_timeout | 3600 | fallback single-solver duration when no challenges to switch to |

## Deployment & images

```bash
# local image (ctf-solver:latest)
bash docker/solver/build.sh

# hosted image (ctf-solver-hosted:latest + tar.gz, builds the local base image first)
bash docker/hosted/build-hosted.sh

# manually run a single challenge (auth/challenge injected via mounts)
# see top of docker/solver/build.sh
```

Hosted deployment: upload `ctf-solver-hosted.tar.gz` + platform injects `BENCHMARK_TOKEN` / `DEEPSEEK_API_KEY`.

## History highlights

- 2026-08-21 fixed cross-round session resume: run.sh foreground pipeline blocked on SIGINT → switched to background + process replacement; master sends SIGINT then SIGKILL to the agent → cleanup extracts the session
- 2026-08-21 fixed `started_round` budget drift: long-run challenges' cross-round budget was being inflated by `current_round`
- 2026-08-21 full flag collection: no direct submission → Hermes review → `dead_ends.md` orders the fix-up