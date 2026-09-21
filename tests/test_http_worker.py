"""Offline HTTP adapter tests: local controlled stub and injected transport errors."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.error

import pytest

from edgepilot.contracts import FailureKind, Goal, Observation, Request
from edgepilot.http_worker import HttpWorker, payload_for


def request():
    return Request("r0001", 0, Goal(1, 0, 0, "idle"), Observation(0, 0, "idle"))


def test_prompt_is_independent_of_wall_clock_delivery_jitter():
    first = request()
    second = replace(first, created_at=.003, goal=replace(first.goal, created_at=.003),
                     observation=replace(first.observation, observed_at=.002))
    assert payload_for(first) == payload_for(second)


def test_http_worker_preserves_payload_and_raw_response_and_remains_occupied(tmp_path):
    entered, release = threading.Event(), threading.Event()
    captured = []
    raw = b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"action\\":\\"wait\\",\\"reason\\":\\"idle\\"}"}}]}'

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            entered.set()
            release.wait(5)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    started = time.perf_counter()
    clock = lambda: time.perf_counter() - started
    worker = HttpWorker(f"http://127.0.0.1:{server.server_port}", tmp_path, clock)
    try:
        worker.submit(request(), 0)
        assert entered.wait(5)
        assert worker.poll(clock()) is None
        with pytest.raises(RuntimeError, match="occupied"):
            worker.submit(request(), clock())
        release.set()
        worker.future.result(timeout=5)  # Synchronize; no latency assertions.
        result = worker.poll(clock())
        assert result.request_id == "r0001" and result.goal_version == 1
        assert result.response == json.loads(raw)
        assert result.started_at <= result.finished_at
        assert worker.future is None
        assert (tmp_path / "r0001-response.raw").read_bytes() == raw
        payload = captured[0]
        assert payload["response_format"]["schema"]["additionalProperties"] is False
        assert payload["cache_prompt"] is False
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert json.loads((tmp_path / "r0001-request.json").read_text())["payload"] == payload
    finally:
        release.set()
        worker.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("exception,kind", [
    (TimeoutError("timed out"), FailureKind.TIMEOUT),
    (urllib.error.URLError(TimeoutError("timed out")), FailureKind.TIMEOUT),
    (urllib.error.URLError("connection refused"), FailureKind.ERROR),
])
def test_transport_errors_are_explicit_and_server_state_uncertain(tmp_path, monkeypatch, exception, kind):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr("urllib.request.urlopen", fail)
    worker = HttpWorker("http://127.0.0.1:8080", tmp_path, lambda: 1)
    try:
        worker.submit(request(), 0)
        worker.future.result(timeout=5)
        result = worker.poll(1)
        assert result.failure_kind == kind
        assert result.server_state_uncertain
        assert result.error
    finally:
        worker.close()


def test_finished_result_after_sampled_now_is_consumed_on_next_poll(tmp_path, monkeypatch):
    worker = HttpWorker("http://127.0.0.1:8080", tmp_path, lambda: 2)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    try:
        worker.submit(request(), 0)
        worker.future.result(timeout=5)
        assert worker.poll(1) is None
        assert worker.future is not None
        assert worker.poll(2).failure_kind == FailureKind.TIMEOUT
    finally:
        worker.close()
