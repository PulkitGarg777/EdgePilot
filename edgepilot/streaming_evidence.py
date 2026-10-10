"""Pure evidence reducer for EP-016 streaming terminal acknowledgement."""

from dataclasses import dataclass, replace

from .streaming_contracts import (
    EvidenceKind, SessionIdentity, StreamingEvidence, TerminalDecision,
    TerminalKind, WorkerState,
)


LOG_KINDS = {
    EvidenceKind.CANCEL_LOG,
    EvidenceKind.FINAL_TIMING_LOG,
    EvidenceKind.ERROR_LOG,
    EvidenceKind.RELEASE_LOG,
    EvidenceKind.LOG_CONTINUITY,
}


@dataclass(frozen=True)
class EvidenceState:
    request_id: str
    session: SessionIdentity
    state: WorkerState = WorkerState.RUNNING
    task_id: int | None = None
    generation_at_ns: int | None = None
    stop_intent_ns: int | None = None
    shutdown_ns: int | None = None
    cancel_log_offset: int | None = None
    cancel_observed_at_ns: int | None = None
    final_timing_log_offset: int | None = None
    error_log_offset: int | None = None
    release_log_offset: int | None = None
    release_observed_at_ns: int | None = None
    log_continuous_through: int | None = None
    stream_finish_reason: str | None = None
    stream_terminal_ns: int | None = None
    reader_exited_ns: int | None = None
    trace_saved_ns: int | None = None
    idle_query_started_ns: int | None = None
    idle_observed_at_ns: int | None = None
    cancel_unsupported_reason: str | None = None
    terminal: TerminalDecision | None = None
    conflicts: tuple[str, ...] = ()
    last_observed_at_ns: int = 0
    last_log_offset: int | None = None

    def __post_init__(self):
        if not self.request_id.strip() or not isinstance(self.session, SessionIdentity):
            raise ValueError("Evidence state requires request and session identity")


def _poison(state: EvidenceState, event: StreamingEvidence, reason: str) -> EvidenceState:
    conflict = state.conflicts if reason in state.conflicts else state.conflicts + (reason,)
    terminal = state.terminal or TerminalDecision(
        TerminalKind.UNCERTAIN, state.request_id, state.session.session_id,
        state.task_id, event.observed_at_ns, reason, False,
    )
    return replace(state, state=WorkerState.POISONED, terminal=terminal,
                   conflicts=conflict, last_observed_at_ns=max(state.last_observed_at_ns,
                                                               event.observed_at_ns))


def _task_matches(state: EvidenceState, event: StreamingEvidence) -> bool:
    return state.task_id is not None and event.task_id == state.task_id


def _terminal_cause(state: EvidenceState) -> tuple[TerminalKind, str] | None:
    """Return a classified cause only after the release interval is complete."""
    release = state.release_log_offset
    if release is None or state.log_continuous_through is None or state.log_continuous_through < release:
        return None
    natural = state.final_timing_log_offset is not None or state.stream_terminal_ns is not None
    failed = state.error_log_offset is not None
    if ((state.final_timing_log_offset is not None and state.final_timing_log_offset >= release) or
            (state.error_log_offset is not None and state.error_log_offset >= release)):
        return TerminalKind.UNCERTAIN, "terminal_cause_after_release"
    if natural and failed:
        return TerminalKind.UNCERTAIN, "conflicting_natural_and_error_evidence"
    if failed:
        return TerminalKind.FAILED, "server_error_before_release"
    if natural:
        if state.final_timing_log_offset is None or state.stream_terminal_ns is None:
            return None
        return TerminalKind.COMPLETED, "natural_stream_terminal_before_release"
    cancelled = (
        state.session.synchronization_audited and
        state.generation_at_ns is not None and
        state.stop_intent_ns is not None and
        state.shutdown_ns is not None and
        state.generation_at_ns <= state.stop_intent_ns <= state.shutdown_ns and
        state.cancel_log_offset is not None and
        state.cancel_observed_at_ns is not None and
        state.cancel_observed_at_ns >= state.shutdown_ns and
        state.cancel_log_offset < release
    )
    return ((TerminalKind.CANCELLED, "acknowledged_generation_cancellation") if cancelled else
            (TerminalKind.UNCERTAIN, "release_without_classified_cause"))


def _maybe_finalize(state: EvidenceState, observed_at_ns: int) -> EvidenceState:
    cause = _terminal_cause(state)
    if cause is None:
        boundary_complete = (
            state.release_observed_at_ns is not None and
            state.log_continuous_through is not None and
            state.release_log_offset is not None and
            state.log_continuous_through >= state.release_log_offset and
            state.idle_query_started_ns is not None and
            state.idle_query_started_ns > state.release_observed_at_ns and
            state.reader_exited_ns is not None and state.trace_saved_ns is not None
        )
        if boundary_complete:
            terminal = TerminalDecision(TerminalKind.UNCERTAIN, state.request_id,
                                        state.session.session_id, state.task_id,
                                        observed_at_ns, "incomplete_terminal_cause", False)
            return replace(state, state=WorkerState.POISONED, terminal=terminal,
                           conflicts=state.conflicts + ("incomplete_terminal_cause",))
        return state
    kind, reason = cause
    if kind == TerminalKind.UNCERTAIN:
        terminal = TerminalDecision(kind, state.request_id, state.session.session_id,
                                    state.task_id, observed_at_ns, reason, False)
        return replace(state, state=WorkerState.POISONED, terminal=terminal,
                       conflicts=state.conflicts + (reason,))
    if (state.release_observed_at_ns is None or state.idle_query_started_ns is None or
            state.idle_query_started_ns <= state.release_observed_at_ns or
            state.idle_observed_at_ns is None or state.reader_exited_ns is None or
            state.trace_saved_ns is None):
        return replace(state, state=WorkerState.VERIFYING)
    terminal = TerminalDecision(kind, state.request_id, state.session.session_id,
                                state.task_id, observed_at_ns, reason, True)
    return replace(state, state=WorkerState.TERMINAL_READY, terminal=terminal)


def reduce_evidence(state: EvidenceState, event: StreamingEvidence) -> EvidenceState:
    """Apply one observed fact. Ambiguity yields a terminal poisoned state."""
    if state.state in (WorkerState.TERMINAL_READY, WorkerState.POISONED):
        return state
    if event.request_id != state.request_id:
        return _poison(state, event, "wrong_request_evidence")
    if event.session != state.session:
        return _poison(state, event, "session_identity_changed")
    if event.observed_at_ns < state.last_observed_at_ns:
        return _poison(state, event, "observation_time_reversed")
    if event.kind in LOG_KINDS:
        if event.log_offset is None:
            return _poison(state, event, "log_offset_missing")
        if event.kind == EvidenceKind.LOG_CONTINUITY:
            if ((state.last_log_offset is not None and event.log_offset <= state.last_log_offset)
                    or (state.log_continuous_through is not None
                        and event.log_offset <= state.log_continuous_through)):
                return _poison(state, event, "log_order_invalid")
        else:
            if ((state.last_log_offset is not None and event.log_offset <= state.last_log_offset)
                    or (state.log_continuous_through is not None
                        and event.log_offset < state.log_continuous_through)):
                return _poison(state, event, "log_order_invalid")
            state = replace(state, last_log_offset=event.log_offset)
    state = replace(state, last_observed_at_ns=event.observed_at_ns)

    if event.kind == EvidenceKind.TASK_MAPPED:
        if event.task_id is None:
            return _poison(state, event, "mapped_task_missing")
        if state.task_id is not None and state.task_id != event.task_id:
            return _poison(state, event, "task_identity_changed")
        state = replace(state, task_id=event.task_id)
    elif event.kind == EvidenceKind.GENERATION_OBSERVED:
        if not _task_matches(state, event) or event.decoded_tokens is None or event.decoded_tokens <= 0:
            return _poison(state, event, "generation_evidence_invalid")
        state = replace(state, generation_at_ns=event.observed_at_ns)
    elif event.kind == EvidenceKind.STOP_INTENDED:
        if state.task_id is None or event.task_id != state.task_id:
            return _poison(state, event, "stop_task_mismatch")
        if state.generation_at_ns is None:
            return _poison(state, event, "stop_without_generation_evidence")
        if state.stop_intent_ns is not None:
            return _poison(state, event, "duplicate_stop_intent")
        state = replace(state, stop_intent_ns=event.observed_at_ns,
                        state=WorkerState.STOP_PENDING)
    elif event.kind == EvidenceKind.SOCKET_SHUTDOWN:
        if state.stop_intent_ns is None or state.generation_at_ns is None:
            return _poison(state, event, "shutdown_without_supported_intent")
        if state.shutdown_ns is not None:
            return _poison(state, event, "duplicate_socket_shutdown")
        state = replace(state, shutdown_ns=event.observed_at_ns,
                        state=WorkerState.VERIFYING)
    elif event.kind in (EvidenceKind.CANCEL_LOG, EvidenceKind.FINAL_TIMING_LOG,
                        EvidenceKind.ERROR_LOG, EvidenceKind.RELEASE_LOG):
        if not _task_matches(state, event):
            return _poison(state, event, "task_log_mismatch")
        field = {
            EvidenceKind.CANCEL_LOG: "cancel_log_offset",
            EvidenceKind.FINAL_TIMING_LOG: "final_timing_log_offset",
            EvidenceKind.ERROR_LOG: "error_log_offset",
            EvidenceKind.RELEASE_LOG: "release_log_offset",
        }[event.kind]
        if getattr(state, field) is not None:
            return _poison(state, event, "duplicate_" + event.kind.value)
        values = {field: event.log_offset, "state": WorkerState.VERIFYING}
        if event.kind == EvidenceKind.CANCEL_LOG:
            values["cancel_observed_at_ns"] = event.observed_at_ns
        if event.kind == EvidenceKind.RELEASE_LOG:
            values["release_observed_at_ns"] = event.observed_at_ns
        state = replace(state, **values)
    elif event.kind == EvidenceKind.LOG_CONTINUITY:
        state = replace(state, log_continuous_through=event.log_offset)
    elif event.kind == EvidenceKind.STREAM_TERMINAL:
        if event.stream_finish_reason is None:
            return _poison(state, event, "stream_finish_reason_missing")
        if state.stream_terminal_ns is not None:
            return _poison(state, event, "duplicate_stream_terminal")
        state = replace(state, stream_finish_reason=event.stream_finish_reason,
                        stream_terminal_ns=event.observed_at_ns,
                        state=WorkerState.VERIFYING)
    elif event.kind == EvidenceKind.READER_EXITED:
        state = replace(state, reader_exited_ns=event.observed_at_ns)
    elif event.kind == EvidenceKind.TRACE_SAVED:
        state = replace(state, trace_saved_ns=event.observed_at_ns)
    elif event.kind == EvidenceKind.SLOT_IDLE:
        if event.query_started_ns is None or event.active_task_ids:
            return _poison(state, event, "idle_evidence_invalid")
        state = replace(state, idle_query_started_ns=event.query_started_ns,
                        idle_observed_at_ns=event.observed_at_ns)
    elif event.kind == EvidenceKind.CANCEL_UNSUPPORTED:
        state = replace(state, cancel_unsupported_reason=event.detail or "unsupported_phase")
    elif event.kind == EvidenceKind.EVIDENCE_FAILURE:
        return _poison(state, event, event.detail or "evidence_failure")
    else:
        return _poison(state, event, "unknown_evidence_kind")
    return _maybe_finalize(state, event.observed_at_ns)
