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
