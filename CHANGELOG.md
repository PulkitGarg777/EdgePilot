# Changelog

## Unreleased

Adds a deterministic headless 2D station world, immutable state/action contracts, bounded skill plans, independent installation/dispatch checks, current-state monitoring, and a conventional executor. Scripted missions inspect two stations and return home; external scripts exercise goal replacement, target unavailability, and irrelevant updates. Includes virtual/wall-clock runners, versioned nanosecond traces on D:, and 51 offline tests. Real-model skill planning and inference cancellation remain unimplemented. The existing wait-only probe and scheduling pilot are unchanged.

## 0.1.0 — 2026-09-21

First wait-only scheduling pilot. Adds typed versioned contracts, FIFO and newest-pending policies, deterministic scenarios, background HTTP inference, shared validation and fallback, deadline draining, event/raw-response logging, per-goal censoring, and comparison reports. Includes offline mock/stub tests and separately documented real-model integration evidence.

Preserves the verified runtime probe and existing Windows/D: setup. Adds a task tracker reconciled with the original Word plan, decision/build logs, and repository contribution templates. No inference cancellation or robot execution is implemented.
