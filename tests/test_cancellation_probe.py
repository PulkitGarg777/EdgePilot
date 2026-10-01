"""Offline tests for EP-010 transport and conservative evidence gating."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from edgepilot.cancellation_probe import (
    CancellationEvidence, Observation, RawHTTPCall, SSEParser, abort_remaining,
    calibration_payload, collect_call, generation_schedule, manifest,
    http_response_body, normalize_slot, response_content, sse_has_generated_content,
    normalize_server_log, ServerLogFollower, stable_json_hash,
    _distribution,
)


def event(sequence, kind, *, task=7, pid=101, phase=None, tokens=None, active=()):
    return Observation(sequence, kind, task, pid, phase, tokens, tuple(active))


def complete_boundary(gate):
    gate.observe(event(1, "active", phase="generation", tokens=64))
    gate.record_stop_intent()
    gate.observe(event(2, "cancel"))
    gate.observe(event(3, "release"))
    gate.observe(event(4, "idle", task=None, active=()))


@pytest.mark.parametrize("repetitions", [0, 1, 3, -2, True])
def test_schedule_requires_balanced_pairs(repetitions):
    with pytest.raises(ValueError):
        generation_schedule(repetitions)


def test_schedule_is_complete_and_counterbalanced():
    entries = generation_schedule()
    assert len(entries) == 32
    assert len({entry["pair_id"] for entry in entries}) == 16
    for pair_id in {entry["pair_id"] for entry in entries}:
        pair = [entry for entry in entries if entry["pair_id"] == pair_id]
        assert {entry["arm"] for entry in pair} == {"finish", "disconnect"}
    first_arms = [entry["arm"] for entry in entries if entry["position"] == 0]
    assert first_arms.count("finish") == first_arms.count("disconnect") == 8
    assert manifest()["schema"] == "edgepilot.cancellation_probe.v1"


@pytest.mark.parametrize("only", ["client", "cancel", "release", "idle"])
def test_individual_signals_never_authorize_reuse(only):
    gate = CancellationEvidence(101, 7, 64)
    if only == "client":
        gate.record_stop_intent()
    elif only == "cancel":
        gate.observe(event(2, "cancel"))
    elif only == "release":
        gate.observe(event(3, "release"))
    else:
        gate.observe(event(4, "idle", task=None))
    assert not gate.reuse_authorized


def test_matching_ordered_generation_evidence_authorizes_distinct_reuse():
    gate = CancellationEvidence(101, 7, 64)
    complete_boundary(gate)
    assert gate.server_task_released and gate.generation_sync_boundary_established
    assert gate.reuse_authorized and gate.outcome() == "generation_sync_boundary_established"
    gate.record_reuse(task_id=9, correct=True, natural_terminal=True, final_idle=True)
    assert gate.reuse_verified and gate.outcome() == "reuse_verified"
    assert not gate.reuse_authorized


@pytest.mark.parametrize("mutation", ["wrong_task", "wrong_pid", "duplicate", "bad_order",
                                       "missing_phase", "prefill", "natural", "unaudited"])
def test_ambiguous_or_conflicting_evidence_refuses_reuse(mutation):
    gate = CancellationEvidence(101, 7, 64, synchronization_audited=mutation != "unaudited")
    phase = "prefill" if mutation == "prefill" else (None if mutation == "missing_phase" else "generation")
    gate.observe(event(1, "active", task=8 if mutation == "wrong_task" else 7,
                       pid=102 if mutation == "wrong_pid" else 101, phase=phase, tokens=64))
    gate.record_stop_intent()
    gate.observe(event(3 if mutation == "bad_order" else 2, "cancel"))
    if mutation == "duplicate":
        gate.observe(event(3, "cancel"))
    gate.observe(event(2 if mutation == "bad_order" else 3, "release"))
    if mutation == "natural":
        gate.observe(event(3, "natural_terminal"))
    gate.observe(event(4, "idle", task=None))
    assert not gate.reuse_authorized
    with pytest.raises(RuntimeError):
        gate.record_reuse(task_id=9, correct=True, natural_terminal=True, final_idle=True)


def test_same_task_cannot_be_mistaken_for_reuse():
    gate = CancellationEvidence(101, 7, 64)
    complete_boundary(gate)
    with pytest.raises(ValueError):
        gate.record_reuse(task_id=7, correct=True, natural_terminal=True, final_idle=True)


def test_release_and_idle_without_cancel_is_not_cancellation_success():
    gate = CancellationEvidence(101, 7, 64)
    gate.observe(event(1, "active", phase="generation", tokens=64))
    gate.record_stop_intent()
    gate.observe(event(2, "release"))
    gate.observe(event(3, "idle", task=None))
    assert gate.server_task_released
    assert not gate.generation_sync_boundary_established
    assert not gate.reuse_authorized
    assert gate.outcome() == "server_task_released_only"


def test_sse_parser_handles_split_utf8_and_ignores_role_frame():
    parser = SSEParser()
    role = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    content = 'data: {"choices":[{"delta":{"content":"caf\u00e9"}}]}\n\n'.encode()
    parts = [role[:11], role[11:] + content[:-4], content[-4:-2], content[-2:]]
    records = []
    for part in parts:
        records.extend(parser.feed(part))
    records.extend(parser.feed(b"", final=True))
    assert len(records) == 2
    assert not sse_has_generated_content(records[0])
    assert sse_has_generated_content(records[1])
    assert not sse_has_generated_content("[DONE]")


def test_chunked_stream_content_is_reconstructed_across_deltas():
    first = b'data: {"choices":[{"delta":{"content":"CAL"}}]}\n\n'
    second = b'data: {"choices":[{"delta":{"content":"IBRATION_OK"}}]}\n\ndata: [DONE]\n\n'
    body = f"{len(first):x}\r\n".encode() + first + b"\r\n"
    body += f"{len(second):x}\r\n".encode() + second + b"\r\n0\r\n\r\n"
    raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body
    assert http_response_body(raw) == first + second
    assert response_content(raw, stream=True) == "CALIBRATION_OK"


def test_nonstream_content_and_calibrated_slot_normalization():
    body = b'{"choices":[{"message":{"content":"CALIBRATION_OK"}}]}'
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 58\r\n\r\n" + body
    assert response_content(raw, stream=False) == "CALIBRATION_OK"
    active = normalize_slot([{"is_processing": True, "id_task": 12,
                              "next_token": [{"n_decoded": 64}]}], server_pid=4, sequence=9)
    assert active.task_id == 12 and active.phase == "generation" and active.decoded_tokens == 64
    idle = normalize_slot([{"id": 0, "is_processing": False}], server_pid=4, sequence=10)
    assert idle.kind == "idle" and idle.active_task_ids == ()
    with pytest.raises(ValueError):
        normalize_slot([{"is_processing": True, "id_task": 12}], server_pid=4, sequence=11)


def test_server_log_parser_requires_task_scoped_audited_events():
    cancel = normalize_server_log("6.48 W srv stop: cancel task, id_task = 2762", server_pid=8, sequence=2)
    release = normalize_server_log(
        "6.49 I slot release: id 0 | task 2762 | stop processing: n_tokens = 106, truncated = 0",
        server_pid=8, sequence=3)
    assert (cancel.kind, cancel.task_id, cancel.server_pid) == ("cancel", 2762, 8)
    assert (release.kind, release.task_id) == ("release", 2762)
    assert normalize_server_log("slot 0 is idle", server_pid=8, sequence=4) is None


def test_log_follower_retains_partial_line(tmp_path):
    path = tmp_path / "server.log"
    path.write_bytes(b"old\n")
    follower = ServerLogFollower(path)
    with path.open("ab") as output:
        output.write(b"cancel task")
    assert follower.read() == []
    with path.open("ab") as output:
        output.write(b", id_task = 4\nrelease\n")
    assert follower.read() == ["cancel task, id_task = 4", "release"]


def test_raw_http_call_disconnects_owned_socket():
    received = threading.Event()
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n")
                    self.wfile.flush()
                    time.sleep(.01)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                disconnected.set()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        call = RawHTTPCall(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", {"stream": True})
        call.start()
        assert received.wait(1)
        assert call.wait_for_data(1)
        call.disconnect()
        result = call.wait(1)
        assert result.disconnected_by_probe and result.error is None and result.raw.startswith(b"HTTP/1.0 200")
        assert disconnected.wait(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_reader_watchdog_is_bounded():
    ready = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            ready.set()
            time.sleep(.3)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        call = RawHTTPCall(f"http://127.0.0.1:{server.server_port}/", {})
        call.start()
        assert ready.wait(1)
        with pytest.raises(TimeoutError):
            call.wait(.01)
        call.disconnect()
        assert call.wait(1).disconnected_by_probe
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_calibration_payload_freezes_generation_controls():
    payload = calibration_payload(stream=True)
    assert payload["stream"] is True and payload["max_tokens"] == 512
    assert payload["ignore_eos"] is True and payload["cache_prompt"] is False
    assert payload["temperature"] == payload["seed"] == 0
    short = calibration_payload(stream=False, short=True)
    assert short["max_tokens"] == 16 and short["ignore_eos"] is False


def test_collect_call_retains_raw_slot_observations():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            encoded = json.dumps([{"id": 0, "is_processing": True, "n_decoded": 3}]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(.04)
            encoded = b'{"choices":[{"message":{"content":"ok"}}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        result, slots = collect_call(root + "/chat", {}, poll_url=root + "/slots",
                                     deadline=1, poll_interval=.005)
        assert result.error is None and b'"content":"ok"' in result.raw
        assert slots and all(item["value"][0]["is_processing"] for item in slots)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_abort_preserves_unattempted_denominator_and_hash_is_stable():
    entries = generation_schedule(2)
    entries[0]["status"] = "failed"
    abort_remaining(entries, 0)
    assert entries[0]["status"] == "failed"
    assert all(entry["status"] == "unattempted_after_abort" for entry in entries[1:])
    assert stable_json_hash({"b": 2, "a": 1}) == stable_json_hash({"a": 1, "b": 2})


def test_report_distribution_keeps_nulls_out_of_numeric_summary():
    empty = _distribution([None])
    single = _distribution([2.0, None])
    assert empty["n"] == 0 and empty["mean"] is None
    assert single["n"] == 1 and single["median"] == 2.0 and single["sample_sd"] is None
