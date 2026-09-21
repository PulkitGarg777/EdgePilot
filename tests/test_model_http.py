"""Offline HTTP adapter integration; synchronized local stub, no real model."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from edgepilot.robot.contracts import NS
from edgepilot.robot.model import ModelHttpWorker, decode_plan, make_request
from edgepilot.robot.world import World


def test_skill_http_adapter_preserves_provenance_raw_body_usage_and_occupancy(tmp_path):
    entered, release = threading.Event(), threading.Event()
    requests = []
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"actions": [
        {"skill": "navigate_to", "target": "station_a"}, {"skill": "inspect", "target": "station_a"},
        {"skill": "navigate_to", "target": "station_b"}, {"skill": "inspect", "target": "station_b"},
        {"skill": "return_home"}]})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 60}}
    raw = json.dumps(response).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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
    start = time.perf_counter_ns()
    clock = lambda: time.perf_counter_ns()-start
    worker = ModelHttpWorker(f"http://127.0.0.1:{server.server_port}", tmp_path, clock)
    request = make_request("model-0001", 0, World().snapshot(), max_tokens=384, deadline_ns=10*NS)
    try:
        worker.submit(request)
        assert entered.wait(5)
        assert worker.poll(clock()) is None
        with pytest.raises(RuntimeError, match="occupied"):
            worker.submit(request)
        release.set()
        worker.transport.future.result(timeout=5)
        result = worker.poll(clock())
        assert (result.prompt_tokens, result.completion_tokens) == (100, 60)
        assert result.started_at_ns <= result.finished_at_ns
        assert result.request_id == request.request_id
        plan = decode_plan(result.response, request)
        world = World()
        world.expect(request.request_id)
        assert world.install(plan)
        assert (tmp_path/"model-0001-response.raw").read_bytes() == raw
        provenance = json.loads((tmp_path/"model-0001-planning-request.json").read_text())
        assert provenance["prompt_sha256"] == request.prompt_sha256
        assert provenance["deadline_ns"] == 10*NS
        payload = requests[0]
        assert payload["max_tokens"] == 384 and payload["cache_prompt"] is False
        assert payload["messages"][0]["content"] == request.prompt
        assert payload["response_format"]["schema"]["additionalProperties"] is False
    finally:
        release.set()
        worker.close()
        server.shutdown()
        server.server_close()
        thread.join()
