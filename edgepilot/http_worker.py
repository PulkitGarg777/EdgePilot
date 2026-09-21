"""One outstanding HTTP call to the already-resident model. No cancellation API."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import socket
from typing import Callable
import urllib.error
import urllib.request

from .contracts import FailureKind, Request, Result
from .validation import WAIT_PLAN_SCHEMA


def payload_for(request: Request) -> dict:
    return {
        "model": "edgepilot",
        "messages": [{"role": "user", "content":
            "A robot is idle. This is a wait-only scheduling experiment. "
            "Return only a JSON object with exactly two fields: action set to wait "
            "and a short reason.\n" + json.dumps({
                "goal": {"version": request.goal.version, "instruction": request.goal.instruction},
                "observation": {"version": request.observation.version, "detail": request.observation.detail},
            }, sort_keys=True)}],
        "temperature": 0, "seed": 0, "max_tokens": 128, "stream": False,
        "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object", "schema": WAIT_PLAN_SCHEMA},
    }


class HttpWorker:
    def __init__(self, base_url: str, run_dir: Path, clock: Callable[[], float],
                 socket_timeout: float = 10):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.run_dir, self.clock, self.socket_timeout = run_dir, clock, socket_timeout
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edgepilot-http")
        self.future = None

    def submit(self, request: Request, now: float) -> None:
        if self.future is not None:
            raise RuntimeError("Worker is occupied until its HTTP call returns or fails")
        payload = payload_for(request)
        (self.run_dir / f"{request.request_id}-request.json").write_text(
            json.dumps({"request": asdict(request), "url": self.url, "payload": payload}, indent=2), encoding="utf-8")
        self.future = self.pool.submit(self._invoke, request, payload)

    def _invoke(self, request, payload):
        started = self.clock()
        raw = None
        try:
            http_request = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                                  headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(http_request, timeout=self.socket_timeout) as response:
                raw = response.read()
            finished = self.clock()
            try:
                decoded = json.loads(raw)
            except (ValueError, UnicodeError):
                decoded = raw.decode("utf-8", errors="replace")
            result = Result(request.request_id, request.goal.version, started, finished, response=decoded)
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    raw = exc.read()
                except Exception:
                    pass  # Keep the original HTTP failure if its body cannot be read.
                finally:
                    exc.close()
            timeout = isinstance(exc, (TimeoutError, socket.timeout)) or (
                isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)))
            result = Result(request.request_id, request.goal.version, started, self.clock(),
                            error=f"{type(exc).__name__}: {exc}",
                            failure_kind=FailureKind.TIMEOUT if timeout else FailureKind.ERROR,
                            server_state_uncertain=True)
        if raw is not None:
            try:
                (self.run_dir / f"{request.request_id}-response.raw").write_bytes(raw)
            except OSError as exc:
                # Do not accept a result whose required raw trace could not be saved.
                result = Result(request.request_id, request.goal.version, started, result.finished_at,
                                response=result.response, error=f"Raw trace write failed: {exc}",
                                failure_kind=FailureKind.ERROR,
                                server_state_uncertain=result.server_state_uncertain)
        return result

    def poll(self, now: float) -> Result | None:
        if self.future is None or not self.future.done():
            return None
        result = self.future.result()
        # The background thread can finish after the coordinator sampled now.
        if result.finished_at > now:
            return None
        self.future = None
        return result

    def close(self) -> None:
        self.pool.shutdown(wait=True)
