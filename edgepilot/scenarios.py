"""Finite, fixed wall-clock scripts shared by both policies (never completion-driven)."""

from dataclasses import dataclass
from enum import StrEnum
import math


class EventKind(StrEnum):
    GOAL = "goal"
    OBSERVATION = "observation"


@dataclass(frozen=True)
class ScenarioEvent:
    at: float
    kind: EventKind
    detail: str


@dataclass(frozen=True)
class Scenario:
    name: str
    events: tuple[ScenarioEvent, ...]
    end_at: float

    def __post_init__(self):
        if not math.isfinite(self.end_at) or self.end_at < 0:
            raise ValueError("Scenario end must be finite and nonnegative")
        previous = 0.0
        for event in self.events:
            if not math.isfinite(event.at) or not previous <= event.at <= self.end_at:
                raise ValueError("Events must be finite, ordered, and within the scenario")
            if not isinstance(event.kind, EventKind):
                raise ValueError("Unknown event kind")
            previous = event.at


def goal(at: float, label: str) -> ScenarioEvent:
    return ScenarioEvent(at, EventKind.GOAL, f"Remain idle; mission revision {label}.")


def observation(at: float) -> ScenarioEvent:
    return ScenarioEvent(at, EventKind.OBSERVATION, "Irrelevant ambient telemetry update; robot remains idle.")


SCENARIOS = {
    "spaced": Scenario("spaced", (goal(0, "A"), goal(6, "B"), goal(12, "C")), 18),
    "isolated": Scenario("isolated", (goal(0, "A"), goal(1.5, "B"), goal(3, "C")), 4.5),
    "burst": Scenario("burst", (goal(0, "A"), goal(.1, "B"), goal(.2, "C"),
                                goal(.3, "D"), goal(2, "E")), 3.5),
    "irrelevant": Scenario("irrelevant", (goal(0, "A"), observation(.1), observation(.2),
                                          observation(.7), observation(1.6)), 2),
    "mixed": Scenario("mixed", (goal(0, "A"), goal(.1, "B"), observation(.15),
                                goal(.2, "C"), goal(.3, "D"), observation(.4),
                                observation(1), goal(2, "E"), observation(2.2),
                                observation(3), goal(4, "F"), observation(4.5)), 5.5),
}
