"""Immutable messages. All timestamps are seconds on an episode-relative monotonic clock."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class Policy(StrEnum):
    FIFO = "fifo"
    NEWEST_PENDING = "newest-pending"


class TerminalState(StrEnum):
    ACCEPTED = "accepted"
    STALE = "rejected_stale_goal"
    INVALID = "rejected_invalid_plan"
    COALESCED = "coalesced_before_inference"
    FAILED = "failed"
    TIMED_OUT = "http_timed_out"
    DEADLINE = "episode_deadline"
    ABORTED = "episode_aborted"


class FailureKind(StrEnum):
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class Observation:
    version: int
    observed_at: float
    detail: str


@dataclass(frozen=True)
class Goal:
    version: int
    created_at: float
    scheduled_at: float
    instruction: str


@dataclass(frozen=True)
class Request:
    request_id: str
    created_at: float
    goal: Goal
    observation: Observation


@dataclass(frozen=True)
class Result:
    request_id: str
    goal_version: int
    started_at: float
    finished_at: float
    response: Any = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    # HTTP transport failures may leave the server still computing. End this
    # episode instead of submitting more work to an uncertain server slot.
    server_state_uncertain: bool = False


@dataclass(frozen=True)
class Terminal:
    request_id: str
    goal_version: int
    state: TerminalState
    decided_at: float
    error: str | None = None


class Worker(Protocol):
    def submit(self, request: Request, now: float) -> None: ...

    def poll(self, now: float) -> Result | None: ...

    def close(self) -> None: ...
