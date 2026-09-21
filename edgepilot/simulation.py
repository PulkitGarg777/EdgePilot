"""Run the headless station world with a scripted planner, without an LLM."""

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable
import uuid

from .__main__ import ROOT, configure_paths
from .robot.contracts import EventKind, NS, TRACE_SCHEMA, WorldEvent, integer
from .robot.planner import PlanningRequest, ScriptedPlanner
from .robot.world import World


SCENARIOS = {
    "nominal": (),
    "station-unavailable": (
        WorldEvent(NS//2, EventKind.AVAILABILITY, "station_a", False),
        WorldEvent(3*NS//2, EventKind.AVAILABILITY, "station_a", True),
    ),
    "goal-replacement": (WorldEvent(7*NS//10, EventKind.GOAL, required_stations=("station_b",)),),
    "irrelevant": (WorldEvent(NS//5, EventKind.TELEMETRY), WorldEvent(3*NS//5, EventKind.TELEMETRY)),
    "blocked": (WorldEvent(0, EventKind.AVAILABILITY, "station_a", False),),
}


class Episode:
    """Deterministic coordinator. Caller owns time; step never waits for planning."""

    def __init__(self, events: tuple[WorldEvent, ...] = (), *, planner: ScriptedPlanner | None = None,
                 deadline_ns: int = 20*NS, emit: Callable[..., None] | None = None):
        integer(deadline_ns, "deadline", 1)
        if any(a.at_ns > b.at_ns for a, b in zip(events, events[1:])):
            raise ValueError("External events must be in chronological order")
        self.emit = emit or (lambda **event: None)
        self.world = World(self.emit)
        self.planner = planner or ScriptedPlanner()
        self.events, self.deadline_ns = events, deadline_ns
        self.event_index = 0
        self.requests = 0
        self.plans_installed = 0
        self.plans_rejected = 0
        self.observation_times: list[int] = []
        self.done = False

    def step(self, now_ns: int) -> bool:
        integer(now_ns, "clock")
        if now_ns < self.world.now_ns:
            raise ValueError("Clock cannot go backwards")
        if self.done:
            return True
        due = []
        if now_ns < self.deadline_ns:
            while self.event_index < len(self.events) and self.events[self.event_index].at_ns <= now_ns:
                due.append(self.events[self.event_index])
                self.event_index += 1
        self.world.advance(now_ns, tuple(due), deadline=now_ns >= self.deadline_ns)
        if self.world.outcome != "timed_out":
            result = self.planner.poll(now_ns)
            if result is not None:
                self.world.log("scripted_result", result=asdict(result))
                if result.error:
                    self.world.terminate("planner_failed")
                elif self.world.install(result.plan):
                    self.plans_installed += 1
                else:
                    self.plans_rejected += 1
                self.world.expected_request_id = None
            if self.world.ready_for_plan() and self.planner.pending is None:
                if self.world.mission_feasible():
                    self.requests += 1
                    request = PlanningRequest(f"script-{self.requests:04d}", now_ns, self.world.snapshot())
                    self.world.expect(request.request_id)
                    self.planner.submit(request)
                    self.world.log("scripted_request", request=asdict(request), delay_ns=self.planner.delay_ns)
                else:
                    self.world.fallback("required_station_unavailable")
            self.world.dispatch()
        gap = now_ns-self.observation_times[-1] if self.observation_times else None
        self.observation_times.append(now_ns)
        self.world.log("observation", gap_ns=gap, snapshot=asdict(self.world.snapshot()))
        self.done = self.world.outcome in ("timed_out", "planner_failed") or (
            self.world.outcome == "succeeded" and self.event_index == len(self.events) and self.planner.pending is None)
        if self.done:
            # This worker is a clock-driven test double. Abandoning its synthetic
            # result is NOT an inference stop/cancellation protocol.
            if self.planner.pending is not None:
                self.world.log("scripted_request_abandoned", request_id=self.planner.pending.request_id)
                self.planner.pending = None
            self.world.log("episode_finished", outcome=self.world.outcome)
        return self.done

    def summary(self) -> dict:
        state = self.world.snapshot()
        return {
            "trace_schema": TRACE_SCHEMA, "outcome": self.world.outcome, "finished": self.done,
            "elapsed_ns": self.world.now_ns, "final_state": asdict(state),
            "mission_outcomes": self.world.mission_records,
            "actions_completed": self.world.actions_completed, "actions_stopped": self.world.actions_stopped,
            "validation_rejections": self.world.validation_rejections, "planning_requests": self.requests,
            "plans_installed": self.plans_installed, "plans_rejected": self.plans_rejected,
            "observations": len(self.observation_times),
            "max_observation_gap_ns": max((b-a for a, b in zip(self.observation_times, self.observation_times[1:])), default=None),
            "unprocessed_events": len(self.events)-self.event_index, "model_used": False,
            "note": "Headless constant-speed simulation with a scripted planner. No model inference, "
                    "GPU cancellation, collision physics, or physical safety claim.",
        }


def seconds_to_ns(value: str) -> int:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 <= seconds <= 3600:
        raise argparse.ArgumentTypeError("Seconds must be finite and between 0 and 3600")
    return round(seconds*NS)


def run(scenario: str, clock_mode: str, planner_delay_ns: int, deadline_ns: int, tick_ns: int) -> dict:
    integer(tick_ns, "tick", 1)
    integer(deadline_ns, "deadline", 1)
    events = SCENARIOS[scenario]
    configure_paths()
    run_dir = ROOT / "runs" / ("sim-" + time.strftime("%Y%m%d-%H%M%S") + "-" + scenario + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__)] + sorted((ROOT / "edgepilot/robot").glob("*.py"))
    metadata = {
        "trace_schema": TRACE_SCHEMA, "scenario": scenario, "clock_mode": clock_mode,
        "test_kind": "scripted_" + clock_mode, "planner": "scripted-inspection-v1", "model_used": False,
        "planner_delay_ns": planner_delay_ns, "deadline_ns": deadline_ns, "tick_ns": tick_ns,
        "scenario_events": [asdict(e) for e in events], "python": sys.version, "executable": sys.executable,
        "room": {"bounds": [0, 4, 0, 4], "home": [0, 0], "station_a": [2, 0], "station_b": [2, 2],
                 "speed_units_per_second": World.speed, "inspection_ns": World.inspection_ns},
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout,
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as log:
        def emit(**event):
            log.write(json.dumps({"trace_schema": TRACE_SCHEMA, **event}) + "\n")
            log.flush()
        episode = Episode(events, planner=ScriptedPlanner(planner_delay_ns), deadline_ns=deadline_ns, emit=emit)
        start_ns = time.perf_counter_ns()
        now_ns = 0
        while not episode.step(now_ns):
            if clock_mode == "wall":
                time.sleep(tick_ns/NS)
                now_ns = time.perf_counter_ns()-start_ns
            else:
                now_ns += tick_ns
    summary = {**episode.summary(), "clock_mode": clock_mode, "test_kind": metadata["test_kind"],
               "run_directory": str(run_dir)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="nominal")
    parser.add_argument("--clock", choices=("wall", "virtual"), default="wall")
    parser.add_argument("--planner-delay", type=seconds_to_ns, default=0, help="Synthetic planning delay in seconds")
    parser.add_argument("--deadline", type=seconds_to_ns, default=20*NS)
    parser.add_argument("--tick", type=seconds_to_ns, default=NS//20)
    args = parser.parse_args()
    if args.deadline == 0 or args.tick == 0:
        parser.error("Deadline and tick must be positive")
    summary = run(args.scenario, args.clock, args.planner_delay, args.deadline, args.tick)
    print(json.dumps(summary, indent=2))
    return int(summary["outcome"] != "succeeded")


if __name__ == "__main__":
    raise SystemExit(main())
