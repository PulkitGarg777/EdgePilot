# Decision log

## D001 — Preserve the verified Windows runtime (21 September 2026)

Use the existing environment, model, server, and D: storage paths. Keep the original probe unchanged and reuse its response schema/validator. Evidence: [handoff](../CODEX_HANDOFF.md) and [pilot report](../PILOT_RESULTS.md). Do not migrate to Linux, download another model, or install a native toolchain without a demonstrated need.

## D002 — First scheduler pilot has no inference cancellation (21 September 2026)

Implement FIFO and newest-pending handling with one active HTTP call. Coalesce only pending requests. Drain the active call after an episode cutoff. Treat transport errors conservatively because server computation may continue. This is the handoff's intentional first slice of the broader Word roadmap. Verified cancellation remains EP-010/EP-016.

## D003 — Match prompts and include a slower control (21 September 2026)

Keep timestamps in traces rather than model prompt text so paired requests have matching content. Add six-second goal spacing after exploratory service times approached 4.8 seconds. Preserve exploratory runs, but exclude them from final-prompt comparisons. Report variability and final-goal latency even when they do not favor newest-pending.

## D004 — Reconcile the roadmap without reorganizing working code (21 September 2026)

The Word plan's `src/edgepilot/` tree and nanosecond contracts are proposed targets. Current `edgepilot/` modules and relative monotonic seconds satisfy the first handoff task, but do not complete all proposed fields or architecture. Track those gaps explicitly under EP-007. Expand/refactor modules as real responsibilities arrive rather than move files solely to match a speculative tree.

## D005 — Research decision remains open (21 September 2026)

The wait-only pilot establishes scheduler plumbing and some discarded-work observations. It does not establish plausible robot workload benefit, task success, cancellation feasibility, energy savings, novelty, or a positive Week 2 decision. Follow EP-008–EP-011 before committing to the proposed research policy. Build deterministic skills and conventional execution before attributing task-quality changes to scheduling.
