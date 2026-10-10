"""Typed contracts for EP-016 acknowledged streaming termination."""

from dataclasses import dataclass
from enum import StrEnum


STREAMING_SCHEMA = "edgepilot.streaming.v1"


class WorkerState(StrEnum):
    RUNNING = "running"
    STOP_PENDING = "stop_pending"
    VERIFYING = "verifying"
    TERMINAL_READY = "terminal_ready"
    POISONED = "poisoned"


class TerminalKind(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class EvidenceKind(StrEnum):
    TASK_MAPPED = "task_mapped"
    GENERATION_OBSERVED = "generation_observed"
    STOP_INTENDED = "stop_intended"
    SOCKET_SHUTDOWN = "socket_shutdown"
    CANCEL_LOG = "cancel_log"
    FINAL_TIMING_LOG = "final_timing_log"
    ERROR_LOG = "error_log"
    RELEASE_LOG = "release_log"
    LOG_CONTINUITY = "log_continuity"
    STREAM_TERMINAL = "stream_terminal"
    READER_EXITED = "reader_exited"
    TRACE_SAVED = "trace_saved"
    SLOT_IDLE = "slot_idle"
    CANCEL_UNSUPPORTED = "cancel_unsupported"
    EVIDENCE_FAILURE = "evidence_failure"


class StopReceiptKind(StrEnum):
    RECORDED = "recorded"
    ALREADY_PENDING = "already_pending"
    ALREADY_TERMINAL = "already_terminal"
    WRONG_REQUEST = "wrong_request"
    UNSUPPORTED_PHASE = "unsupported_phase"


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    server_pid: int
    server_started_ns: int
    configuration_sha256: str
    synchronization_audited: bool

    def __post_init__(self):
        if not self.session_id.strip() or not self.configuration_sha256.strip():
            raise ValueError("Session and configuration identity are required")
        if type(self.server_pid) is not int or self.server_pid <= 0:
            raise ValueError("Server PID must be a positive integer")
        if type(self.server_started_ns) is not int or self.server_started_ns < 0:
            raise ValueError("Server start time must be a nonnegative integer")
        if type(self.synchronization_audited) is not bool:
            raise ValueError("Synchronization audit flag must be boolean")


@dataclass(frozen=True)
class StreamingEvidence:
    kind: EvidenceKind
    request_id: str
    session: SessionIdentity
    observed_at_ns: int
    task_id: int | None = None
    log_offset: int | None = None
    query_started_ns: int | None = None
    decoded_tokens: int | None = None
    active_task_ids: tuple[int, ...] = ()
    stream_finish_reason: str | None = None
    detail: str | None = None

    def __post_init__(self):
        if not isinstance(self.kind, EvidenceKind) or not self.request_id.strip():
            raise ValueError("Typed evidence and request identity are required")
        if not isinstance(self.session, SessionIdentity):
            raise ValueError("Evidence requires session identity")
        for name in ("observed_at_ns", "log_offset", "query_started_ns", "decoded_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer or null")
        if self.task_id is not None and (type(self.task_id) is not int or self.task_id < 0):
            raise ValueError("Task ID must be a nonnegative integer or null")
        if (not isinstance(self.active_task_ids, tuple) or
                any(type(value) is not int or value < 0 for value in self.active_task_ids)):
            raise ValueError("Active task IDs must be an immutable integer tuple")


@dataclass(frozen=True)
class StopReceipt:
    request_id: str
    kind: StopReceiptKind
    recorded_at_ns: int
    detail: str | None = None


@dataclass(frozen=True)
class TerminalDecision:
    kind: TerminalKind
    request_id: str
    session_id: str
    task_id: int | None
    observed_at_ns: int
    reason: str
    reuse_authorized: bool

