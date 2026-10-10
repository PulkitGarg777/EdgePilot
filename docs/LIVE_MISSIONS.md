# Guarded missions with the resident model

The existing Qwen3-0.6B Q8_0 model now plans skills for the initial station world. Both policies use the same immutable observations, prompt builder, output schema, independent validator, fallback, and conventional executor. This is simulated task execution. The regular resident-server path remains noncancelling; the separate EP-016 owned-session harness supports controlled streaming-generation cancellation.

## Reproduce on Windows

Use the existing environment and healthy single-slot llama.cpp server at `http://127.0.0.1:8080`, alias `edgepilot`. The [README](../README.md) includes its existing launch command. Run policies sequentially:

```powershell
Set-Location D:\EdgePilot
.\.venv\Scripts\python.exe -m edgepilot.mission --policy fifo --scenario nominal
.\.venv\Scripts\python.exe -m edgepilot.mission --policy newest-pending --scenario nominal
.\.venv\Scripts\python.exe -m edgepilot.mission --policy fifo --scenario goal-burst
.\.venv\Scripts\python.exe -m edgepilot.mission --policy newest-pending --scenario goal-burst
```

Each command prints its summary and unique `D:\EdgePilot\runs\mission-*` directory. Compare a matched pair using the two printed directories:

```powershell
.\.venv\Scripts\python.exe -m edgepilot.mission_compare .\runs\<fifo-run> .\runs\<newest-pending-run>
```

The comparison verifies source/model hashes, scenario, Python/packages, server settings, generation settings, and configuration. Default output budget is 384 tokens, temperature/seed zero, thinking disabled, prompt cache disabled. Default tick is 50 ms, episode deadline 45 s, maximum admitted requests 24, socket timeout 20 s. `--request-ttl SECONDS` optionally expires a request from admission including queue time; it is disabled by default. No package or model installation is needed. Cache, temporary, model, and run paths remain on D:.

## Scenarios and admission

| Scenario | External changes relative to episode start |
| --- | --- |
| nominal | Inspect A and B, then return home; no updates |
| station-unavailable | A unavailable at 0.5 s, restored at 1.5 s |
| goal-replacement | At 0.7 s replace mission with inspect B and return home |
| goal-burst | At 0.1 s require B, 0.2 s require A, 0.3 s require A and B |
| irrelevant | Harmless telemetry at 0.2 and 0.6 s |
| blocked | A unavailable from time zero; intentional timeout, zero model requests |

External schedules are fixed and independent of HTTP completion. The main loop keeps observing and advancing skills during HTTP inference. All updates due in a step precede completion credit, response handling, validation, and dispatch. No missed skill transitions are backfilled. Delivery lateness and observation gaps are recorded; this is not hard real-time execution.

Admission occurs for a feasible new goal, a relevant availability change while ready for a plan, or an idle mission needing a refresh. An impossible required mission waits without silently dropping the unavailable station. Harmless telemetry does not request a new plan. FIFO retains admitted requests in order, including superseded goals. Newest-pending retains the active request and replaces only queued work. The request budget bounds refreshes from partial, wait-only, or invalid plans.

Requests retain their admission snapshot. Acceptance requires the current goal and current preconditions; an older snapshot alone is not rejection grounds. A goal change clears inspection credit and stops any active simulated skill. Availability changes are checked before installation, before dispatch, and during active execution. A valid partial/wait plan may be accepted, but only actual required inspections and return home establish mission success.

The episode deadline stops admission/acceptance, clears pending work, stops simulated execution, and retains the active HTTP slot until its actual response/failure. Process time can exceed the deadline. A transport timeout/error ends the episode without another request because server work may still be running. The socket timeout is not a guaranteed overall HTTP bound. CLI exit is nonzero for incomplete missions, episode cutoffs, or worker failures; rejected plans that recover are preserved in terminal counts.

## Contracts, traces, and measurements

`edgepilot.mission.v1` uses episode-relative integer nanoseconds. Existing `pilot-*` seconds-based traces and `sim-*` scripted traces keep their separate schemas/commands. Inventory/model hashing occurs before the episode clock starts.

- `metadata.json`: server build/command/configuration, actual model SHA-256, Python/packages, all package source hashes, Git state, scenario/configuration, prompt/schema/generation versions, and clock information.
- `events.jsonl`: immutable requests, queue/coalescing decisions, HTTP results, terminal reasons, observations, external updates/lateness, checked snapshots, validator failures, skill transitions, and mission outcomes.
- `model-NNNN-planning-request.json`: full immutable snapshot/goal, model ID, stable prompt and SHA-256, output budget, optional deadline, and prompt version for dispatched requests. All admitted requests, including coalesced requests, also appear in events.
- `model-NNNN-request.json` and `model-NNNN-response.raw`: transport envelope/payload and unmodified HTTP response bytes. The transport envelope retains legacy seconds fields; the planning request is the authoritative scene provenance.
- `summary.json`: per-goal first accepted-plan latency, supersession/censoring, mission outcomes, request terminals, observation gaps, HTTP elapsed time, and validator timing distributions. Missing token usage and unmeasured metrics are null.

Prompt content contains decision-relevant scene state and goal version, but excludes request IDs and clocks. Model output supplies only bounded actions; the client binds request/goal/snapshot IDs from trusted request provenance. JSON-schema generation does not replace the independent parser and current-state checks.

Validation `validation_time_ns` and summary `validation_cpu` use thread CPU time. Windows can return zero for these short checks; zero samples do not prove zero cost. Separate `validation_wall_ns` and `validation_wall` use the monotonic performance clock, including scheduling/clock overhead. Both clocks surround the check itself, excluding event logging and model-response JSON decoding. Scripted demos leave profiling disabled for deterministic replay. Clock metadata and zero-sample counts are retained.

Superseded/censored goals have null acceptance latency unless accepted before supersession. Report these outcomes with successful goals. Rejected HTTP wall time includes nonaccepted calls, not measured GPU compute or energy. Shared external scripts/settings do not guarantee identical robot trajectories, sampled states, outputs, or service times. These small integration samples are not a statistical policy evaluation.

## Verification

Offline suite (fake workers, virtual clocks, and a local HTTP stub; no model/CUDA):

```powershell
$env:TEMP = 'D:\EdgePilot\tmp'
$env:TMP = 'D:\EdgePilot\tmp'
.\.venv\Scripts\python.exe -m pytest -q --basetemp=D:\EdgePilot\tmp\pytest-mission
```

Tests cover exact FIFO/coalescing order; observation progress; goal/state changes during inference and execution; same-step update/completion ordering; precondition rejection; malformed/truncated model replies; partial plans; request expiry; deadline draining; transport uncertainty; request provenance/raw bytes; usage; and profiling. Real-model results below come only from separate CLI runs against the resident server.

On 21 September 2026, **138 offline tests and six subtests passed** (2.74 s). This includes the unchanged probe/pilot tests and scripted world tests. Failure injection, in-progress skill interruption, request expiry, and timeout/draining evidence in this milestone come from these mock/stub tests; they were not separately induced on the real server.

## Real-model integration results, 21 September 2026

Ten matched-source episodes used the existing Windows Python 3.11.4, llama.cpp `b11065-ce8caa6e6`, Qwen3-0.6B Q8_0, and the defaults above. Model SHA-256: `9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`. All ten completed the current mission, with no malformed model output or HTTP failure. Each scenario has one sample per policy; order was not counterbalanced. All five pairs passed the comparison's provenance/configuration checks.

| Scenario | FIFO elapsed (s) | Newest-pending elapsed (s) | Requests, FIFO / newest | What happened |
| --- | ---: | ---: | --- | --- |
| nominal | 5.730 | 5.735 | 1 / 1 | Both completed five skills: inspect A/B and return home |
| station-unavailable | 7.047 | 7.049 | 2 / 2 | Each rejected one inapplicable plan while A was unavailable, refreshed after restoration, completed five skills |
| goal-replacement | 5.326 | 5.379 | 2 / 2 | Each rejected one stale goal; inspected only B and returned home, three skills |
| goal-burst | 8.220 | 6.795 | 4 / 2 | FIFO rejected three stale responses; newest coalesced two pending requests and rejected one stale response |
| irrelevant | 5.730 | 5.840 | 1 / 1 | Neither replanned for harmless telemetry; both completed five skills |

For the burst, final-goal first accepted-plan latency was 3.451 s FIFO versus 2.026 s newest-pending. The three superseded goals have null acceptance latency under both policies. Rejected HTTP wall time was 2.665 s versus 1.304 s; this is a proxy, not compute/energy saved. Ordinary nominal performance was nearly equal. Short-run service variability, warm-server effects, and ordering prevent an effectiveness claim.

The scripted nominal executor took about five seconds including its configured 0.5 s planner delay; live nominal runs took about 5.73 s with first-plan acceptance about 1.27 s after mission creation. Both execute the same five bounded skills. This comparison establishes model integration, not equal planner cost or a statistical speed comparison.

Observations continued during inference. Maximum observation gap across these runs was 56.875 ms against a 50 ms target. Per-run p95 validator wall duration ranged from 51.6 to 63.7 microseconds; all short CPU samples were zero at the available clock granularity. These are measured elapsed checks, not a CPU-overhead conclusion. State/goal updates in these real runs occurred before skill dispatch; interruption during execution is covered by the deterministic tests and earlier scripted demos, not these ten runs.

## Repeated characterization, 29 September 2026

Run the counterbalanced development protocol against a healthy resident server:

```powershell
Set-Location D:\EdgePilot
.\.venv\Scripts\python.exe -m edgepilot.characterize --repetitions 4
```

This prewrites a manifest and sequentially runs four FIFO/newest-pending pairs for each of the five non-blocked scenarios (40 episodes). Policy-first order alternates within every scenario and scenario order rotates. The runner preserves stdout/stderr, hashes each trace, rejects source/model/configuration drift, stops on uncertain transport state, and regenerates `report.json` and `report.md` from recorded traces. Regenerate without inference using `python -m edgepilot.characterize --report <manifest.json>`.

The local batch `runs/characterization-20260929-134649-4908f29f` completed all 40 episodes: every mission succeeded, no attempt failed, and no pair was excluded. Deterministic regeneration passed all integrity/comparison checks and reproduced byte-identical reports. Focused offline verification passed 49 tests.

| Scenario | FIFO median elapsed (s) | Newest median elapsed (s) | Mean paired delta (newest - FIFO), range (s) |
| --- | ---: | ---: | ---: |
| nominal | 5.656 | 5.645 | -0.017 [-0.101, 0.055] |
| station-unavailable | 6.949 | 6.955 | 0.022 [-0.000, 0.042] |
| goal-replacement | 5.278 | 5.273 | -0.037 [-0.250, 0.112] |
| goal-burst | 8.113 | 6.626 | -1.462 [-1.563, -1.368] |
| irrelevant | 5.629 | 5.635 | 0.012 [-0.000, 0.042] |

In goal-burst, FIFO rejected 12 stale results across four runs; newest-pending rejected four and coalesced eight requests before inference. That mechanism and elapsed difference repeated in these samples. The other scenarios had small, mixed differences. These are four dependent development pairs per scenario from one warm resident server: report descriptive ranges only, with no confidence interval, held-out inference, or general speedup claim. Null goal latencies remain separate from accepted latencies. Background load, RAM/VRAM, GPU compute, and energy were not measured; HTTP elapsed time is a wall-time proxy. Rendering remained headless and no inference cancellation occurred.

### Local run manifest

Paths are local, ignored raw traces on D:; repository readers need a separately shared trace bundle to open them. The pairs share measured runtime source hashes; a later comparison-output table-format fix does not change the recorded runtime results. These paths allow regenerating comparisons without mixing exploratory source versions.

| Scenario | FIFO summary | Newest-pending summary |
| --- | --- | --- |
| nominal | [033821-b222b389](../runs/mission-20260921-033821-fifo-nominal-b222b389/summary.json) | [033828-bca8ff08](../runs/mission-20260921-033828-newest-pending-nominal-bca8ff08/summary.json) |
| station-unavailable | [033835-7f8ebfa5](../runs/mission-20260921-033835-fifo-station-unavailable-7f8ebfa5/summary.json) | [033843-1fd063e7](../runs/mission-20260921-033843-newest-pending-station-unavailable-1fd063e7/summary.json) |
| goal-replacement | [033851-41690188](../runs/mission-20260921-033851-fifo-goal-replacement-41690188/summary.json) | [033702-d9ea5ae0](../runs/mission-20260921-033702-newest-pending-goal-replacement-d9ea5ae0/summary.json) |
| goal-burst | [033709-2734b2cd](../runs/mission-20260921-033709-fifo-goal-burst-2734b2cd/summary.json) | [033718-a9455f9e](../runs/mission-20260921-033718-newest-pending-goal-burst-a9455f9e/summary.json) |
| irrelevant | [033725-b2e7a27a](../runs/mission-20260921-033725-fifo-irrelevant-b2e7a27a/summary.json) | [033732-c9dcc278](../runs/mission-20260921-033732-newest-pending-irrelevant-c9dcc278/summary.json) |

Six exploratory episodes are retained separately: `033407-fifo-nominal-7eb49a4a` (before wall profiling), `033626-fifo-nominal-42861acf`, `033633-newest-pending-nominal-94a96469`, `033640-fifo-station-unavailable-6f97a78d`, `033648-newest-pending-station-unavailable-492c1435`, and `033656-fifo-goal-replacement-e74f238c` (before response-envelope hardening). All succeeded; they are excluded from the table to keep one consistent measured source version. Each has the prefix `runs/mission-20260921-`.

## Immediate next work and user input

EP-008 through EP-016 are complete for their bounded development scopes. The [EP-011 decision](RESEARCH_DECISION.md) now advances to EP-017 strong simple baselines. The [EP-016 record](STREAMING_ADAPTER.md) documents the owned finish control, a failed-closed diagnostic cancellation run, and a successful actual-mission cancellation/reuse run. Automatic task-aware policy selection and a candidate policy remain unimplemented.

Before a held-out research evaluation, agree on a target application and acceptable task-success/latency tradeoffs with the user or project supervisor. A fixed delivery date, required demonstration hardware, or faculty rubric would affect prioritization if one exists; none is assumed. Keep the initial station mission until evidence warrants expanding it.
