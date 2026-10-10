"""Deterministic EP-016 terminal-evidence reducer tests."""

from dataclasses import replace

import pytest

from edgepilot.streaming_contracts import (
    EvidenceKind, SessionIdentity, StreamingEvidence, TerminalKind, WorkerState,
)
from edgepilot.streaming_evidence import EvidenceState, reduce_evidence


SESSION = SessionIdentity("session-1", 41, 100, "abc123", True)


def event(kind, at, *, task=7, offset=None, query=None, tokens=None,
          active=(), finish=None, session=SESSION, request="request-1", detail=None):
    return StreamingEvidence(EvidenceKind(kind), request, session, at, task,
                             offset, query, tokens, tuple(active), finish, detail)


def apply(*events, session=SESSION):
    state = EvidenceState("request-1", session)
    for item in events:
        state = reduce_evidence(state, item)
    return state


def cancellation_prefix():
    return (
        event("task_mapped", 1),
        event("generation_observed", 2, tokens=8),
        event("stop_intended", 3),
        event("socket_shutdown", 4),
        event("cancel_log", 5, offset=10),
        event("release_log", 6, offset=20),
        event("log_continuity", 7, task=None, offset=21),
    )


def cleanup(*, idle_query=7, idle_at=8):
    return (
        event("slot_idle", idle_at, task=None, query=idle_query),
        event("reader_exited", 9, task=None),
        event("trace_saved", 10, task=None),
    )


def test_matching_generation_cancel_release_and_fresh_idle_authorize_reuse():
    state = apply(*cancellation_prefix(), *cleanup())
    assert state.state == WorkerState.TERMINAL_READY
    assert state.terminal.kind == TerminalKind.CANCELLED
    assert state.terminal.reuse_authorized


def test_worker_stays_occupied_until_reader_and_trace_are_complete():
    state = apply(*cancellation_prefix(), event("slot_idle", 8, task=None, query=7))
    assert state.state == WorkerState.VERIFYING and state.terminal is None
    state = reduce_evidence(state, event("reader_exited", 9, task=None))
    assert state.state == WorkerState.VERIFYING
    state = reduce_evidence(state, event("trace_saved", 10, task=None))
    assert state.state == WorkerState.TERMINAL_READY


def test_idle_query_must_be_sent_after_release_was_observed():
    state = apply(*cancellation_prefix(), *cleanup(idle_query=6))
    assert state.state == WorkerState.VERIFYING and state.terminal is None
    state = reduce_evidence(state, event("slot_idle", 11, task=None, query=10))
    assert state.state == WorkerState.TERMINAL_READY


@pytest.mark.parametrize("mutation, reason", [
    (event("task_mapped", 1, task=8), "task_identity_changed"),
    (event("cancel_log", 5, task=8, offset=10), "task_log_mismatch"),
    (event("cancel_log", 5, offset=None), "log_offset_missing"),
    (event("slot_idle", 8, task=None, query=None), "idle_evidence_invalid"),
    (event("slot_idle", 8, task=None, query=7, active=(99,)), "idle_evidence_invalid"),
    (event("evidence_failure", 5, task=None, detail="queue_overflow"), "queue_overflow"),
])
def test_conflicting_or_lost_evidence_poison_session(mutation, reason):
    prefix = [event("task_mapped", 1), event("generation_observed", 2, tokens=8),
              event("stop_intended", 3), event("socket_shutdown", 4)]
    if mutation.kind == EvidenceKind.TASK_MAPPED:
        prefix = [prefix[0]]
    state = apply(*prefix, mutation)
    assert state.state == WorkerState.POISONED
    assert state.terminal.kind == TerminalKind.UNCERTAIN
    assert reason in state.conflicts
    assert not state.terminal.reuse_authorized


def test_process_identity_change_poison_session_even_with_same_pid():
    restarted = replace(SESSION, server_started_ns=101)
    state = apply(event("task_mapped", 1),
                  event("generation_observed", 2, tokens=8, session=restarted))
    assert state.state == WorkerState.POISONED
    assert "session_identity_changed" in state.conflicts


def test_stop_requires_current_task_mapping_and_positive_generation_evidence():
    without_mapping = apply(event("stop_intended", 1))
    assert "stop_task_mismatch" in without_mapping.conflicts
    without_generation = apply(event("task_mapped", 1), event("stop_intended", 2))
    assert "stop_without_generation_evidence" in without_generation.conflicts


def test_unsupported_phase_does_not_latch_stop_or_poison_healthy_request():
    state = apply(event("task_mapped", 1),
                  event("cancel_unsupported", 2, detail="prefill_unconfirmed"))
    assert state.state == WorkerState.RUNNING
    assert state.stop_intent_ns is None
    assert state.cancel_unsupported_reason == "prefill_unconfirmed"


def test_natural_completion_race_wins_over_cleanup_cancel():
    events = (
        event("task_mapped", 1), event("generation_observed", 2, tokens=8),
        event("stop_intended", 3), event("socket_shutdown", 4),
        event("final_timing_log", 5, offset=9),
        event("cancel_log", 6, offset=10),
        event("stream_terminal", 7, task=None, finish="stop"),
        event("release_log", 8, offset=20),
        event("log_continuity", 9, task=None, offset=21),
        event("slot_idle", 10, task=None, query=9),
        event("reader_exited", 11, task=None), event("trace_saved", 12, task=None),
    )
    state = apply(*events)
    assert state.state == WorkerState.TERMINAL_READY
    assert state.terminal.kind == TerminalKind.COMPLETED
    assert state.terminal.reason == "natural_stream_terminal_before_release"


def test_natural_completion_requires_stream_terminal_and_final_timing():
    events = (
        event("task_mapped", 1), event("generation_observed", 2, tokens=8),
        event("final_timing_log", 3, offset=9), event("release_log", 4, offset=20),
        event("log_continuity", 5, task=None, offset=21),
        event("slot_idle", 6, task=None, query=5),
        event("reader_exited", 7, task=None), event("trace_saved", 8, task=None),
    )
    state = apply(*events)
    assert state.state == WorkerState.POISONED
    assert state.terminal.reason == "incomplete_terminal_cause"


def test_known_server_error_can_release_cleanly_without_becoming_cancellation():
    state = apply(
        event("task_mapped", 1), event("error_log", 2, offset=10),
        event("release_log", 3, offset=20), event("log_continuity", 4, task=None, offset=21),
        event("slot_idle", 5, task=None, query=4),
        event("reader_exited", 6, task=None), event("trace_saved", 7, task=None),
    )
    assert state.state == WorkerState.TERMINAL_READY
    assert state.terminal.kind == TerminalKind.FAILED
    assert state.terminal.reuse_authorized


def test_release_without_audited_cause_is_uncertain_and_forbids_reuse():
    unaudited = replace(SESSION, synchronization_audited=False)
    events = tuple(replace(item, session=unaudited) for item in cancellation_prefix() + cleanup())
    state = apply(*events, session=unaudited)
    assert state.state == WorkerState.POISONED
    assert state.terminal.kind == TerminalKind.UNCERTAIN
    assert state.terminal.reason == "release_without_classified_cause"


def test_cancel_observed_before_socket_shutdown_cannot_acknowledge_that_stop():
    state = apply(
        event("task_mapped", 1), event("generation_observed", 2, tokens=8),
        event("stop_intended", 3), event("cancel_log", 4, offset=10),
        event("socket_shutdown", 5), event("release_log", 6, offset=20),
        event("log_continuity", 7, task=None, offset=21),
    )
    assert state.state == WorkerState.POISONED
    assert state.terminal.reason == "release_without_classified_cause"


def test_duplicate_task_log_poison_session():
    state = apply(
        event("task_mapped", 1), event("generation_observed", 2, tokens=8),
        event("stop_intended", 3), event("socket_shutdown", 4),
        event("cancel_log", 5, offset=10), event("cancel_log", 6, offset=11),
    )
    assert state.state == WorkerState.POISONED
    assert "duplicate_cancel_log" in state.conflicts


def test_log_offsets_and_observation_times_must_be_monotonic():
    bad_log = apply(event("task_mapped", 1), event("error_log", 2, offset=10),
                    event("release_log", 3, offset=9))
    assert "log_order_invalid" in bad_log.conflicts
    bad_time = apply(event("task_mapped", 2), event("generation_observed", 1, tokens=2))
    assert "observation_time_reversed" in bad_time.conflicts


def test_next_record_may_start_at_prior_exclusive_continuity_boundary():
    state = apply(
        event("task_mapped", 1), event("generation_observed", 2, tokens=8),
        event("stop_intended", 3), event("socket_shutdown", 4),
        event("log_continuity", 5, task=None, offset=10),
        event("cancel_log", 6, offset=10),
        event("release_log", 7, offset=20),
        event("log_continuity", 8, task=None, offset=30),
        event("slot_idle", 9, task=None, query=8),
        event("reader_exited", 10, task=None), event("trace_saved", 11, task=None),
    )
    assert state.state == WorkerState.TERMINAL_READY
    assert state.terminal.kind == TerminalKind.CANCELLED


def test_poisoned_state_ignores_late_evidence_and_cannot_recover():
    state = apply(event("task_mapped", 1), event("evidence_failure", 2, task=None))
    terminal = state.terminal
    state = reduce_evidence(state, event("slot_idle", 3, task=None, query=2))
    assert state.state == WorkerState.POISONED and state.terminal == terminal
