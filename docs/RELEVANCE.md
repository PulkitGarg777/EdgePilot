# EP-015 architecture: declared request relevance

The classifier describes the applicability of a declared mission request, not the validity of an unseen model plan. It uses the request snapshot and observed current state only. Its classifications never install a plan, cancel inference, alter queue admission, or bypass validation.

## Contract and data flow

`edgepilot/robot/relevance.py` owns immutable dependency, evidence, and decision records plus a pure classifier. Dependencies are derived at request creation from the mission's required stations, robot position, executor occupancy, and inspection credit. The declared skill set is the existing navigation, inspection, wait, and return-home set; navigation/inspection depend on station presence, availability, and position. Returning home depends on known robot position. No future events, partial generated actions, or service-time estimates enter this contract.

`MissionEpisode` records declarations when a request is queued. It compares every outstanding request against each delivered external update, before same-step model completion is consumed. Comparisons always use the original request snapshot, so a later telemetry update cannot erase a still-invalidating change. A decision is also recorded immediately before consuming a response, covering ordinary executor progress. Request IDs, source/current snapshot IDs, goal versions, evidence, reasons, and uncertainty are retained in additive `request_relevance` trace events. Existing trace readers and FIFO/newest-pending behavior remain compatible.

## Classification rules

| Classification | Evidence | Meaning |
| --- | --- | --- |
| invalidating | Goal replaced, or an outstanding required station is known unavailable | The declared request cannot currently serve its original mission assumptions |
| harmless | Only observation identity/time or stations outside the declared mission change | No declared dependency changed |
| accommodating | Same-goal inspection credit increases or required availability is restored, with other dependencies established | Task progress or restored feasibility may preserve useful work |
| unknown | Missing position/station/availability, moved robot/station, busy executor, lost inspection credit, malformed same-version mission identity, or reversed snapshot order | Applicability cannot be established from this contract |

Known invalidation takes precedence over uncertainty, then unknown over accommodating, then harmless. All evidence remains visible, including uncertainty alongside invalidation. Missing dependencies are uncertain even if missing in both snapshots. A currently unavailable remaining target is invalidating even if it was unavailable at submission. Completed targets remain declared dependencies: their availability changes are uncertain because an unseen plan might revisit them. Missing or moved station information is also uncertain for completed targets.

Accommodating is a task-level assessment, not a promise that every possible old plan remains executable. A model can emit an optional station visit or start with an inspection whose preconditions changed. Current plan installation and per-skill dispatch checks remain authoritative. Goal identity is strict: an otherwise identical replacement still invalidates the old request. No cross-goal reuse is authorized.

## Implementation and verification sequence

1. Add typed declarations and a deterministic classifier with JSON-serializable evidence.
2. Add runtime trace integration without changing scheduling decisions.
3. Test missing state, each classification, mixed evidence, same-version corruption, reversals, recovery relative to the original snapshot, and same-step update/completion ordering.
4. Run the complete offline suite and a deterministic mission trace inspection; retain model/GPU experiments for EP-016.

EP-016 will consume this contract when implementing acknowledged streaming-generation cancellation. EP-017 can use it for task-aware controls after those controls are separately implemented and measured.
