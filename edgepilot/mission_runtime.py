"""Wall-clock scene coordination with FIFO or newest-pending model planning."""

from collections import Counter, deque
from dataclasses import asdict
import math
from typing import Callable

from .contracts import FailureKind, Policy
from .robot.contracts import EventKind, NS, WorldEvent, integer
from .robot.model import ModelRequest, ModelResult, ModelWorker, decode_plan, make_request
from .robot.relevance import RELEVANCE_SCHEMA, RequestDependencies, classify_relevance
from .robot.world import World
from .simulation import SCENARIOS as SCRIPTED_SCENARIOS

TRACE_SCHEMA = "edgepilot.mission.v1"
SCENARIOS = {**SCRIPTED_SCENARIOS, "goal-burst": (
    WorldEvent(NS//10, EventKind.GOAL, required_stations=("station_b",)),
    WorldEvent(NS//5, EventKind.GOAL, required_stations=("station_a",)),
    WorldEvent(3*NS//10, EventKind.GOAL, required_stations=("station_a", "station_b")),
)}


class MissionEpisode:
    def __init__(self, policy: Policy, worker: ModelWorker, events: tuple[WorldEvent, ...] = (), *,
                 emit: Callable[..., None] | None = None, deadline_ns: int = 45*NS,
                 max_tokens: int = 384, model_id: str = "edgepilot", max_requests: int = 24,
                 request_ttl_ns: int | None = None, validation_clock: Callable[[], int] | None = None,
                 validation_wall_clock: Callable[[], int] | None = None,
                 cancel_on_goal_replacement: bool = False):
        integer(deadline_ns, "episode deadline", 1)
        integer(max_requests, "request budget", 1)
        integer(max_tokens, "output budget", 1)
        if request_ttl_ns is not None:
            integer(request_ttl_ns, "request lifetime", 1)
        if any(a.at_ns > b.at_ns for a, b in zip(events, events[1:])):
            raise ValueError("Scenario events must be chronological")
        self.policy, self.worker = Policy(policy), worker
        self.emit = emit or (lambda **e: None)
        self.world = World(self.emit, validation_clock=validation_clock, validation_wall_clock=validation_wall_clock)
        self.events, self.event_index = events, 0
        self.deadline_ns, self.request_ttl_ns = deadline_ns, request_ttl_ns
        self.max_tokens, self.model_id, self.max_requests = max_tokens, model_id, max_requests
        self.pending: deque[ModelRequest] = deque()
        self.active: ModelRequest | None = None
        self.requests = 0
        self.dispatched = 0
        self.terminals: list[dict] = []
        self.goal_metrics: dict[int, dict] = {}
        self._record_goal(0)
        self.stop_reason: str | None = None
        self.done = False
        self.observation_times: list[int] = []
        self.worker_wall_ns = 0
        self.rejected_worker_wall_ns = 0
        self.max_pending = 0
        self.http_times_ns: list[int] = []
        self.dependencies: dict[str, RequestDependencies] = {}
        self.relevance_counts: Counter = Counter()
        self.cancel_on_goal_replacement = cancel_on_goal_replacement
        if cancel_on_goal_replacement and not callable(getattr(worker, "request_cancel", None)):
            raise ValueError("Controlled cancellation requires a cancellable worker")
        self.cancel_candidates: dict[str, tuple[ModelRequest, int, str]] = {}
        self.cancel_resolved: set[tuple[str, int]] = set()
        self.cancel_last_attempt_ns: dict[tuple[str, int], int] = {}
        self.cancel_receipt_counts: Counter = Counter()
        self.acknowledged_cancellations = 0

    def _record_relevance(self, request: ModelRequest, trigger: str):
        decision = classify_relevance(self.dependencies[request.request_id], self.world.snapshot())
        self.relevance_counts[decision.classification.value] += 1
        self.world.log("request_relevance", trigger=trigger, decision=decision.to_dict())
        return decision

    def _request_controlled_cancel(self, request: ModelRequest, goal_version: int, reason: str):
        key = (request.request_id, goal_version)
        if key in self.cancel_resolved or self.cancel_last_attempt_ns.get(key) == self.world.now_ns:
            return
        self.cancel_last_attempt_ns[key] = self.world.now_ns
        receipt = self.worker.request_cancel(request.request_id, reason, self.world.now_ns)
        self.cancel_receipt_counts[receipt.kind.value] += 1
        if receipt.kind.value != "unsupported_phase":
            self.cancel_resolved.add(key)
        self.world.log("cancellation_requested", request_id=request.request_id,
                       request_goal_version=request.snapshot.mission.version,
                       current_goal_version=self.world.mission.version,
                       reason=reason, receipt=asdict(receipt))

    def _record_goal(self, scheduled_at_ns: int):
        mission = self.world.mission
        self.goal_metrics[mission.version] = {
            "goal_version": mission.version, "created_at_ns": mission.created_at_ns,
            "scheduled_at_ns": scheduled_at_ns, "accepted_at_ns": None, "latency_ns": None,
            "scheduled_latency_ns": None, "status": "pending", "superseded_at_ns": None,
        }

    def _terminal(self, request: ModelRequest, state: str, error: str | None = None):
        record = {"request_id": request.request_id, "request_goal_version": request.snapshot.mission.version,
                  "request_snapshot_id": request.snapshot.snapshot_id, "state": state,
                  "decided_at_ns": self.world.now_ns, "error": error}
        self.terminals.append(record)
        self.world.log("request_terminal", **record)
        self.dependencies.pop(request.request_id, None)
        self.cancel_candidates.pop(request.request_id, None)

    def _stop(self, reason: str):
        if self.stop_reason:
            return
        self.stop_reason = reason
        self.world.terminate("timed_out" if reason == "episode_deadline" else "planner_failed")
        while self.pending:
            self._terminal(self.pending.popleft(), reason)
        self.world.log("admission_stopped", reason=reason,
                       active_request_id=self.active.request_id if self.active else None)

    def _enqueue(self, trigger: str):
        if self.stop_reason or not self.world.mission_feasible() or self.world.outcome != "running":
            return
        if self.requests >= self.max_requests:
            self._stop("request_budget_exhausted")
            return
        self.requests += 1
        ttl = self.world.now_ns+self.request_ttl_ns if self.request_ttl_ns is not None else None
        request = make_request(f"model-{self.requests:04d}", self.world.now_ns, self.world.snapshot(),
                               self.model_id, self.max_tokens, ttl)
        if self.policy == Policy.NEWEST_PENDING:
            while self.pending:
                replaced = self.pending.popleft()
                self._terminal(replaced, "coalesced_before_inference")
                self.world.log("pending_replaced", discarded_request_id=replaced.request_id,
                               replacement_request_id=request.request_id)
        self.pending.append(request)
        self.dependencies[request.request_id] = RequestDependencies(request.request_id, request.snapshot)
        self.max_pending = max(self.max_pending, len(self.pending))
        self.world.log("request_queued", request=asdict(request), trigger=trigger, pending_count=len(self.pending),
                       dependencies=self.dependencies[request.request_id].to_dict())

    def _consume(self, result: ModelResult):
        request = self.active
        if request is None or result.request_id != request.request_id:
            raise RuntimeError("Worker result does not match the occupied slot")
        if not request.created_at_ns <= result.started_at_ns <= result.finished_at_ns <= self.world.now_ns:
            raise RuntimeError("Worker returned invalid monotonic timestamps")
        self._record_relevance(request, "before_response")
        elapsed = result.finished_at_ns-result.started_at_ns
        self.http_times_ns.append(elapsed)
        self.worker_wall_ns += elapsed
        self.world.log("model_response", result=asdict(result), http_elapsed_ns=elapsed)
        error = result.error
        if result.error is not None or result.failure_kind is not None:
            state = "http_timed_out" if result.failure_kind == FailureKind.TIMEOUT else "worker_failed"
        elif result.termination == "cancelled":
            state = "cancelled_acknowledged"
            self.acknowledged_cancellations += 1
        elif self.stop_reason:
            state = self.stop_reason
        elif request.snapshot.mission.version != self.world.mission.version:
            state = "rejected_stale_goal"
        elif request.deadline_ns is not None and self.world.now_ns >= request.deadline_ns:
            state = "request_expired"
        else:
            try:
                plan = decode_plan(result.response, request)
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                state, error = "rejected_invalid_plan", f"{type(exc).__name__}: {exc}"
                if self.world.ready_for_plan():
                    self.world.fallback(state)
            else:
                self.world.expect(request.request_id, request.snapshot.snapshot_id)
                if self.world.install(plan):
                    state = "accepted"
                    record = self.goal_metrics[self.world.mission.version]
                    if record["accepted_at_ns"] is None:
                        record.update(accepted_at_ns=self.world.now_ns, status="accepted",
                                      latency_ns=self.world.now_ns-record["created_at_ns"],
                                      scheduled_latency_ns=self.world.now_ns-record["scheduled_at_ns"])
                else:
                    state = "rejected_" + self.world.last_validation.reason.value
                self.world.expected_request_id = None
        self._terminal(request, state, error)
        if state != "accepted":
            self.rejected_worker_wall_ns += elapsed
        self.active = None
        if result.server_state_uncertain or result.failure_kind == FailureKind.TIMEOUT:
            self._stop("transport_state_uncertain")

    def _dispatch_request(self):
        while self.active is None and self.pending and not self.stop_reason:
            request = self.pending.popleft()
            if request.deadline_ns is not None and self.world.now_ns >= request.deadline_ns:
                self._terminal(request, "request_expired")
                continue
            self.active = request
            self.dispatched += 1
            self.world.log("request_dispatched", request_id=request.request_id,
                           request_goal_version=request.snapshot.mission.version,
                           queue_wait_ns=self.world.now_ns-request.created_at_ns, pending_count=len(self.pending))
            try:
                self.worker.submit(request)
            except Exception as exc:
                self._consume(ModelResult(request.request_id, self.world.now_ns, self.world.now_ns,
                                          error=f"{type(exc).__name__}: {exc}", failure_kind=FailureKind.ERROR,
                                          server_state_uncertain=True))

    def step(self, now_ns: int) -> bool:
        integer(now_ns, "clock")
        if now_ns < self.world.now_ns:
            raise ValueError("Clock cannot go backwards")
        if self.done:
            return True
        if now_ns >= self.deadline_ns and not self.stop_reason:
            self.world.advance(now_ns, complete_skills=False)
            self._stop("episode_deadline")
        elif not self.stop_reason:
            # Preserve every goal version, even if a delayed loop sees a batch.
            # Completion is deferred until ALL due updates have been applied.
            while self.event_index < len(self.events) and self.events[self.event_index].at_ns <= now_ns:
                event = self.events[self.event_index]
                old_goal = self.world.mission.version
                old_availability = {s.name: s.available for s in self.world.stations}
                self.world.advance(now_ns, (event,), complete_skills=False)
                self.event_index += 1
                for request in ([self.active] if self.active else []) + list(self.pending):
                    decision = self._record_relevance(request, event.kind.value)
                    if (self.cancel_on_goal_replacement and request is self.active and
                            event.kind == EventKind.GOAL and
                            decision.classification.value == "invalidating"):
                        self.cancel_candidates[request.request_id] = (
                            request, self.world.mission.version, "goal_replaced")
                if event.kind == EventKind.GOAL:
                    previous = self.goal_metrics[old_goal]
                    previous["superseded_at_ns"] = now_ns
                    if previous["accepted_at_ns"] is None:
                        previous["status"] = "superseded"
                    self._record_goal(event.at_ns)
                    self._enqueue("goal_changed")
                elif (event.kind == EventKind.AVAILABILITY and old_availability[event.station] != event.available
                      and event.station in set(self.world.mission.required_stations)-self.world.inspected
                      and self.world.ready_for_plan()):
                    self._enqueue("required_station_changed")
                if self.stop_reason:
                    break
            self.world.advance(now_ns, complete_skills=not bool(self.stop_reason))
        else:
            self.world.advance(now_ns, complete_skills=False)
        if self.active:
            result = self.worker.poll(now_ns)
            if result is not None:
                self._consume(result)
        if not self.stop_reason and self.active:
            candidate = self.cancel_candidates.get(self.active.request_id)
            if candidate is not None:
                self._request_controlled_cancel(*candidate)
        if not self.stop_reason:
            outstanding = ([self.active] if self.active else []) + list(self.pending)
            if self.world.ready_for_plan() and not any(
                    r.snapshot.mission.version == self.world.mission.version for r in outstanding):
                if self.world.mission_feasible():
                    self._enqueue("initial_or_refresh")
                else:
                    self.world.fallback("required_station_unavailable")
            self._dispatch_request()
            self.world.dispatch()
        gap = now_ns-self.observation_times[-1] if self.observation_times else None
        self.observation_times.append(now_ns)
        self.world.log("observation", snapshot=asdict(self.world.snapshot()), gap_ns=gap,
                       worker_occupied=self.active is not None, pending_count=len(self.pending))
        schedule_done = self.event_index == len(self.events)
        self.done = self.active is None and not self.pending and (
            self.stop_reason is not None or (schedule_done and self.world.outcome == "succeeded"))
        if self.done:
            for record in self.goal_metrics.values():
                if record["status"] == "pending":
                    record["status"] = "censored_" + (self.stop_reason or "no_accepted_plan")
            self.world.log("episode_finished", reason=self.stop_reason or "completed")
        return self.done

    def summary(self) -> dict:
        def timings(values):
            samples = sorted(values)
            def percentile(fraction):
                return samples[max(0, math.ceil(len(samples)*fraction)-1)] if samples else None
            return {"samples": len(samples), "zero_samples": samples.count(0),
                    "total_ns": sum(samples) if samples else None,
                    "median_ns": percentile(.5), "p95_ns": percentile(.95),
                    "max_ns": max(samples) if samples else None}
        return {
            "trace_schema": TRACE_SCHEMA, "policy": self.policy.value, "finished": self.done,
            "episode_outcome": self.stop_reason or ("completed" if self.done else "running"),
            "mission_outcome": self.world.outcome, "elapsed_ns": self.world.now_ns,
            "final_state": asdict(self.world.snapshot()), "mission_outcomes": self.world.mission_records,
            "goals": list(self.goal_metrics.values()), "requests_created": self.requests,
            "requests_dispatched": self.dispatched, "max_pending_count": self.max_pending,
            "terminal_counts": dict(Counter(t["state"] for t in self.terminals)),
            "actions_completed": self.world.actions_completed, "actions_stopped": self.world.actions_stopped,
            "validation_rejections": self.world.validation_rejections,
            "validation_cpu": timings(self.world.validation_cpu_samples_ns),
            "validation_wall": timings(self.world.validation_wall_samples_ns),
            "validation_timing_note": "Zero CPU samples may be below clock granularity; they do not imply zero cost. "
                                      "Wall duration includes scheduling effects and clock overhead; it is not CPU time.",
            "observations": len(self.observation_times),
            "max_observation_gap_ns": max((b-a for a,b in zip(self.observation_times, self.observation_times[1:])), default=None),
            "worker_wall_ns": self.worker_wall_ns, "rejected_worker_wall_ns": self.rejected_worker_wall_ns,
            "http_elapsed_ns": self.http_times_ns, "unprocessed_events": len(self.events)-self.event_index,
            "relevance_schema": RELEVANCE_SCHEMA, "relevance_decision_counts": dict(self.relevance_counts),
            "controlled_cancellation_enabled": self.cancel_on_goal_replacement,
            "cancellation_receipt_counts": dict(self.cancel_receipt_counts),
            "acknowledged_cancellations": self.acknowledged_cancellations,
            "note": "Accepted plan latency is separate from completed-mission success. Superseded goals have null latency. "
                    "Rejected HTTP wall time is a proxy, not GPU compute or energy. Controlled cancellation is "
                    "counted only after the worker reports an acknowledged cancelled terminal.",
        }
