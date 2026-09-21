# Initial station world

This is a headless, deterministic 2D simulator and conventional skill executor. The planner is a small scripted function, optionally delayed by a controllable clock. It does not call the model. The existing FIFO/newest-pending HTTP pilot remains available unchanged.

## Run it

From PowerShell in `D:\EdgePilot`:

```powershell
.\.venv\Scripts\python.exe -m edgepilot.simulation --planner-delay 0.5
```

The default mission is **inspect station A, inspect station B, return home**. It runs against wall-clock time and prints a JSON summary after approximately five seconds. It creates a unique `D:\EdgePilot\runs\sim-*` directory containing `metadata.json`, `events.jsonl`, and `summary.json`. Project temporary/cache paths stay on D:. No package installation or running model server is required.

Replay as fast as possible with an explicit virtual clock:

```powershell
.\.venv\Scripts\python.exe -m edgepilot.simulation --clock virtual
.\.venv\Scripts\python.exe -m edgepilot.simulation --clock virtual --scenario station-unavailable
.\.venv\Scripts\python.exe -m edgepilot.simulation --clock virtual --scenario goal-replacement --planner-delay 1
.\.venv\Scripts\python.exe -m edgepilot.simulation --clock virtual --scenario blocked --deadline 2
```

The blocked example intentionally exits with status 1 and reports `timed_out`. Success exits with status 0. `--tick` defaults to 0.05 seconds and `--deadline` to 20 seconds; both are runtime parameters, not project delivery dates. `--planner-delay` is synthetic delay, not measured inference latency.

## Room, state, and skills

The room spans coordinates 0 through 4 on each axis. Distances use abstract room units. There are no obstacles, collision physics, acceleration limits, sensing uncertainty, battery simulation, or graphics.

| Location | Coordinates |
| --- | --- |
| Home | (0, 0) |
| Station A | (2, 0) |
| Station B | (2, 2) |

Immutable snapshots contain a snapshot ID, observation time, mission version/targets, robot position, station availability, inspection credit, and active skill. Availability can be unknown in the contract; checks fail closed in that case. Snapshots remain immutable when the world changes. Inspection credit belongs to a mission and resets when its goal is replaced.

| Skill | Arguments | Behavior and checks |
| --- | --- | --- |
| `navigate_to` | Known station name | Straight-line motion at 2 units/second; target must be available |
| `inspect` | Known station name | Takes 0.5 seconds; requires arrival at an available station |
| `wait` | Integer `duration_ns`, greater than 0 and at most 10 seconds | Holds position for the requested duration |
| `return_home` | None | Straight-line motion to home at the same speed |

Movement interpolates position between steps. Even a zero-distance navigation takes at least one nanosecond and finishes on a subsequent step. Inspection is credited only after its duration and a successful current-state check. Mission success requires every required station inspected, the robot at home, and no active or pending skill. A valid plan, a wait action, or refusing all actions is not mission success.

## Contracts and checks

An accepted plan carries the expected request ID, current goal version, original request snapshot ID, and 1–16 bounded typed actions. Parsing rejects extra/missing fields, unsupported skills, invalid targets/argument types, boolean values masquerading as integers, and invalid durations. Replayed request IDs, mismatched snapshots, superseded goals, and attempts to replace a busy executor are rejected.

At installation, the validator projects action order through the **current** scene: navigation can establish the location needed by a later inspection. It then repeats current-state preconditions immediately before each actual dispatch. An older snapshot alone is not grounds for rejection. Validation failure does not interrupt an existing valid executing plan; when no executable plan remains, the executor waits and requests refresh.

The single owner loop checks and dispatches without intervening callbacks or concurrent updates. While a skill is active, it monitors target availability. An invalidating change stops that simulated skill at its current position, clears its remaining plan, and waits for applicable work. A new goal also clears the old plan and inspection credit. There is no teleportation back home. Stopping this conventional simulated skill is not inference cancellation.

Validation events record phase (`install`, `dispatch`, or `monitor`), request/goal identity, checked snapshot, skill, outcome, reason, and failed constraints. `validation_time_ns` is explicitly null: CPU validation overhead has not been measured. This is still open under EP-013/EP-020.

## Time and deterministic event order

The new trace schema is **`edgepilot.simulation.v1`**, using episode-relative integer nanoseconds. Wall-clock runs use `time.perf_counter_ns()`; virtual-clock runs advance by the configured tick. The old `pilot-*` schema and floating-point seconds are unchanged and are not implicitly converted. Use their existing comparison command only for pilot traces.

For each step:

1. Validate time and event inputs, then integrate only the already-active skill to the sampled time.
2. A deadline stops execution before event admission, completion credit, or new dispatch at that step.
3. Apply due external updates in script order before granting skill completion. A target becoming unavailable at the same step as inspection completion prevents credit.
4. Check the active skill and process completion, then consume any ready scripted planning result and validate it against current state.
5. Request a new scripted plan if the executor is idle and its remaining mission targets are available; check and dispatch the next action.
6. Record an observation, actual gap, and current snapshot. Stop once the mission and external script finish, or on deadline/worker failure.

Late loop iterations do not secretly execute a backlog of skills: the next skill starts at the next observed dispatch time. External event lateness and observation gaps are explicit. Virtual replay is deterministic given the same initial state, script, tick, and planner delay. Wall-clock delivery has OS/logging jitter and provides no real-time guarantee.

The scripted worker permits one pending request. Goal replacement does not discard that pending result early; it is rejected when ready if stale. On an episode cutoff, a still-pending **synthetic** result is logged as abandoned. This behavior must not be reused as a real HTTP cancellation protocol; the preserved HTTP pilot still drains active calls.

## Scenarios

| Scenario | External updates | Expected behavior |
| --- | --- | --- |
| `nominal` | None | Inspect A and B, return home |
| `station-unavailable` | A unavailable at 0.5 s, restored at 1.5 s | Stop motion or reject a pending plan, wait, then refresh and finish |
| `goal-replacement` | At 0.7 s, replace mission with inspect B and return home | Stop superseded execution or reject its delayed plan; only new-goal inspection earns credit |
| `irrelevant` | Harmless updates at 0.2 and 0.6 s | Continue valid work; no extra planning |
| `blocked` | A unavailable from time zero | Wait and time out; no false success |

The failure mode depends on planner delay and sampled event time, not a hidden scenario-specific policy. A permanently unavailable required target does not silently disappear from the mission. Choosing alternatives or relaxing goals requires an explicit future task specification.

## Verification and recorded demos

Full offline suite on Windows: **101 tests passed, plus six subtests**, in 2.14 seconds. This includes 51 new simulator/contract tests and the 50 existing pilot/probe tests. They cover motion/inspection timing, request/snapshot/goal identity, malformed actions, unknown state, installation-to-dispatch changes, invalidation at completion, active-skill interruption, worker failure, observation progress, deadline termination, and exact virtual replay. No test in this suite invokes a real model.

```powershell
$env:TEMP = 'D:\EdgePilot\tmp'
$env:TMP = 'D:\EdgePilot\tmp'
.\.venv\Scripts\python.exe -m pytest -q --basetemp=D:\EdgePilot\tmp\pytest-simulation
```

Final scripted demos on 21 September 2026:

| Scenario / clock / planner delay | Result | Elapsed scene time | Evidence |
| --- | --- | --- | --- |
| Nominal / wall / 0.5 s | Both inspected; home; 5 completed actions | 4.966 s | [summary](../runs/sim-20260921-031643-nominal-0547cdea/summary.json) |
| Station unavailable / virtual / 0 s | 1 movement stopped, then mission completed | 5.550 s | [summary](../runs/sim-20260921-031643-station-unavailable-d53c0bf3/summary.json) |
| Goal replacement / virtual / 0 s | Old movement stopped; B inspected; home | 3.800 s | [summary](../runs/sim-20260921-031644-goal-replacement-fe819247/summary.json) |
| Irrelevant / virtual / 0 s | Mission completed with only 1 planning request | 4.500 s | [summary](../runs/sim-20260921-031644-irrelevant-fdd2ab0a/summary.json) |
| Goal replacement / virtual / 1 s | 1 stale plan rejected; new mission completed | 5.400 s | [summary](../runs/sim-20260921-031645-goal-replacement-949c6778/summary.json) |
| Blocked / virtual / 0 s | Timed out; no actions executed | 2.000 s | [summary](../runs/sim-20260921-031645-blocked-5f551a07/summary.json) |

The wall-clock demo recorded 99 observations and a largest observation gap of 52.6 ms. These runs are **scripted simulations**, including the wall-clock run. They are neither real-model tests nor a policy performance comparison. Raw run directories remain local and excluded from Git; metadata includes exact configuration, source hashes, Python, and Git state. Earlier development demo traces are preserved too; the table identifies the final source version's traces.

## Remaining work

Real-model skill-plan generation and FIFO/newest-pending execution comparisons are EP-014, not part of this initial world. The broader EP-007 request provenance/usage fields and EP-013 validation CPU timing are also unfinished. No model was loaded, changed, or tested in this step. The original model-based wait-only evidence remains in [PILOT_RESULTS.md](../PILOT_RESULTS.md). Cancellation, physical safety, energy savings, and scheduling benefit on robot tasks remain unestablished.
