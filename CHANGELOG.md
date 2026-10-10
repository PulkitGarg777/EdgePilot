# Changelog

## Unreleased

Adds a deterministic headless 2D station world, immutable state/action contracts, bounded skill plans, independent installation/dispatch checks, current-state monitoring, and a conventional executor. Scripted and real-model missions inspect stations and return home; external scripts exercise goal replacement, target unavailability, and irrelevant updates.

Connects the existing resident model through FIFO/newest-pending scheduling with typed request provenance, stable prompt hashes, constrained output and independent validation, optional request expiry, deadline draining, and raw/event traces. Adds live-mission commands and paired comparison, CPU/wall validation measurements with clock-granularity caveats, and separately recorded mock versus real-model evidence in [LIVE_MISSIONS.md](docs/LIVE_MISSIONS.md). The existing probe and wait-only commands remain compatible.

Adds repeatable live-model characterization with a predeclared batch manifest, counterbalanced policy order, rotated scenarios, fail-closed execution, trace-integrity checks, and deterministic metric/report regeneration. The first development batch recorded all 40 planned episodes across five scenarios; its descriptive results and telemetry limitations are documented without claiming inference cancellation, energy savings, or a general policy advantage.

Adds a primary-source related-work matrix and overlap assessment covering the roadmap's scheduling, robotics, guardrail, serving, and measurement references. Narrows the proposed research question to the future utility of unfinished planning versus measured stop/restart cost; novelty remains unestablished.

Adds the isolated EP-010 cancellation-feasibility probe with raw-socket disconnects, calibrated server task/progress mapping, task-scoped cancel/release evidence, conservative reuse gating, counterbalanced finish/disconnect trials, and deterministic reports. On the pinned runtime, streaming generation cancellation succeeded with verified reuse in all eight development trials; non-stream disconnects cancelled none of eight and ran to natural completion. This does not add cancellation to the production mission worker or establish prompt-phase cancellation, GPU kernel preemption, energy savings, or generality beyond the tested configuration.

Completes the [EP-011 research decision](docs/RESEARCH_DECISION.md): narrow and continue through declared relevance, acknowledged streaming-generation cancellation, and strong simple baselines. Documents separate candidate implementation/evaluation gates, pivot conditions, evidence limitations, and faculty feedback material. EP-015 is next; runtime behavior is unchanged.

Completes EP-015 with the `edgepilot.relevance.v1` declared-dependency contract. Requests retain immutable dependency declarations; traces record conservative invalidating, harmless, accommodating, and unknown relevance decisions using observed state and the original request snapshot. Independent goal/version, installation, and dispatch checks remain authoritative. Added focused classifier/runtime tests; the full offline suite passes with 198 tests and 6 subtests. EP-016 is next; production inference cancellation remains unimplemented.

Completes EP-016 for the pinned streaming-generation scope. Adds a strict SSE transport, immutable evidence reducer, task-scoped log/slot monitor, exclusively owned single-slot server lifecycle, acknowledged worker reuse, controlled mission integration, and finish/cancel verification arms. A live goal-replacement run failed closed on an offset-boundary defect; the regression-fixed run then acknowledged one cancellation, reused the same session for a distinct natural completion, and completed the replacement mission. Prompt-phase and nonstreaming cancellation remain unsupported; default mission policies are unchanged.

## 0.1.0 — 2026-09-21

First wait-only scheduling pilot. Adds typed versioned contracts, FIFO and newest-pending policies, deterministic scenarios, background HTTP inference, shared validation and fallback, deadline draining, event/raw-response logging, per-goal censoring, and comparison reports. Includes offline mock/stub tests and separately documented real-model integration evidence.

Preserves the verified runtime probe and existing Windows/D: setup. Adds a task tracker reconciled with the original Word plan, decision/build logs, and repository contribution templates. No inference cancellation or robot execution is implemented.
