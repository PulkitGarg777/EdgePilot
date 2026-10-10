# EP-009 overlap assessment

29 September 2026, updated 1 October 2026. Research judgment based on the [primary-source matrix](RELATED_WORK.md); an input to EP-011 alongside the completed [cancellation-feasibility record](CANCELLATION_PROBE_PLAN.md).

## What the existing work establishes

EdgePilot's present results establish a reproducible integration and characterization artifact. EP-008's four burst pairs favor newest-pending, with mean elapsed difference -1.462 seconds and all missions successful; that is evidence for pending-work coalescing in the tested workload, not evidence that a new candidate policy is necessary. See [recorded results](LIVE_MISSIONS.md#repeated-characterization-29-september-2026).

The novelty boundary is crowded. Event relevance already drives replanning in [CoMuRoS](https://arxiv.org/html/2511.22354v2). Freshness-driven queue replacement and preemption are established in the [AoI survey](https://arxiv.org/html/2007.08564v1); service-age-based decisions also appear in [When to Preempt](https://arxiv.org/html/2402.00845v2). These overlaps rule out a claim based only on event triggers, newest-pending storage, or elapsed-time cancellation thresholds. The matrix also documents prior bounded-skill grounding, asynchronous control, inference optimization, and guardrail mechanisms.

## A plausible question to investigate

**Under what task changes and measured runtime costs does retaining, replacing, or stopping a local planning request improve useful response latency without unacceptable loss of mission success?**

The potentially distinguishing element is the combination of declared task applicability, uncertain remaining service time, and measured stop/restart cost in a guarded skill executor. This is a research hypothesis, not a novelty finding. A cheaper device or the addition of a validator alone would not establish it.

Two cases must be separated. If the active result is guaranteed unusable after a goal change, waiting for it only delays a replacement. With identical fresh-request cost and no reuse effects, cancellation helps latency when terminal-acknowledgement delay is less than residual service time. This is a simple comparator, not a sophisticated policy. If the active result could still be applicable, stopping it sacrifices potential value; that is where relevance and cost estimates might earn their complexity. Already-spent computation is a sunk cost; decisions should use future costs and benefits.

## Evidence needed before building the candidate

- **EP-010 (complete):** streaming generation produced task-scoped cancellation/release/idle and verified reuse in 8/8 development trials; non-streaming cancelled 0/8 and ran naturally to the output limit. Prompt-phase compute acknowledgement remains unresolved.
- **EP-015/017, subject to the roadmap gate:** represent harmless, invalidating, and accommodating changes from declared information; compare newest-pending, task-aware stop, and tuned age/service-threshold rules. A symbolic planner is necessary for this small fully specified world. Treat additional threshold controls as a proposal for EP-011, not an implemented baseline.
- **EP-018/019/020:** demonstrate headroom over the strongest simple controls before freezing a candidate. Predeclare success tolerance; include early/late changes, slow/fast inference, bursts and quiet periods. Ablate relevance, cost estimates, and coalescing separately. Avoid future events, unfinished-plan contents, or oracle remaining time as deployable inputs.
- Report completed missions, refusal/failure, useful-response latency, censored goals, and scheduler/validator overhead together. Measure resource contention explicitly if claiming resource-aware behavior. Preserve EP-008's telemetry limitations.

## Decision implication

The subsequent [EP-011 memo](RESEARCH_DECISION.md) records **narrow and continue** through EP-015 to EP-017. Assess unresolved baseline tradeoffs before implementing a candidate, and require repeated development comparisons before freezing it. Narrow to a characterization/engineering contribution if stop costs dominate, simple controls match the candidate, or a symbolic planner fully explains mission performance. Expand the literature search before any publication-level novelty claim. The memo now governs the implementation gate; this assessment remains its literature input.
