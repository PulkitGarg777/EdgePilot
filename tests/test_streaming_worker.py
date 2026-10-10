"""Synchronized offline tests for the acknowledged streaming worker facade."""

from dataclasses import replace
import json

from edgepilot.robot.model import make_request
from edgepilot.robot.world import World
from edgepilot.streaming_contracts import (
    EvidenceKind, SessionIdentity, StopReceiptKind, StreamingEvidence,
)
from edgepilot.streaming_transport import StreamingTransportResult
from edgepilot.streaming_worker import AcknowledgedStreamingWorker


SESSION = SessionIdentity("session-1", 41, 100, "abc", True)


def raw_response(content='{"actions":[{"skill":"return_home"}]}'):
    records = [
        {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 4}},
    ]
    body = "".join("data: " + json.dumps(item) + "\n\n" for item in records).encode() + b"data: [DONE]\n\n"
    return (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: " +
            str(len(body)).encode() + b"\r\n\r\n" + body)


class FakeMonitor:
    def __init__(self):
        self.session = SESSION
        self.events = []
        self.active = None
        self.closed = False

    def observe_request(self, request_id):
        assert self.active is None
        self.active = request_id

    def clear_request(self, request_id):
        assert self.active == request_id
        self.active = None

    def drain(self, limit=256):
        result, self.events = self.events[:limit], self.events[limit:]
        return result

    def close(self, timeout_s=2):
        self.closed = True


class FakeCall:
    def __init__(self, result=None):
        self.transport_result = result
        self.started = False
        self.disconnects = 0

    def start(self):
        self.started = True

    @property
    def done(self):
        return self.transport_result is not None

    def result(self):
        assert self.done
        return self.transport_result

    def request_disconnect(self):
        self.disconnects += 1
        self.transport_result = StreamingTransportResult(0, 4, b"partial", None, True, 4)
        return 4

    def close(self, timeout_s=2):
        if self.transport_result is None:
            self.request_disconnect()
        return self.transport_result


def ev(kind, at, *, task=7, offset=None, query=None, tokens=None, finish=None, detail=None):
    return StreamingEvidence(EvidenceKind(kind), "request-1", SESSION, at, task,
                             offset, query, tokens, (), finish, detail)


def worker(tmp_path, call, monitor=None, clock=lambda: 12):
    monitor = monitor or FakeMonitor()
    value = AcknowledgedStreamingWorker("http://127.0.0.1:8080", tmp_path, clock, monitor,
                                        call_factory=lambda _url, _payload: call)
    return value, monitor


def request():
    return make_request("request-1", 0, World().snapshot())


def natural_events():
    return [ev("task_mapped", 1), ev("generation_observed", 2, tokens=2),
            ev("final_timing_log", 3, offset=10), ev("release_log", 4, offset=20),
            ev("log_continuity", 5, task=None, offset=21),
            ev("slot_idle", 6, task=None, query=5)]


def test_natural_stream_completion_emits_one_compatible_model_result(tmp_path):
    call = FakeCall(StreamingTransportResult(0, 7, raw_response(), None, False, None))
    value, monitor = worker(tmp_path, call)
    value.submit(request())
    monitor.events.extend(natural_events())
    result = value.poll(12)
    assert result.termination == "completed" and result.response["choices"][0]["finish_reason"] == "stop"
    assert result.prompt_tokens == 5 and result.completion_tokens == 4
    assert result.server_task_id == 7 and result.session_id == "session-1"
    assert result.occupied_ns == result.terminal_observed_at_ns
    assert value.poll(13) is None and monitor.active is None
    assert (tmp_path / "request-1-stream-request.json").is_file()
    assert (tmp_path / "request-1-stream-response.raw").read_bytes() == call.transport_result.raw


def test_acknowledged_cancel_keeps_worker_occupied_until_complete_boundary(tmp_path):
    call = FakeCall()
    value, monitor = worker(tmp_path, call)
    value.submit(request())
    monitor.events.extend([ev("task_mapped", 1), ev("generation_observed", 2, tokens=3)])
    assert value.poll(2) is None
    receipt = value.request_cancel("request-1", "goal_superseded", 3)
    assert receipt.kind == StopReceiptKind.RECORDED and call.disconnects == 1
    assert value.request_cancel("request-1", "again", 3).kind == StopReceiptKind.ALREADY_PENDING
    monitor.events.extend([
        ev("cancel_log", 5, offset=10), ev("release_log", 6, offset=20),
        ev("log_continuity", 7, task=None, offset=21),
    ])
    assert value.poll(7) is None and value.request is not None
    monitor.events.append(ev("slot_idle", 8, task=None, query=7))
    result = value.poll(12)
    assert result.termination == "cancelled" and result.response is None
    assert result.error is None and not result.server_state_uncertain
    assert result.server_task_id == 7 and monitor.active is None


def test_cancel_before_confirmed_generation_is_not_latched(tmp_path):
    value, monitor = worker(tmp_path, FakeCall())
    value.submit(request())
    assert value.request_cancel("request-1", "too_early", 1).kind == StopReceiptKind.UNSUPPORTED_PHASE
    assert value.evidence.stop_intent_ns is None
    monitor.events.extend([ev("task_mapped", 2), ev("generation_observed", 3, tokens=1)])
    value.poll(3)
    assert value.request_cancel("request-1", "retry", 4).kind == StopReceiptKind.RECORDED


def test_wrong_request_and_poisoned_evidence_fail_closed(tmp_path):
    call = FakeCall()
    value, monitor = worker(tmp_path, call)
    value.submit(request())
    assert value.request_cancel("other", "wrong", 1).kind == StopReceiptKind.WRONG_REQUEST
    monitor.events.append(ev("evidence_failure", 2, task=None, detail="monitor_failed"))
    result = value.poll(2)
    assert result.termination == "uncertain" and result.server_state_uncertain
    assert result.error == "monitor_failed" and result.failure_kind.value == "error"
    assert value.session_poisoned
    try:
        value.submit(request())
    except RuntimeError as exc:
        assert "poisoned" in str(exc)
    else:
        raise AssertionError("Poisoned session must refuse reuse")


def test_future_evidence_and_transport_completion_wait_for_sampled_clock(tmp_path):
    call = FakeCall(StreamingTransportResult(0, 7, raw_response(), None, False, None))
    value, monitor = worker(tmp_path, call, clock=lambda: 8)
    value.submit(request())
    monitor.events.extend(natural_events())
    assert value.poll(4) is None
    assert value.evidence.release_log_offset == 20
    assert value.poll(6) is None
    assert value.poll(8).termination == "completed"


def test_stream_reconstruction_failure_poison_session(tmp_path):
    bad = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 0\r\n\r\n"
    value, monitor = worker(tmp_path, FakeCall(StreamingTransportResult(0, 2, bad, None, False, None)))
    value.submit(request())
    result = value.poll(3)
    assert result.termination == "uncertain" and result.server_state_uncertain
    assert "stream_reconstruction_failed" in result.error


def test_submit_remains_single_occupancy_and_close_is_bounded(tmp_path):
    call = FakeCall()
    value, monitor = worker(tmp_path, call)
    value.submit(request())
    try:
        value.submit(replace(request(), request_id="request-2"))
    except RuntimeError as exc:
        assert "occupied" in str(exc)
    else:
        raise AssertionError("Second submission should fail")
    value.close()
    assert call.disconnects == 1 and monitor.closed
