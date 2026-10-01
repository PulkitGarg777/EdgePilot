# Build log

## 21 September 2026 — Verified probe and scheduling pilot

- Inspected the handoff, Windows environment, server health/configuration, original probe, and existing run records. Initial Git status reported no repository.
- Preserved original probe/handoff bytes; verified their hashes after implementing a separate `edgepilot` package.
- Added typed contracts, FIFO/newest-pending scheduling, finite event scenarios, shared validation/fallback, deadline draining, raw/event traces, per-goal censoring, and paired comparison output.
- Offline verification: 50 tests and six subtests passed; mocks/stubs only.
- Final real-model integration: six episodes, three paired scenarios, no HTTP/validation failures. Two earlier exploratory runs were retained separately. Full evidence: [PILOT_RESULTS.md](../PILOT_RESULTS.md).
- Inference cancellation, robot execution, physical safety, energy savings, and publication novelty were not established.

## 21 September 2026 — Roadmap review and repository preparation

- Read the complete `EdgePilot_RT_Plan_of_Action.docx` version 1.1. The preceding implementation had used the handoff, not this Word document.
- Reconciled the twelve-week roadmap and guardrail extension with actual code/evidence in [TASKS.md](TASKS.md).
- Added explicit contract/architecture gaps, the open Week 2 decision gate, simulator/skills acceptance criteria, research baselines, and release tasks.
- Prepared a source-only initial repository snapshot, change log, and GitHub task/PR templates. GitHub publication status is tracked under EP-001; do not infer remote publication from these files alone.

## 21 September 2026 - Signed baseline published and actionable backlog

- Repaired the existing GPG agent without disabling signing; a temporary-file signature verified successfully. The user's two commits, `91257bd` and `4985bc7`, both verified locally, and GitHub `main` matched `4985bc7` when checked.
- Updated EP-001 to complete for the actual repository `PulkitGarg777/EdgePilot`. No version tag has been created; release/tag work is tracked under EP-022.
- Expanded [TASKS.md](TASKS.md) into milestones with dependencies, completion criteria, and a research decision gate. Added [NEXT_STEPS.md](NEXT_STEPS.md) for contracts, deterministic scene state, guarded dispatch, scripted mission execution, and then real-model integration.
- This update changes planning documents only. Runtime behavior and the recorded 50-test/six-episode evidence are unchanged; no new model tests or research experiments were performed for it.

## 21 September 2026 - Initial world and scripted mission

- Added `edgepilot/robot` contracts, validation, scripted planner, and deterministic world/executor; added the `python -m edgepilot.simulation` runner. Chose a headless scene without additional dependencies, model changes, or a physics engine.
- Nominal mission: navigate to A, inspect, navigate to B, inspect, return home. Added checks for current preconditions, request/goal/snapshot identity, missing state, bounded arguments, and changes between installation/dispatch or during execution.
- Verification: 101 offline tests plus six subtests passed (51 new simulation tests, 50 existing pilot/probe tests). No model inference was invoked.
- Six final scripted demos covered wall-clock nominal completion, virtual interruption/recovery, goal replacement during execution and delayed planning, irrelevant updates, and intentional blocked timeout. Source hashes, configurations, state traces, and results remain on D:. See [SIMULATION.md](SIMULATION.md) for run links and outcomes.
- EP-012 is complete for the agreed initial headless scope. EP-007 remains partial (model request provenance/usage fields); EP-013 remains partial (validation overhead measurement and shared live-policy integration). EP-014 real-model skill planning has not started.

## 21 September 2026 - Resident-model guarded missions

- Completed typed live request provenance, schema-constrained skill generation with independent decoding/validation, FIFO/newest-pending scene coordination, shared guarded execution, request TTLs, bounded refreshes, deadline draining, and paired trace comparison. Reused the existing Windows environment and resident model; no installations or model changes.
- Offline verification: 138 tests plus six subtests passed. Virtual clocks/fake workers and a synchronized local HTTP stub test state changes, malformed/truncated plans, scheduling, observation progress, deadlines, request provenance, and measurement. These tests do not invoke the model.
- Separately exercised real-model nominal, temporary station loss, goal replacement, goal burst, and irrelevant-update missions. Both policies completed all five scenarios. Final matched-source run manifest and outcomes: [LIVE_MISSIONS.md](LIVE_MISSIONS.md). Earlier exploratory runs are retained on D:.
- Added CPU and wall validation timing. Exploratory CPU samples were zero on Windows; explicit granularity caveats and separate elapsed measurements avoid treating this as zero-cost validation.
- EP-007, EP-013, and EP-014 are complete for the initial headless scope. Next: repeated characterization (EP-008), related-work assessment (EP-009), and runtime cancellation feasibility (EP-010), feeding EP-011. No inference cancellation or statistical policy advantage is claimed.

## 29 September 2026 - Repeated paired characterization

- Added `edgepilot.characterize`, a sequential real-model batch runner with a complete pre-run manifest, counterbalanced policy order, rotated scenario order, fail-closed infrastructure handling, source/model/configuration checks, trace hashes, and deterministic JSON/Markdown report regeneration.
- Added focused offline coverage for scheduling balance, paired metrics, null/censored goals, mission and transport failures, unattempted denominators, startup errors, trace tampering, configuration drift, and small-sample statistics. Verification: 49 focused tests passed.
- Recorded 40/40 planned episodes: four FIFO/newest-pending pairs for each of nominal, station-unavailable, goal-replacement, goal-burst, and irrelevant. All missions succeeded; no trial or pair was excluded. Batch: `runs/characterization-20260929-134649-4908f29f` (local ignored evidence).
- Deterministic regeneration revalidated every recorded trace hash and comparison constraint and reproduced byte-identical `report.json` and `report.md` files. The goal-burst elapsed difference was repeatable across all four pairs (newest-pending minus FIFO mean -1.462 s, range -1.563 to -1.368 s); other scenario differences were small and mixed.
- EP-008 is complete for development characterization. Results are descriptive dependent samples, not a held-out evaluation or general speedup claim. Background load, RAM/VRAM, GPU compute, and energy were not measured; HTTP wall time is only a proxy. No inference cancellation is implemented.

## 29 September 2026 - Bounded related-work review

- Verified and compared the roadmap's primary sources for CoMuRoS, VLASH, FutureRTC, RTGen scheduling, ActionFlow, SafePlan, SENTINEL, SayCan, MELTing Point, Sarathi-Serve, and Age of Information. Added a targeted service-age preemption paper because it directly bounds the proposed remaining-time-aware policy.
- Added [RELATED_WORK.md](RELATED_WORK.md), recording mechanisms, assumptions, hardware, baselines, limitations, and project overlap, plus [RESEARCH_OVERLAP.md](RESEARCH_OVERLAP.md), which states the narrower useful-work versus stop/restart-cost hypothesis and the evidence needed to test it.
- EP-009 is complete for the roadmap's bounded corpus. The review finds prior art for event relevance, newest-update replacement, service-time-aware preemption, guarded skill planning, asynchronous robot control, and inference optimization. It does not establish novelty or an affirmative EP-011 decision.
- Next: EP-010 must establish actual cancellation acknowledgement and worker reuse—or document that the pinned interface cannot do so—before EP-011 decides whether to continue, narrow, or pivot.

## 1 October 2026 - Pinned-runtime cancellation feasibility

- Audited the exact llama.cpp `b11065-ce8caa6e6` HTTP, response-reader, queue, slot-release, decode-synchronization, and abort-callback paths. Added [the EP-010 protocol](CANCELLATION_PROBE_PLAN.md), an isolated raw-socket transport, calibrated slot/task parsing, task-scoped server-log correlation, conservative reuse gating, counterbalanced trial scheduling, and deterministic JSON/Markdown report regeneration. The production mission worker remains unchanged.
- Calibration confirmed one exclusive slot, effective 512-token `ignore_eos` controls, active `id_task`/decoded-token progress, natural completion in both transport modes, and correct distinct-task reuse. One initial calibration correctly aborted on an invalid raw streaming-marker check; retained SSE reconstruction fixed the validator. One first trial batch correctly stopped after non-stream cancellation did not occur; the revised classification records safe natural completion as an unsupported result rather than cancellation success. Both retained batches remain local evidence.
- Final batch `runs/cancellation-trials-20260930-125525-12c1ac56`: 32/32 target trials recorded, all 16 finish/disconnect pairs included, no failures or censoring, and correct distinct-task reuse after every trial. Streaming cancellation was verified in 8/8 disconnect trials; median observed stop-to-release was 46 ms at the 64-token cutpoint and 35 ms at 256 tokens. Paired avoided remaining time medians were 3.701 s and 2.168 s respectively.
- Non-streaming disconnect cancelled 0/8 requests. All continued naturally to the 512-token limit; paired avoided-time medians were -0.024 s and -0.025 s, consistent with no useful cancellation. Report regeneration was byte/hash stable; 32 focused offline tests passed.
- EP-010 is complete as a phase- and transport-specific feasibility result. Prompt-phase compute acknowledgement, arbitrary kernel preemption, production integration, energy, and general tail behavior remain unestablished. The final report explicitly records that the trial manifest omitted the exact runner-source hash; same-process calibration pins the server binary, implementation DLL, and model. Next: EP-011.
