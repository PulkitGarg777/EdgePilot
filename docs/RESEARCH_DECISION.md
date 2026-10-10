# EP-011: narrow and continue

Decision date: 1 October 2026. Status: EP-011 complete; proceed to EP-015 through EP-017. Candidate-policy development remains conditional on the evidence gate below.

## Decision and research question

Continue with a bounded investigation of **when retaining, replacing, or cooperatively stopping a local planning request improves useful-response latency while preserving simulated mission success**. Focus on declared task applicability, uncertainty in remaining service time, and measured acknowledgement/restart costs. Keep the existing headless station world, Qwen3-0.6B Q8_0, and pinned llama.cpp runtime for the next stage.

This is a decision to investigate a narrower hypothesis. A distinctive scheduling contribution and an advantage over strong simple baselines remain unestablished. EP-011 closes the feasibility decision; it does not close the implementation or evaluation tasks.

## Evidence and its limits

| Input | Recorded evidence | Decision implication |
| --- | --- | --- |
| [EP-008 mission characterization](LIVE_MISSIONS.md#repeated-characterization-29-september-2026) | 40/40 missions succeeded across five scenarios. All four burst pairs favored newest-pending: mean elapsed difference -1.462 s, range -1.563 to -1.368 s. Eight pending requests were coalesced; other scenario differences were small and mixed. | Pending-work replacement has observable value in this workload. Headroom beyond newest-pending or task-aware stopping is still unknown. |
| [EP-009 overlap assessment](RESEARCH_OVERLAP.md) and [bounded source review](RELATED_WORK.md) | The existing review identifies prior event relevance, queue replacement, service-age preemption, grounded skills, and guardrails. | Treat these as established mechanisms and comparison baselines. Expand the literature review before asserting novelty. |
| [EP-010 cancellation probe](CANCELLATION_PROBE_PLAN.md) | 32/32 target trials recorded. Streaming generation cancellation and distinct correct reuse occurred in 8/8 disconnect trials. Median observed stop-to-release was 46 ms at 64 tokens and 35 ms at 256 tokens; paired avoided-time medians were 3.701 s and 2.168 s. Non-streaming disconnect cancelled 0/8 requests. | A streaming-generation adapter is feasible on this configuration. Unsupported transports/phases require a noncancelling fallback. |
| [Guarded mission integration](LIVE_MISSIONS.md) | Scripted and resident-model missions use the same state checks and conventional executor. Model plans can become stale or inapplicable while inference proceeds. | There is enough infrastructure to investigate relevance and scheduling without new hardware, perception, physics, or model training. |

The characterization consists of four dependent development pairs per scenario on one warm server. HTTP duration is a wall-time proxy; GPU compute, energy, RAM/VRAM, and controlled background load were not measured. Report superseded and censored goals alongside accepted-goal latencies; preserve null latencies rather than treating them as zero or silently dropping them.

The cancellation trials forced a 512-token synthetic generation workload. Their remaining-time savings cannot be transferred to shorter bounded mission plans. Client receipt times include polling and log/transport delays. Prompt-phase compute acknowledgement remains unresolved. The trial manifest omitted an exact runner-source snapshot; same-process calibration pins the binary, implementation DLL, and model, but does not repair that source-provenance gap. Raw batches remain local ignored artifacts; publicly accessible trace packaging is still EP-022 work.

## Why this scope

Unrestricted continuation would assume both novelty and headroom that have not been shown. An immediate pivot would discard an observable stale-work problem and a feasible generation stop mechanism. Narrow continuation permits the inexpensive relevance/baseline work needed to decide between those outcomes.

Distinguish two cases. A request whose goal version is superseded cannot install a plan under the existing contract. For that case, compare the future cost of finishing with acknowledged stop plus replacement; the simplest task-aware rule may suffice. With the same goal and changed observations, an older snapshot can still yield an applicable plan. Here the decision may need to account for the value lost by stopping. Already-spent computation is a sunk cost. Use only declared dependencies, current observations, measured history, and available progress signals; future events and unfinished plan contents are unavailable to the policy.

Preserve request/goal identity checks. An accommodating state change may preserve applicability within the current goal; it does not authorize accepting a response from a superseded goal. Any future cross-goal reuse proposal needs its own contract and evidence.

## Authorized next stage and exit criteria

1. **EP-015 — declared relevance.** Specify invalidating, harmless, and accommodating changes using known task/skill dependencies. Log the evidence and reason for each classification, including uncertainty when applicability cannot be established. Test before/after state changes and missing information deterministically. Keep final installation/dispatch checks authoritative.
2. **EP-016 — acknowledged streaming adapter.** Integrate the verified generation path with task/process identity, cancellation intent, task-scoped release on the audited synchronized path, and fresh idle evidence before reuse. Verify distinct-task reuse in real integration trials. Handle natural-completion races, late responses, worker failure, and missing acknowledgement. Missing/ambiguous evidence prevents reuse; unsupported phases finish normally where possible. Measure overhead and stop/restart costs on actual mission requests, including stream reconstruction and monitoring costs. Recheck provenance for each run.
3. **EP-017 — strong controls.** Compare finish-all FIFO, newest-pending, tuned periodic replanning, deadline admission, task-aware stopping, tuned age/service thresholds, and the symbolic planner. Model-based policies share the model, budget, state information, guardrails, executor, and fallback. Use a streaming finish control to distinguish a transport change from a scheduling change. The symbolic planner shares the mission/executor contract and establishes whether an LLM adds value to the fixed world; it does not incur an artificial inference budget.

EP-015 is the immediate next implementation task. Noncancelling baseline work can proceed before EP-016 completes. The inference model remains pinned for these comparisons; reconsider it if new tasks expose a demonstrated planning limitation, or separately under EP-021.

## Candidate and evaluation gates

Before beginning EP-018, review EP-015 through EP-017 results for unresolved tradeoffs among the simple controls: for example, one rule stops useful work under accommodating changes while another waits on invalid work. Require plausible mission workloads, working acknowledgement/fallback behavior, and an observed opportunity that exceeds measurement noise. If one simple rule already handles the tested cases adequately, close EP-018 with a documented decision not to build a candidate.

If justified, develop one bounded candidate using only decision-time information. Before freezing it for held-out evaluation, require repeated paired development comparisons with the strongest applicable controls and separate ablations of relevance, service/cost estimates, and coalescing. Report regressions as well as gains. Do not require candidate results before permitting its first implementation, and do not interpret development tuning as held-out evidence.

For development, use no observed additional mission failures or guardrail violations as a conservative screening criterion; this is not a statistical reliability guarantee. EP-019 must specify the target application, acceptable task-success loss, latency criterion, repetitions, held-out split, and uncertainty method before evaluation. Those application tradeoffs require user or supervisor agreement; none is assumed by this memo. Shared event scripts may produce different states and trajectories across policies.

If acknowledgement cannot be maintained, restrict the artifact to noncancelling characterization. If simple controls match the candidate or stop/restart cost consumes its benefit, retain the baseline/negative result and narrow the report accordingly. If symbolic planning fully accounts for mission performance, describe the world as a scheduler testbed and avoid claiming that its mission needs an LLM. Expand task scope only when there is a concrete evidence need.

## Feedback brief and claim boundary

For faculty feedback: the implemented artifact combines reproducible local inference, guarded simulated execution, and measured stale-work/cancellation behavior. The open question is whether task applicability and uncertain service costs justify anything beyond simple scheduling rules. Useful feedback concerns the target workload, strength of the controls, acceptable success/latency tradeoff, and whether characterization alone is a sufficient contribution. This document prepares that material; no outreach has occurred.

Supported claims are confined to the recorded coalescing behavior, simulated mission outcomes, and pinned streaming-generation feasibility. General speedups, energy savings, physical safety, prompt-phase cancellation, arbitrary kernel preemption, and publication novelty remain unestablished.
