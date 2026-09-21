# EdgePilot scheduling pilot

This wait-only scheduler plumbing experiment extends the verified `runtime_probe_v1` without changing it. It uses the existing Windows Python environment and resident llama.cpp server. It does not execute robot actions or implement inference cancellation.

## Project progress

The [whole-project task tracker](docs/TASKS.md) reconciles the original [Word plan](EdgePilot_RT_Plan_of_Action.docx) with implemented code and measured evidence. Start with the [next milestone checklist](docs/NEXT_STEPS.md): versioned scene/action contracts, a deterministic scene, guarded skills, a scripted mission, and then resident-model integration. Use EP task IDs in signed commits and issues. The [decision log](docs/DECISIONS.md), [build log](docs/BUILD_LOG.md), and [changelog](CHANGELOG.md) record scope, progress, and versions. Cancellation and the broader research evaluation remain open and depend on an explicit feasibility decision.

## Run from PowerShell

The existing server must be healthy on `http://127.0.0.1:8080`, with alias `edgepilot`. Do not run paired episodes concurrently against its single slot.

```powershell
Set-Location D:\EdgePilot
.\.venv\Scripts\python.exe -m edgepilot --policy fifo --scenario mixed
.\.venv\Scripts\python.exe -m edgepilot --policy newest-pending --scenario mixed
```

Each command creates a unique `D:\EdgePilot\runs\pilot-*` directory. The CLI sets `TEMP`, `TMP`, `PIP_CACHE_DIR`, and `HF_HOME` to directories inside this workspace for its process. It neither installs dependencies nor starts another server. The existing `psutil` installation is used for runtime inventory; scheduler and HTTP code use the standard library.

To start the pinned server if it is no longer running, first check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

If it is unavailable, start the existing binary in a separate PowerShell terminal:

```powershell
Set-Location D:\EdgePilot
$env:TEMP = 'D:\EdgePilot\tmp'
$env:TMP = 'D:\EdgePilot\tmp'
.\tools\llama-b11065\llama-server.exe -m D:\EdgePilot\models\Qwen3-0.6B-Q8_0.gguf --alias edgepilot -ngl 99 -c 2048 -np 1 -t 4 -b 256 -ub 128 -fa off --jinja --host 127.0.0.1 --port 8080
```

## Policy semantics

Every scripted goal update creates an immutable versioned request with its goal and observation snapshot. Exactly one HTTP request is active. FIFO finishes every queued request in order, including already superseded requests, then rejects their stale responses. Its backlog is bounded by the finite scenario's number of goal events, not a constant-size queue. Newest-pending finishes the active request and retains at most one pending request, replacing earlier pending requests before inference starts. This is coalescing, not cancellation.

Both policies import the original probe's schema and independent validator. Acceptance requires the current goal version, normal model completion, and exactly the supported `wait` action plus a valid reason. Every goal change invalidates the previously accepted plan. Until a current plan is accepted, the coordinator uses the same wait fallback. Observation updates here are explicitly irrelevant; they update the observation version without triggering a request or invalidating an accepted plan.

The main loop polls independently of the HTTP thread, with a 50 ms observation target and approximately 5 ms polling sleeps. Missed ticks are not backfilled. All contract timestamps and event times are episode-relative `perf_counter` seconds. The coordinator processes due scenario events before completed results; a goal change and completion on the same step therefore reject the old result. Actual delivery lateness is logged. Disk logging, OS scheduling, and polling can delay the loop; this is not a hard real-time system.

## Deterministic scenarios

Use `--scenario NAME` with either policy. Event times are fixed relative to episode start and never depend on inference completion. Both policies see the same scenario script. Prompts contain stable goal/observation fields; scheduling timestamps remain in traces. A fixed seed and temperature do not guarantee identical inference timing or model output across runs.

| Scenario | Goal update times (s) | Observation-only updates (s) | Schedule ends (s) |
| --- | --- | --- | --- |
| mixed | 0, 0.1, 0.2, 0.3, 2, 4 | 0.15, 0.4, 1, 2.2, 3, 4.5 | 5.5 |
| burst | 0, 0.1, 0.2, 0.3, 2 | none | 3.5 |
| isolated | 0, 1.5, 3 | none | 4.5 |
| spaced | 0, 6, 12 | none | 18 |
| irrelevant | 0 | 0.1, 0.2, 0.7, 1.6 | 2 |

The six-second control was added after exploratory HTTP durations reached about 4.8 seconds, exceeding the earlier probe's approximately 0.5-second service time. It provides a slower workload alongside rapid updates. Inspect actual service times on your hardware; the scenario names do not guarantee that a particular model keeps up.

The schedule must end **and** all queued/active work must finish before normal termination. The default `--deadline 20` stops event admission and result acceptance, marks queued requests as deadline terminals, and drains the active call while continuing observation ticks. The deadline wins ties with events and completions. An active call is never relabelled as cancelled or reused while outstanding. Its result is recorded when it actually returns/fails, and is not accepted after the deadline. Consequently, process duration can exceed the episode deadline.

`--socket-timeout 10` is a socket timeout, not a proven hard HTTP deadline. A stalled/trickling response can prolong shutdown. Transport errors/timeouts end the episode without sending another request because server work may still be running. Inspect the server before starting another episode after such a failure. Confirmed fake-worker failures in offline tests allow later requests; those tests are not evidence that an HTTP failure stopped GPU work. Unexpected infrastructure/validation failures and episode cutoffs produce a nonzero CLI exit status.

## Traces and interpretation

Each run contains:

- `metadata.json`: exact scenario, CLI configuration, Python/package versions, server `/props` including build/configuration, running server command lines, recomputed model SHA-256 and comparison with the original hash record, source hashes, and Git status. Health checks and inventory are outside episode timing.
- `events.jsonl`: scenario delivery/lateness, goal and observation versions, queue/coalescing decisions, dispatch and queue waits, HTTP responses/durations, terminal decisions, accepted plans, fallbacks, and observation gaps.
- `rNNNN-request.json` and `rNNNN-response.raw`: submitted request envelopes/payloads and unmodified HTTP body bytes, including invalid responses and HTTP error bodies when available. Coalesced requests appear in events and never get an HTTP request file.
- `summary.json`: counts, per-goal outcomes, episode duration, observation gaps, and worker wall-time proxies. Readiness/inventory failures instead write `startup-failure.json`.

Goal latency is time from observed goal change to acceptance while that goal is current; `scheduled_latency_seconds` additionally includes event delivery lateness. A goal superseded without acceptance has status `superseded` and null latency. A still-current goal without acceptance at episode end is censored, also with null latency. Earlier accepted goals retain their acceptance and later `superseded_at`. Do not average null latencies as zero or compare only successful goals without reporting censoring.

`http_elapsed_seconds` covers HTTP invocation through response receipt or failure. `worker_wall_seconds` sums these intervals; `rejected_worker_wall_seconds` sums nonaccepted calls, including failures and deadline-discarded results. It is a discarded-work proxy, **not measured GPU compute or energy**. Coalesced pending requests contribute no worker time. Queue wait and coordinator decision delay are separately observable.

Compare two run directories using their printed paths:

```powershell
.\.venv\Scripts\python.exe -m edgepilot.compare .\runs\<fifo-run> .\runs\<newest-pending-run>
```

The comparison rejects different scenarios, model/source hashes, package/Python versions, server defaults/builds, or episode configurations. It prints counts and per-goal latencies, including superseded goals. It does not establish statistical significance. See [PILOT_RESULTS.md](PILOT_RESULTS.md) for the measured runs from this implementation.

## Offline tests (no model)

```powershell
Set-Location D:\EdgePilot
$env:TEMP = 'D:\EdgePilot\tmp'
$env:TMP = 'D:\EdgePilot\tmp'
.\.venv\Scripts\python.exe -m pytest -q --basetemp=D:\EdgePilot\tmp\pytest-pilot
```

The scheduler tests use an explicit virtual clock and controllable fake worker. They check queue order, pending coalescing, active-request preservation, version rejection, same-step goal/completion ties, observation progress, snapshots, failures, timeout aborts, deadline draining, censoring, and clean termination. HTTP adapter tests use a synchronized local stub and injected exceptions; they check raw trace retention, payload/schema settings, worker occupancy, and timeout classification. The original probe's four tests also run unchanged. **None of these tests invoke the real model or CUDA.** Real-model evidence comes only from the separately recorded CLI episodes.

## Code map and remaining scope

`edgepilot/contracts.py` defines typed immutable messages and terminal enums; `scenarios.py` holds finite scripts; `scheduler.py` owns policy/state decisions; `http_worker.py` owns the single HTTP worker; `validation.py` reuses the probe validator; `__main__.py` records inventory and runs episodes; `compare.py` reads paired traces. Tests live under `tests/`. The workspace initially had no Git repository; local version tracking now uses the `main` branch. `.gitignore` excludes environments, models, runtime binaries, caches, logs/runs, archives, and common secret files. Existing work was preserved.

This pilot does not establish robot task success, physical safety, energy savings, or publication novelty. Meaningful bounded skills, preconditions, simulated scenes, and a measured server-side cancellation acknowledgement protocol remain future work.
