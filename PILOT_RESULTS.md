# Scheduling pilot results — 21 September 2026

Implemented typed versioned observations/goals/requests/results/terminals, FIFO and newest-pending queues, deterministic finite scenarios, event/raw HTTP logging, deadline draining, per-goal censoring, and a paired trace comparison command. The original probe and handoff were preserved byte-for-byte, verified by SHA-256 before and after. The workspace was not a Git repository; no initialization, reset, or commit was performed.

## Offline verification

The existing Windows interpreter `D:\EdgePilot\.venv\Scripts\python.exe` ran:

```powershell
$env:TEMP = 'D:\EdgePilot\tmp'
$env:TMP = 'D:\EdgePilot\tmp'
.\.venv\Scripts\python.exe -m pytest -q --basetemp=D:\EdgePilot\tmp\pytest-pilot
```

Result: **50 tests passed, plus 6 passing subtests**, in 1.87 seconds. This includes the original probe's four tests. Scheduler tests use a virtual clock and controllable fake worker; adapter tests use a controlled local HTTP stub or injected exceptions. They cover queue order, pending replacement, active-request preservation, goal-version rejection, completion/update ties, irrelevant updates, observation progress, failures/timeouts, deadlines, censoring, raw response preservation, and clean termination. These are **mock/stub tests, not real-model inference tests**.

## Real-model integration

Six final episodes ran sequentially against the already-running server; no second server or replacement model was started. All six completed, with no invalid-plan rejection, HTTP failure, timeout, or deadline cutoff. These are short integration checks, one sample per policy/scenario, not a controlled performance benchmark.

- Python 3.11.4; EdgePilot 0.1.0; pytest 9.1.1. Each run's metadata records the complete installed package set.
- Server `/props`: build `b11065-ce8caa6e6`, alias `edgepilot`, one slot, Q8_0.
- Running arguments: `-m D:\EdgePilot\models\Qwen3-0.6B-Q8_0.gguf --alias edgepilot -ngl 99 -c 2048 -np 1 -t 4 -b 256 -ub 128 -fa off --jinja --host 127.0.0.1 --port 8080`.
- Recomputed model SHA-256: `9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`, matching the original record in every run.
- Request configuration: temperature 0, seed 0, max_tokens 128, nonstreaming, prompt cache disabled, thinking disabled, original wait-only response schema plus independent validation.
- CLI configuration: 50 ms observation target, 20-second episode admission/acceptance deadline, 10-second socket timeout. Metadata/startup inventory is outside episode timing.

| Scenario | Policy | HTTP requests | Accepted | Stale | Coalesced before inference | Rejected worker wall (s) | Max tick gap (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| mixed | FIFO | 6 | 1 | 5 | 0 | 4.012 | 0.058 |
| mixed | newest-pending | 4 | 2 | 2 | 2 | 2.717 | 0.056 |
| spaced | FIFO | 3 | 3 | 0 | 0 | 0 | 0.057 |
| spaced | newest-pending | 3 | 3 | 0 | 0 | 0 | 0.056 |
| irrelevant | FIFO | 1 | 1 | 0 | 0 | 0 | 0.055 |
| irrelevant | newest-pending | 1 | 1 | 0 | 0 | 0 | 0.056 |

In the mixed workload, newest-pending avoided two HTTP calls by replacing pending requests, and accepted goal 5 before it was superseded. FIFO eventually ran all six requests and rejected the first five. The six-second spacing control allowed both policies to accept every goal. Irrelevant observation updates caused no extra planning in either policy.

The mixed scenario uses goal times 0, 0.1, 0.2, 0.3, 2, and 4 seconds. Its goal-by-goal outcomes are:

| Goal | FIFO latency (s) | Newest-pending latency (s) |
| --- | --- | --- |
| 1–4 | superseded; null | superseded; null |
| 5 | superseded; null | 1.941 |
| 6 | 0.803 | 1.173 |

Newest-pending was **not faster for every accepted goal**. Final-goal latency was lower for FIFO in this pair. HTTP medians were 0.749 seconds for FIFO and 1.229 seconds for newest-pending. The irrelevant scenario performed identical scheduling, yet its single HTTP call took 1.863 seconds for FIFO and 0.297 seconds for newest-pending, demonstrating substantial timing variability that cannot be attributed to policy. Runs were sequential, FIFO first, with no randomized order or repetitions; do not infer a general speedup from these samples.

The final spaced control had HTTP times of 1.275–1.872 seconds, below its six-second update interval. Mixed burst intervals were 0.1 seconds, below every observed final HTTP service time. Maximum scenario-event lateness across the final episodes was about 5.2 ms. Actual delivery jitter is retained separately from the identical scheduled times, and all eight shared request payloads across the three pairs were checked for equality. Observation gaps are observed values, not hard guarantees.

`rejected_worker_wall_seconds` includes HTTP time for nonaccepted results; it is only a proxy for discarded work, not GPU compute or energy. Coalesced requests never started inference. Superseded goals retain null latency, not zero. Both policies use identical validators and wait fallbacks. **Inference cancellation is not implemented.** No robot action was executed, and these results do not establish robot task quality or physical safety.

## Preserved final traces

Each directory contains metadata, events, request JSON, raw response bytes, and a summary. The original baseline and probe directories remain separate.

| Scenario | FIFO | Newest-pending |
| --- | --- | --- |
| mixed | [summary](runs/pilot-20260921-022642-fifo-ffd0691d/summary.json) | [summary](runs/pilot-20260921-022649-newest-pending-e4d2707a/summary.json) |
| spaced | [summary](runs/pilot-20260921-022655-fifo-25707e81/summary.json) | [summary](runs/pilot-20260921-022714-newest-pending-6b629f71/summary.json) |
| irrelevant | [summary](runs/pilot-20260921-022733-fifo-c1570612/summary.json) | [summary](runs/pilot-20260921-022736-newest-pending-ab08df23/summary.json) |

Two earlier exploratory mixed runs, `pilot-20260921-022523-fifo-eb38e9f4` and `pilot-20260921-022549-newest-pending-153fd38d`, are also retained. They included scheduling timestamps in prompts and are excluded from the final comparison. Their HTTP times reached about 4.8 seconds, motivating the slower control. Final prompts use stable goal/observation content and keep timestamps only in the trace.

Reproduce the final mixed comparison from PowerShell in `D:\EdgePilot`:

```powershell
.\.venv\Scripts\python.exe -m edgepilot --policy fifo --scenario mixed
.\.venv\Scripts\python.exe -m edgepilot --policy newest-pending --scenario mixed
.\.venv\Scripts\python.exe -m edgepilot.compare .\runs\pilot-20260921-022642-fifo-ffd0691d .\runs\pilot-20260921-022649-newest-pending-e4d2707a
```

The last command reads the recorded pair; substitute newly printed directories to compare a new pair. Repeat the first two commands with `--scenario spaced` or `--scenario irrelevant` for the controls. `burst` and `isolated` are additionally covered offline but were not separately run against the real model in this final set.

Real-server timeout, failure, deadline, and cancellation behavior were not fault-injected. Timeout/deadline scheduler behavior was verified with mocks; no cancellation protocol is claimed. The socket timeout is not a hard process deadline, and active calls are drained after an episode deadline. Future work remains meaningful bounded skills, state/precondition checks, a simulated scene, repeated controlled measurements, and a separately verified cancellation acknowledgement protocol.
