# Next milestone: a deterministic, guarded simulated mission

Parent roadmap: [TASKS.md](TASKS.md). The initial world and guarded scripted mission are implemented and tested; checked items below link to the evidence in [SIMULATION.md](SIMULATION.md). The remaining immediate work is model-request provenance followed by real-model integration. No real-model skill mission has been tested yet.

## Outcome

One simulated robot completes a small station-inspection mission and returns home using declared skills and a conventional executor. Scene time keeps advancing while planning is delayed. Goal replacement, unavailable stations, and invalid plans produce logged rejection/refresh/wait or controlled-stop behavior. First establish this without a model, then connect the existing resident model.

Use the current Windows environment, pinned llama.cpp server/model, and D: storage. Keep the existing wait-only pilot reproducible. Start without rendering, camera perception, physical hardware, ROS2, training, or GPU cancellation. Record whether a lightweight deterministic scene suffices before installing MuJoCo, which the Word plan proposes.

## Ordered implementation checklist

### 1. Define the state, action, and trace contract (EP-007)

- [x] Specify a minimal room with a home location and two named inspection stations; define robot position/busy state, station availability, inspected state, mission goal/version, and immutable observation snapshot identity.
- [x] Write typed schemas for skill names/arguments, a bounded plan, result/decision/event records, and explicit terminal reason codes. Keep allowed actions small: `navigate_to`, `inspect`, `wait`, `return_home`.
- [x] Write the timing protocol: monotonic clock origin, integer nanosecond fields versus existing seconds, scheduled versus delivered event times, and the order of updates, completion handling, validation, and dispatch. Choose and version the format before implementing it.
- [ ] Add request provenance: snapshot/goal identity, model ID, stable prompt hash, output budget, optional deadline, and normalized response usage/timing. Define null values for unavailable metrics.
- [x] Define a compatibility path for current `pilot-*` traces and commands. Preserve their modules/seconds-based format, add a separate simulation schema, and test plan round trips, version identity, time ordering, and all existing pilot/probe tests. No legacy-trace conversion is introduced.

**Exit:** a written protocol and tested contracts that support stateful skills without breaking the preserved probe. A directory reshuffle is not required. Suggested first commit: `EP-007: define versioned scene and action contracts`.

### 2. Build scene state and deterministic replay (EP-012, scene portion)

- [x] Implement the fixed small scene and direct state observer. Use a controllable clock for tests and a wall-clock runner for demonstration.
- [x] Define repeatable external events: mission replacement, station unavailable, and harmless telemetry updates. Deliver them on a fixed schedule independent of worker completion.
- [x] Define conventional motion/inspection transitions with finite durations and bounded destinations. Establish deterministic nominal mission inputs using a scripted/symbolic plan.
- [x] Verify replay from the same initial state and event script; record event lateness and simulation gaps without hiding missed progress.

**Exit:** reproducible state evolution and observations, plus documented dynamics limits. The scene contains enough behavior to test scheduling; it does not establish physical fidelity.

### 3. Put mandatory checks before execution (EP-013)

- [x] Reject malformed/incomplete action objects, undeclared skills, unknown targets, invalid argument types/ranges, and missing state. Generation truncation remains handled by the old pilot validator; the new simulator does not consume model output yet.
- [x] Reject superseded request IDs/goals before installing a plan. An old snapshot alone must not invalidate an otherwise applicable action.
- [x] Validate current preconditions at installation and immediately before dispatch; for example, inspection requires arrival at an available target.
- [x] Make the final precondition/version check and skill dispatch atomic relative to scene updates by keeping ownership in one executor loop or an equivalent tested mechanism.
- [x] Specify monitoring and stop behavior for a skill already in progress when its goal or required state changes. Distinguish stopping a conventional simulated skill from cancelling inference.
- [x] Implement wait/stop fallback for the scripted executor and log snapshot, skill, outcome, refresh reason, and failed constraints.
- [ ] Reuse the same fallback/checks across live scheduling policies and measure validation CPU duration; the initial simulation logs `validation_time_ns: null` explicitly.

**Exit:** tests exercise a state change between plan acceptance and dispatch, stale IDs/goals, unavailable targets, missing state, malformed output, and relevant changes during execution. No rejected action reaches the executor.

### 4. Complete the mission using a scripted planner (EP-012, executor portion)

- [x] Connect only validated skills to the conventional executor; inspect both available stations and return home in the nominal case.
- [x] Define observable mission success, progress, failure, refusal, and episode timeout. A wait-only episode or blocked action must not count as mission success.
- [x] Run a deliberately delayed fake worker while the scene and skills continue; verify ordering with a mock clock rather than wall-clock timing thresholds.
- [x] Exercise goal replacement, target unavailability, irrelevant updates, worker failure, and clean episode termination. Preserve one reproducible no-model demo trace on D:.

**Exit:** a mission completes without an LLM, guarded failure cases behave as specified, and delayed planning does not freeze the scene. This establishes the executor baseline before attributing outcomes to model planning.

### 5. Connect the existing model (EP-014)

- [ ] Extend generation to the bounded skill-plan schema while keeping independent validation and execution preconditions.
- [ ] Feed identical observed state, model settings, output budget, validator, and executor to FIFO and newest-pending policies. Keep stable prompt content separate from runtime timestamps.
- [ ] Preserve raw requests/responses and separate planner validity failures, stale scheduling decisions, executor failures, and HTTP/infrastructure failures.
- [ ] Run short real-model nominal and state-change episodes if the server is ready. Compare with the scripted baseline; record actual task outcomes and censored goals.
- [ ] Document a Windows reproduction command and report separately what passed with fake workers and with the real model.

**Exit:** a real-model mission is evaluated through the same guarded executor with honest successes/failures; a valid JSON response is not enough. If the small model cannot complete the mission reliably, investigate that result without weakening checks.

## Research work to advance alongside this milestone

| Task | Concrete next artifact | Decision it informs |
| --- | --- | --- |
| EP-008 | Repeated, counterbalanced paired-run manifest and characterization report | Whether discarded work and response delays are repeatable |
| EP-009 | Closest-work matrix and one-page overlap assessment | Whether there is a distinct research question |
| EP-010 | Pinned-runtime cancellation feasibility record with measured terminal acknowledgement or an explicit unsupported result | Whether a cancellation policy is technically justified |
| EP-011 | Continue/narrow/pivot memo drawing on the above, with bounded claims if scene evidence is not yet ready | Whether to proceed to the advanced policy or focus on characterization/relevance |

These are independent work items, not an instruction to spawn agents or contact external people. The cancellation feasibility experiment may investigate stop behavior; production inference cancellation remains absent until EP-016 is implemented and verified.

## Definition of done and handoff

Finish a subtask with the smallest meaningful test set and record the outcome. Finish this milestone with a reproducible scripted mission, guardrail failure tests, nonblocking scene progress, separately labelled real-model evidence, and updated roadmap/build log. Include configuration/trace schema versions and run identifiers. The executor owns motion; the scheduler owns planning requests. Keep required checks and fallback identical across policies.

Use signed commits referencing the parent EP ID. The user currently performs commits manually. Do not mark the whole milestone complete after only the scene or schema is implemented; do not claim GPU cancellation, energy savings, research novelty, or physical safety.
