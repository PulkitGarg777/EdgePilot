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

## D006 - Maintain one backlog and a bounded next milestone (21 September 2026)

Use TASKS.md as the authoritative whole-project backlog and NEXT_STEPS.md as its ordered implementation checklist. Preserve EP IDs. The next engineering work proceeds from contracts to scene state, mandatory checks, conventional skill execution, and resident-model integration. The research evidence track can proceed alongside this bounded work, but its continue/narrow/pivot decision gates the advanced cancellation/candidate-policy build. The Word plan's twelve-week, 15-hours-per-week schedule is a planning assumption, not proof of progress or a newly confirmed deadline.

## D007 - Initial world uses a headless deterministic scene (21 September 2026)

Implement home and two stations in a bounded 2D room with straight-line constant-speed motion, timed inspections, direct state snapshots, and serialized checks/dispatch. This is sufficient to exercise changing availability, goals, and skill preconditions without a physics/rendering dependency. Do not install MuJoCo yet; revisit it when collision/contact or physical dynamics are needed. Scripted planning first establishes executor correctness. These simulated checks do not demonstrate physical safety or model task quality.

## D008 - Version simulation traces separately from the pilot (21 September 2026)

New simulation traces use `edgepilot.simulation.v1` and episode-relative integer nanoseconds, with a virtual clock for deterministic tests and `perf_counter_ns` for wall-clock runs. Preserve existing pilot contracts, commands, and second-based traces without implicit migration. Plan provenance binds request, goal, and original snapshot; old snapshots remain acceptable when current preconditions hold. CPU validation timing is unmeasured and logged as null. The simulation's synthetic planner is not an HTTP adapter and supplies no cancellation evidence.

## D009 - Reuse guarded execution for real-model policies (21 September 2026)

Add `edgepilot.mission.v1` and a bounded model adapter over the existing HTTP transport. Preserve the old pilot and scripted simulator. Both policies use the same admission rules, prompt/settings, independent checks, executor, and fallback. The client binds request/goal/snapshot identity; the model supplies actions only. Keep request identity/timestamps outside prompt text. Current-state checks remain mandatory even with constrained generation. Finite request budgets and optional TTLs bound retries without inventing cancellation.

Run fixed external scripts through ordinary wall-clock execution. Goal latency measures accepted plans separately from completed missions; partial/wait-only acceptance is insufficient for success. Preserve superseded/censored goals and recovered failures. Shared scripts can yield different sampled states/trajectories. Single integration pairs are not the repeated characterization required by EP-008.

Measure thread CPU and monotonic elapsed validation time separately. Windows zero CPU samples can be below clock granularity; retain zero counts and avoid claims of zero overhead. Wall duration includes scheduling/clock effects and excludes JSON decoding/logging. Broader overhead evaluation remains EP-020. Detailed evidence and limits: [LIVE_MISSIONS.md](LIVE_MISSIONS.md).
