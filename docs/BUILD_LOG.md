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
