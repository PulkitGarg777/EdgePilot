# Changelog

## Unreleased

Adds a deterministic headless 2D station world, immutable state/action contracts, bounded skill plans, independent installation/dispatch checks, current-state monitoring, and a conventional executor. Scripted and real-model missions inspect stations and return home; external scripts exercise goal replacement, target unavailability, and irrelevant updates.

Connects the existing resident model through FIFO/newest-pending scheduling with typed request provenance, stable prompt hashes, constrained output and independent validation, optional request expiry, deadline draining, and raw/event traces. Adds live-mission commands and paired comparison, CPU/wall validation measurements with clock-granularity caveats, and separately recorded mock versus real-model evidence in [LIVE_MISSIONS.md](docs/LIVE_MISSIONS.md). The existing probe and wait-only commands remain compatible. Inference cancellation remains unimplemented.

Adds repeatable live-model characterization with a predeclared batch manifest, counterbalanced policy order, rotated scenarios, fail-closed execution, trace-integrity checks, and deterministic metric/report regeneration. The first development batch recorded all 40 planned episodes across five scenarios; its descriptive results and telemetry limitations are documented without claiming inference cancellation, energy savings, or a general policy advantage.

Adds a primary-source related-work matrix and overlap assessment covering the roadmap's scheduling, robotics, guardrail, serving, and measurement references. Narrows the proposed research question to the future utility of unfinished planning versus measured stop/restart cost; novelty and the continue/narrow/pivot decision remain open pending cancellation feasibility.

## 0.1.0 — 2026-09-21

First wait-only scheduling pilot. Adds typed versioned contracts, FIFO and newest-pending policies, deterministic scenarios, background HTTP inference, shared validation and fallback, deadline draining, event/raw-response logging, per-goal censoring, and comparison reports. Includes offline mock/stub tests and separately documented real-model integration evidence.

Preserves the verified runtime probe and existing Windows/D: setup. Adds a task tracker reconciled with the original Word plan, decision/build logs, and repository contribution templates. No inference cancellation or robot execution is implemented.
