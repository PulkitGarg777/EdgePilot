# EdgePilot project task tracker

Source: [EdgePilot RT Plan of Action, version 1.1, 20 September 2026](../EdgePilot_RT_Plan_of_Action.docx), reconciled with [the runtime handoff](../CODEX_HANDOFF.md), actual code, and [pilot evidence](../PILOT_RESULTS.md) on 21 September 2026.

The Word plan is the research roadmap; the handoff narrowed the first implementation to wait-only scheduler plumbing. The checklist below distinguishes that completed slice from the broader research release. Checked tasks have recorded evidence. Week numbers are the document's proposed schedule, not elapsed work or promises of completion.

Use this file for the whole-project backlog and [NEXT_STEPS.md](NEXT_STEPS.md) for the next implementation milestone. Start next with **EP-007: scene/action contracts and the timing protocol**, then scene state, mandatory guardrails, conventional execution, and model integration. Preserve EP IDs for commits and future issues.

## Milestones

| Milestone | Status | Tasks | Exit criterion |
| --- | --- | --- | --- |
| M0: Verified scheduler pilot | Complete | EP-001 to EP-006 | Signed source baseline, offline tests, preserved real-model traces |
| M1: Research feasibility decision | Open | EP-008 to EP-011 | Evidence-based continue, narrow, or pivot memo |
| M2: Meaningful simulated mission | Next implementation | EP-007, EP-012 to EP-014 | Guarded mission completes without an LLM first, then is evaluated with the resident model |
| M3: Scheduling policies | Gated | EP-015 to EP-018 | Strong baselines and a frozen candidate; any cancellation waits for actual terminal acknowledgement |
| M4: Evaluation | Planned | EP-019, EP-020 | Held-out paired trials, task quality, resource limits, ablations, and failure analysis |
| M5: Reproduction and report | Planned | EP-022, EP-023 | Reproduction package, trace access, signed release, report, and ordinary-speed demo |
| Extension: Generalization | Optional | EP-021 | Separately scoped second-model/device/perception evidence |

M1 evidence gathering and bounded M2 engineering can advance alongside each other. The M1 decision gates the advanced cancellation/candidate-policy build. The current wait-only results do not imply an affirmative research decision. There is no simulated mission, executable robot skill set, or verified inference cancellation yet.

## Current milestone: reproducible pilot and Week 2 decision

- [x] **EP-001 — Repository and progress tracking.** `main` is connected to [PulkitGarg777/EdgePilot](https://github.com/PulkitGarg777/EdgePilot). Baseline commits `91257bd` and `4985bc7` have valid GPG signatures and are pushed. Source, tests, the original plan, and reports are tracked; models, environments, binaries, caches, temporary files, and raw runs remain outside Git. No version tag has been created; signed tag/release work belongs to EP-022.
- [x] **EP-002 — Windows environment and resident inference.** Reuse Windows Python 3.11.4, llama.cpp `b11065-ce8caa6e6`, and Qwen3-0.6B Q8_0 on D:. Verified server configuration, model hash, package inventory, and CPU/GPU baseline evidence are recorded in the handoff and pilot metadata. Do not reinstall the environment or infer toolkit availability from driver output. (Sections 7–8, Week 1.)
- [x] **EP-003 — Independent loop and stale-result rejection.** Preserve the verified runtime probe; observation ticks continue during inference and superseded goals cannot accept a result. This is synthetic observation progress, not simulated physics. (Sections 3, 5, 8.)
- [x] **EP-004 — First typed scheduler contracts.** Immutable observations, goals, requests, results, and terminal records with explicit versions and episode-relative monotonic timestamps exist. These are the handoff's initial contracts, not every field proposed in the Word document. (Section 4.)
- [x] **EP-005 — FIFO and newest-pending plumbing.** Single active request; finite FIFO backlog; one newest-pending slot; identical validator/fallback; coalescing occurs before inference; deadline admission cutoff drains active work. (Sections 5–6; handoff next task.)
- [x] **EP-006 — Deterministic scenarios, traces, and meaningful tests.** Fixed external schedules, virtual-clock tests, raw HTTP traces, per-goal censoring, and comparison command. Evidence: 50 passing offline tests plus six subtests, and six final real-model episodes across mixed, spaced, and irrelevant scenarios. Those are integration checks, not research evaluation. (Sections 6, 10.)
- [ ] **EP-007 — Contract and timing protocol completion.** Before adding the executor, decide and document migration from relative float seconds to the plan's `perf_counter_ns` integer fields. Add explicit snapshot IDs/state facts, typed decision/event records, model ID, prompt hash, optional request deadline, output limit, and normalized token/timing fields. Preserve old trace readability or introduce a trace schema version. (Section 4.)
- [ ] **EP-008 — Repeatable characterization.** Add a paired episode runner and metric regeneration with repetitions and counterbalanced policy order. Report per-goal censoring, service-time variability, event lateness, resource telemetry availability, and confidence intervals where justified. Keep prompt/model/output limits identical. Do not interpret worker wall time as GPU compute or energy. (Sections 2, 6.)
- [ ] **EP-009 — Related-work matrix.** Read and compare the listed scheduling papers (CoMuRoS, VLASH, FutureRTC, RTGen, ActionFlow), guardrail papers (SafePlan, SENTINEL), and foundations. Record mechanisms, assumptions, hardware, baselines, limitations, overlap, and possible distinctness using verified primary sources. The Word document's descriptions are a reading list, not a completed literature review. (Sections 2, 9, 11–12.)
- [ ] **EP-010 — Cancellation feasibility probe.** Establish whether the pinned server/native interface can acknowledge actual stopped computation. Measure request-to-terminal acknowledgement and worker reuse. Closing HTTP or cancelling a Future is insufficient. This is a feasibility investigation before a research go/no-go, not a claim that cancellation exists. (Sections 2, 4–5.)
- [ ] **EP-011 — Written Week 2 decision.** Combine plausible stale-work traces, simple-baseline headroom, cancellation feasibility, and related work in a continue/narrow/pivot memo. The current wait-only results alone do not satisfy this gate. Prepare material for faculty feedback; contact anyone only when explicitly requested. (Section 2; Week 2.)

## Next implementation milestone: a useful simulated mission

Follow [NEXT_STEPS.md](NEXT_STEPS.md): EP-007 contracts, EP-012 scene/state definitions, EP-013 guardrails, EP-012 guarded execution, then EP-014 real-model integration. Splitting scene definition from execution avoids a circular dependency: define the scene first, but put required checks in place before dispatching skills. EP-008–EP-011 remain open research work; avoid implementing a complicated candidate scheduling policy before that gate.

- [ ] **EP-012 — Small deterministic scene and conventional executor.** One room family, one robot, fixed station states, repeatable event scripts, and direct structured observations. Implement bounded `navigate_to`, `inspect`, `wait`, and `return_home` skills. Demonstrate a mission completed by a scripted/symbolic plan without an LLM, with wall-clock simulation continuing during a delayed worker call. Decide whether the initial scene needs MuJoCo before installing it; the Word plan proposes a MuJoCo smoke test. No ROS2 or training. (Sections 3, 6–8; Week 3.)
- [ ] **EP-013 — Action contracts and state-aware guardrails.** Typed bounded arguments; known targets; request/goal validity; latest-state preconditions at plan installation and immediately before skill dispatch; missing-state rejection; wait/controlled-stop fallback. Serialize the final check with dispatch relative to state updates. Add changed-precondition, malformed-action, missing-state, stale-result, and dispatch-race tests. (Sections 11–12; Week 4.)
- [ ] **EP-014 — Local model plans a bounded mission.** Connect the existing resident model to the same skills and independent validator. Separate model failures from scheduler failures. Log validation reasons, checked snapshot, failed constraints, and validation duration. Record ordinary-speed task execution; do not label simulated checks physical safety. (Sections 3, 10–11; Week 4.)

## Scheduling research and evaluation

- [ ] **EP-015 — Declared task dependencies and relevance.** Derive dependencies from known tasks/skills/state. Include invalidating, harmless, and accommodating changes. Never use future events or an unfinished plan as an oracle. (Sections 2, 11–12; Week 5.)
- [ ] **EP-016 — Verified cooperative cancellation adapter.** Proceed only after EP-010. Model receipt versus terminal acknowledgement separately; preserve superseded IDs; release the slot only on actual terminal state. Test late results, worker crashes, and cancellation ordering. Measure stop delay; add a native adapter/chunked prefill only when evidence warrants it. (Sections 4–5; Week 5.)
- [ ] **EP-017 — Strong simple baselines.** Tuned periodic replanning, newest-pending, task-aware cancellation, deadline admission, finish-all, and a symbolic planner where applicable. All share model, quantization, budget, observed state, validator, executor, and fallback. (Section 6.)
- [ ] **EP-018 — Candidate policy.** Combine declared relevance, measured remaining-time estimates, and restart/cancellation cost using only decision-time information. Freeze the first candidate after development tuning. (Sections 2, 5; Week 6.)
- [ ] **EP-019 — Predeclared held-out protocol.** Split development and held-out scenarios; predeclare acceptable task-success loss; choose repetitions from observed variability; pair event scripts and report divergent trajectories. Freeze policies before held-out runs. (Section 6; Week 7.)
- [ ] **EP-020 — Evaluation, ablations, and failure analysis.** Report completion and refusal together; useful response latency; rejected/cancelled work; cancellation delay; RAM/VRAM and scheduler/validation CPU overhead. Include contention, tail latency, unnecessary cancellation checks, and relevance/estimation/coalescing ablations. Unsupported telemetry remains missing; energy needs valid sensor coverage. (Sections 6, 12; Weeks 7–8.)

## Reproduction and release

- [ ] **EP-021 — Optional generalization.** Second small model and borrowed-device validation if justified and available. Camera perception is a separately evaluated extension. Drop these before weakening baseline quality. (Sections 1, 7; Week 9.)
- [ ] **EP-022 — Reproducible artifact and releases.** Package worker interface, versioned configs/scenarios, one reproduction command, metric/plot scripts, example mission, and trace-sharing strategy outside Git for bulky data. Define signed tags/releases, including a signed pilot baseline tag if desired. Deliver a clean-checkout reproduction record; local-only run links do not satisfy artifact availability. Ask an external developer to reproduce only when outreach is authorized. (Sections 7, 10; Week 10.)
- [ ] **EP-023 — Report and feedback.** Problem, related work, implementation, results, negative outcomes, limitations, and ordinary-speed demo. Prepare faculty feedback material; select a venue only after results and current calls are checked. Publication and novelty are not established. (Sections 7, 10; Weeks 11–12.)

## Dependencies and planning order

The task descriptions above are the completion criteria; this table determines when work can start and what evidence closes it. An unchecked task is not implemented. A negative feasibility or novelty finding can validly narrow the project.

| Task | Prerequisites | Completion evidence |
| --- | --- | --- |
| EP-007 | Initial contracts and pilot (done) | Tested versioned contracts, timing/trace protocol, and legacy-trace compatibility decision |
| EP-008 | Current pilot; extend after EP-014 | Repeated counterbalanced paired-run manifest and report, including service variability/censoring and background-load/rendering overhead where applicable |
| EP-009 | Current project scope | Verified primary-source matrix and one-page overlap assessment; include the plan's SayCan, MELTing Point, Sarathi Serve, and Age of Information foundations |
| EP-010 | Pinned resident runtime (available) | Actual stop/reuse acknowledgement traces, or explicit evidence that the interface does not support them |
| EP-011 | EP-008, EP-009, EP-010 | Continue/narrow/pivot memo; bound or defer conclusions if plausible scene evidence is not yet available |
| EP-012 | EP-007; EP-013 before dispatch | Deterministic scene/replay, conventional mission success, and progress during delayed planning |
| EP-013 | EP-007 and EP-012 scene/state definitions | Contract, precondition, missing-state, dispatch-race, fallback, and in-progress-skill monitoring tests |
| EP-014 | EP-012 and EP-013 | Separately labelled real-model nominal and state-change mission traces; distinguish planning, scheduling, execution, and infrastructure failures |
| EP-015 | EP-012 to EP-014 | Inspectable dependency/relevance decisions for invalidating, harmless, and accommodating changes |
| EP-016 | EP-010 demonstrates feasibility; EP-011 permits scope | Mock ordering tests plus real worker terminal acknowledgement and stop-delay measurements |
| EP-017 | EP-014; EP-015/EP-016 for task-aware cancellation | Tuned baselines sharing required checks/configuration; noncancelling baselines can proceed first |
| EP-018 | EP-011 decision; EP-015 to EP-017 | Measured development headroom and a frozen candidate, or a documented decision not to build it |
| EP-019 | EP-008/EP-014 for design; EP-017/EP-018 frozen before trials | Predeclared protocol, task-success tolerance, held-out split, seeds, repetitions, and configuration hashes |
| EP-020 | EP-019; frozen policies or a documented characterization-only scope | Paired effects/uncertainty, failures, ablations, task quality, violations/refusals, and telemetry limits; separate finish-generation replays from live trials |
| EP-021 | Stable core and concrete evidence need | Device/model-specific results; optional rather than a release blocker |
| EP-022 | M2 example mission; EP-020 for research release | Clean-checkout reproduction, shared trace manifest, dependency/model/build inventory, signed release |
| EP-023 | EP-009/EP-011 for drafts; EP-020/EP-022 for final report | Report, limitations, ordinary-speed demo, and feedback material |

## Schedule and scope

The Word plan assumes about 15 focused hours weekly for 12 weeks: environment/probe in Week 1, evidence/decision in Week 2, simulator/guardrails in Weeks 3–4, policies in Weeks 5–6, evaluation in Weeks 7–8, optional generalization in Week 9, reproduction in Week 10, and report/demo in Weeks 11–12. Treat this as a capacity assumption until availability is confirmed. Adjust dates using actual progress and dependencies; do not infer completion from the calendar.

Keep the existing Windows environment, model/runtime, caches, temporary files, and logs on D:. Preserve the original probe and runs. Model training, ROS2, adaptive precision, frequency/voltage control, multi-robot work, and physical deployment remain outside the core release. Camera perception and a second device are optional extensions. Do not equate coalescing with inference cancellation, synthetic ticks with robot sensing, simulation checks with physical safety, or rejected HTTP wall time with GPU compute/energy.

## How to maintain this tracker

Use task IDs in signed commit/PR titles, for example `EP-012: add deterministic station scene`. Keep one reviewable change per commit. Check a task only after its completion criterion is met and link its code/tests/report or run manifest. Keep mock/stub and real-model evidence separate. Record material scope or protocol decisions in [DECISIONS.md](DECISIONS.md), measured milestones in [BUILD_LOG.md](BUILD_LOG.md), and version changes in [CHANGELOG.md](../CHANGELOG.md).

This file is the authoritative backlog. GitHub issues can mirror individual tasks when useful; this planning update does not create issues, a project board, tags, or releases. The user currently performs commits manually; signing remains enabled. Workstreams listed as independent do not imply instructions to spawn agents or contact external people.
