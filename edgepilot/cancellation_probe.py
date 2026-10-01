"""Offline-testable primitives for the EP-010 cancellation feasibility probe.

This module deliberately does not change the production HTTP worker.  It owns a
raw HTTP socket so a trial can disconnect the actual connection, and it keeps
reuse admission in a small evidence state machine.  Live llama.cpp parsing is
added only after calibration establishes the pinned build's observed schemas.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import statistics
import sys
import threading
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit
import urllib.request
import uuid

from .__main__ import ROOT, configure_paths, sha256


SCHEMA = "edgepilot.cancellation_probe.v1"
MODES = ("nonstream", "stream")
CUTPOINTS = (64, 256)
ARMS = ("finish", "disconnect")


def generation_schedule(repetitions: int = 4) -> list[dict]:
    """Return the frozen, counterbalanced generation-trial manifest entries."""
    if type(repetitions) is not int or repetitions < 2 or repetitions % 2:
        raise ValueError("Use a positive even number of pairs, at least two")
    entries = []
    conditions = [(mode, cutpoint) for mode in MODES for cutpoint in CUTPOINTS]
    for repeat in range(repetitions):
        offset = repeat % len(conditions)
        rotated = conditions[offset:] + conditions[:offset]
        arms = ARMS if repeat % 2 == 0 else ARMS[::-1]
        for mode, cutpoint in rotated:
            pair_id = f"{mode}-{cutpoint}-{repeat}"
            for position, arm in enumerate(arms):
                entries.append({
                    "index": len(entries), "pair_id": pair_id, "repeat": repeat,
                    "mode": mode, "stream": mode == "stream", "cutpoint": cutpoint,
                    "arm": arm, "position": position, "status": "planned",
                })
    return entries


def manifest(repetitions: int = 4) -> dict:
    entries = generation_schedule(repetitions)
    return {
        "schema": SCHEMA,
        "test_kind": "real_model_cancellation_feasibility",
        "protocol": "Counterbalanced finish/disconnect pairs; no retries or silent exclusions.",
        "repetitions": repetitions,
        "entries": entries,
        "status": "planned",
    }


@dataclass(frozen=True)
class Observation:
    """One normalized observation in server event order."""

    sequence: int
    kind: str
    task_id: int | None = None
    server_pid: int | None = None
    phase: str | None = None
    decoded_tokens: int | None = None
    active_task_ids: tuple[int, ...] = ()
    natural_reason: str | None = None


@dataclass
class CancellationEvidence:
    """Conservative reuse gate for one disconnect trial.

    Server-log order is represented by ``sequence``. Client timestamps belong
    in the raw trace and are intentionally not compared with server timestamps.
    """

    server_pid: int
    task_id: int
    cutpoint: int
    synchronization_audited: bool = True
    active_generation: Observation | None = None
    stop_intended: bool = False
    cancel: Observation | None = None
    release: Observation | None = None
    idle: Observation | None = None
    natural_terminal: Observation | None = None
    conflicts: list[str] = field(default_factory=list)
    reuse_task_id: int | None = None
    reuse_correct: bool = False
    reuse_natural_terminal: bool = False
    reuse_final_idle: bool = False

    def observe(self, event: Observation) -> None:
        if event.server_pid is not None and event.server_pid != self.server_pid:
            self.conflicts.append("server_pid_changed")
            return
        if event.kind == "active":
            if event.task_id == self.task_id and event.phase == "generation":
                if event.decoded_tokens is not None and event.decoded_tokens >= self.cutpoint:
                    self.active_generation = event
        elif event.kind == "cancel":
            if event.task_id == self.task_id:
                if self.cancel is not None:
                    self.conflicts.append("duplicate_cancel")
                else:
                    self.cancel = event
        elif event.kind == "release":
            if event.task_id == self.task_id:
                if self.release is not None:
                    self.conflicts.append("duplicate_release")
                else:
                    self.release = event
        elif event.kind == "natural_terminal" and event.task_id == self.task_id:
            self.natural_terminal = event
        elif event.kind == "idle":
            if self.task_id not in event.active_task_ids:
                self.idle = event

    def record_stop_intent(self) -> None:
        self.stop_intended = True

    @property
    def server_task_released(self) -> bool:
        return self.release is not None

    @property
    def generation_sync_boundary_established(self) -> bool:
        events = (self.active_generation, self.cancel, self.release, self.idle)
        if (not self.stop_intended or not self.synchronization_audited or
                any(event is None for event in events) or self.natural_terminal or self.conflicts):
            return False
        active, cancel, release, idle = events
        assert active is not None and cancel is not None and release is not None and idle is not None
        return active.sequence < cancel.sequence < release.sequence < idle.sequence

    @property
    def reuse_authorized(self) -> bool:
        return self.generation_sync_boundary_established and self.reuse_task_id is None

    def record_reuse(self, *, task_id: int, correct: bool, natural_terminal: bool,
                     final_idle: bool) -> None:
        if not self.generation_sync_boundary_established:
            raise RuntimeError("Reuse is forbidden until the cancellation boundary is established")
        if task_id == self.task_id:
            raise ValueError("Reuse must have a distinct server task ID")
        self.reuse_task_id = task_id
        self.reuse_correct = correct
        self.reuse_natural_terminal = natural_terminal
        self.reuse_final_idle = final_idle

    @property
    def reuse_verified(self) -> bool:
        return (self.generation_sync_boundary_established and self.reuse_task_id is not None
                and self.reuse_correct and self.reuse_natural_terminal and self.reuse_final_idle)

    def outcome(self) -> str:
        if self.reuse_verified:
            return "reuse_verified"
        if self.natural_terminal:
            return "completed_before_stop"
        if self.conflicts:
            return "inconclusive"
        if self.generation_sync_boundary_established:
            return "generation_sync_boundary_established"
        if self.release:
            return "server_task_released_only"
        return "insufficient_evidence"

    def to_dict(self) -> dict:
        value = asdict(self)
        value.update(
            server_task_released=self.server_task_released,
            generation_sync_boundary_established=self.generation_sync_boundary_established,
            reuse_authorized=self.reuse_authorized,
            reuse_verified=self.reuse_verified,
            outcome=self.outcome(),
        )
        return value


class SSEParser:
    """Incrementally parse SSE data fields across arbitrary UTF-8 fragments."""

    def __init__(self) -> None:
        import codecs
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, data: bytes, *, final: bool = False) -> list[str]:
        self._buffer += self._decoder.decode(data, final=final)
        self._buffer = self._buffer.replace("\r\n", "\n")
        blocks = self._buffer.split("\n\n")
        self._buffer = blocks.pop()
        values = []
        for block in blocks:
            fields = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if fields:
                values.append("\n".join(fields))
        if final and self._buffer:
            fields = [line[5:].lstrip() for line in self._buffer.splitlines() if line.startswith("data:")]
            if fields:
                values.append("\n".join(fields))
            self._buffer = ""
        return values


def sse_has_generated_content(data_field: str) -> bool:
    """Ignore role-only frames; SSE chunks are progress, never token counts."""
    if data_field == "[DONE]":
        return False
    try:
        payload = json.loads(data_field)
        choices = payload.get("choices", [])
        return any(bool(choice.get("delta", {}).get("content")) for choice in choices)
    except (TypeError, ValueError, AttributeError):
        return False


def http_response_body(raw: bytes) -> bytes:
    """Extract a body from the HTTP/1 response retained by ``RawHTTPCall``."""
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("Incomplete HTTP response headers")
    headers = head.decode("iso-8859-1").lower()
    if "transfer-encoding: chunked" not in headers:
        return body
    decoded = bytearray()
    while body:
        line, separator, body = body.partition(b"\r\n")
        if not separator:
            raise ValueError("Incomplete HTTP chunk size")
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError as exc:
            raise ValueError("Invalid HTTP chunk size") from exc
        if size == 0:
            return bytes(decoded)
        if len(body) < size + 2 or body[size:size + 2] != b"\r\n":
            raise ValueError("Incomplete HTTP chunk")
        decoded.extend(body[:size])
        body = body[size + 2:]
    raise ValueError("Chunked response did not contain a terminator")


def response_content(raw: bytes, *, stream: bool) -> str:
    """Reconstruct assistant content from a complete chat response."""
    body = http_response_body(raw)
    if not stream:
        value = json.loads(body)
        return "".join(choice.get("message", {}).get("content") or ""
                       for choice in value.get("choices", []))
    parser = SSEParser()
    records = parser.feed(body, final=True)
    pieces = []
    for record in records:
        if record == "[DONE]":
            continue
        value = json.loads(record)
        pieces.extend(choice.get("delta", {}).get("content") or ""
                      for choice in value.get("choices", []))
    return "".join(pieces)


def normalize_slot(value: object, *, server_pid: int, sequence: int) -> Observation:
    """Normalize the single-slot schema observed during pinned-build calibration."""
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Expected one raw slot object")
    slot = value[0]
    if not slot.get("is_processing"):
        return Observation(sequence, "idle", server_pid=server_pid)
    task_id = slot.get("id_task")
    next_tokens = slot.get("next_token")
    if type(task_id) is not int or not isinstance(next_tokens, list) or len(next_tokens) != 1:
        raise ValueError("Active slot lacks calibrated task/progress fields")
    decoded = next_tokens[0].get("n_decoded") if isinstance(next_tokens[0], dict) else None
    if type(decoded) is not int or decoded < 0:
        raise ValueError("Active slot has invalid decoded-token progress")
    return Observation(sequence, "active", task_id=task_id, server_pid=server_pid,
                       phase="generation" if decoded > 0 else "prefill", decoded_tokens=decoded,
                       active_task_ids=(task_id,))


@dataclass(frozen=True)
class TransportResult:
    started_ns: int
    finished_ns: int
    raw: bytes
    error: str | None
    disconnected_by_probe: bool


class RawHTTPCall:
    """One HTTP/1.1 call whose underlying socket is owned by the probe."""

    def __init__(self, url: str, payload: dict, *, timeout: float = 30,
                 clock_ns: Callable[[], int] = time.perf_counter_ns):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("The initial probe supports an explicit local http:// URL only")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.target = parsed.path or "/"
        if parsed.query:
            self.target += "?" + parsed.query
        self.payload, self.timeout, self.clock_ns = payload, timeout, clock_ns
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._data_received = threading.Event()
        self._lock = threading.Lock()
        self._raw = bytearray()
        self._error: str | None = None
        self._started_ns = 0
        self._finished_ns = 0
        self._disconnected = False

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Call already started")
        body = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        request = (
            f"POST {self.target} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        connection = socket.create_connection((self.host, self.port), timeout=self.timeout)
        connection.settimeout(self.timeout)
        with self._lock:
            self._socket = connection
            self._started_ns = self.clock_ns()
            connection.sendall(request)
        self._thread = threading.Thread(target=self._read, name="ep010-http", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                with self._lock:
                    connection = self._socket
                if connection is None:
                    break
                chunk = connection.recv(65536)
                if not chunk:
                    break
                self._raw.extend(chunk)
                self._data_received.set()
        except OSError as exc:
            if not self._disconnected:
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self._finished_ns = self.clock_ns()
            self._done.set()

    def disconnect(self) -> int:
        """Shutdown and close the actual connection; return completion time."""
        with self._lock:
            self._disconnected = True
            connection, self._socket = self._socket, None
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        return self.clock_ns()

    def wait_for_data(self, timeout: float | None = None) -> bool:
        """Wait until at least one response byte is retained."""
        return self._data_received.wait(timeout)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> TransportResult:
        if self._thread is None:
            raise RuntimeError("Call has not started")
        if not self._done.wait(timeout):
            raise TimeoutError("HTTP reader did not finish before the watchdog deadline")
        return TransportResult(self._started_ns, self._finished_ns, bytes(self._raw),
                               self._error, self._disconnected)


def stable_json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def abort_remaining(entries: Iterable[dict], failed_index: int) -> None:
    """Retain planned denominators after a batch-stopping uncertainty."""
    for entry in entries:
        if entry["index"] > failed_index and entry["status"] == "planned":
            entry["status"] = "unattempted_after_abort"


_CANCEL_RE = re.compile(r"cancel task, id_task = (\d+)")
_RELEASE_RE = re.compile(r"\| task\s+(\d+) \| stop processing:")


def normalize_server_log(line: str, *, server_pid: int, sequence: int) -> Observation | None:
    """Parse only the two task-scoped events audited for the pinned build."""
    match = _CANCEL_RE.search(line)
    if match:
        return Observation(sequence, "cancel", task_id=int(match.group(1)), server_pid=server_pid)
    match = _RELEASE_RE.search(line)
    if match:
        return Observation(sequence, "release", task_id=int(match.group(1)), server_pid=server_pid)
    return None


class ServerLogFollower:
    """Read newly appended UTF-8 log lines without changing the server log."""

    def __init__(self, path: Path):
        self.path = path
        self.position = path.stat().st_size
        self._partial = b""

    def read(self) -> list[str]:
        with self.path.open("rb") as source:
            source.seek(self.position)
            data = source.read()
            self.position = source.tell()
        data = self._partial + data
        parts = data.splitlines(keepends=True)
        if parts and not parts[-1].endswith((b"\n", b"\r")):
            self._partial = parts.pop()
        else:
            self._partial = b""
        return [part.decode("utf-8", errors="replace").rstrip("\r\n") for part in parts]


def _save(path: Path, value: object) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    pending.replace(path)


def _poll_slot(base_url: str) -> object:
    return _get_json(base_url.rstrip("/") + "/slots")


def _run_reuse(base_url: str, *, stream: bool, original_task_id: int,
               server_pid: int, deadline: float, poll_interval: float) -> dict:
    result, slots = collect_call(base_url.rstrip("/") + "/v1/chat/completions",
                                 calibration_payload(stream=stream, short=True),
                                 poll_url=base_url.rstrip("/") + "/slots",
                                 deadline=deadline, poll_interval=poll_interval)
    normalized = [normalize_slot(item["value"], server_pid=server_pid, sequence=index)
                  for index, item in enumerate(slots) if item["error"] is None]
    task_ids = sorted({item.task_id for item in normalized if item.kind == "active"})
    content = response_content(result.raw, stream=stream)
    idle = normalize_slot(_poll_slot(base_url), server_pid=server_pid,
                          sequence=len(normalized)).kind == "idle"
    correct = content.startswith("CALIBRATION_OK")
    if result.error or len(task_ids) != 1 or task_ids[0] == original_task_id or not correct or not idle:
        raise RuntimeError("Short reuse control did not establish distinct correct natural completion and idle")
    return {"task_id": task_ids[0], "correct": correct, "natural_terminal": True,
            "final_idle": idle, "elapsed_ns": result.finished_ns - result.started_ns,
            "response_raw": result.raw, "slots": slots}


def run_generation_trial(entry: dict, *, base_url: str, server_log: Path, server_pid: int,
                         folder: Path, deadline: float, poll_interval: float) -> dict:
    """Run one frozen finish/disconnect arm and its gated short reuse."""
    base_url = base_url.rstrip("/")
    prefix = f"{entry['index']:03d}-{entry['mode']}-{entry['cutpoint']}-{entry['arm']}"
    payload = calibration_payload(stream=entry["stream"])
    (folder / f"{prefix}-request.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    follower = ServerLogFollower(server_log)
    initial_log_offset = follower.position
    call = RawHTTPCall(base_url + "/v1/chat/completions", payload, timeout=deadline)
    call.start()
    started = time.perf_counter()
    sequence = 0
    raw_slots, raw_logs = [], []
    gate = None
    task_id = None
    actual_cutpoint = None
    stop_intent_ns = None
    disconnect_finished_ns = None
    release_seen = False
    idle_after_release = False

    while time.perf_counter() - started < deadline:
        slot_started = time.perf_counter_ns()
        raw_slot = _poll_slot(base_url)
        slot_finished = time.perf_counter_ns()
        raw_slots.append({"query_started_ns": slot_started, "query_finished_ns": slot_finished,
                          "value": raw_slot})
        observed = normalize_slot(raw_slot, server_pid=server_pid, sequence=sequence)
        sequence += 1
        if observed.kind == "active":
            if task_id is None:
                task_id = observed.task_id
            elif observed.task_id != task_id:
                raise RuntimeError("Server task identity changed within a target request")
            if observed.phase == "generation" and observed.decoded_tokens >= entry["cutpoint"]:
                if actual_cutpoint is None:
                    actual_cutpoint = observed.decoded_tokens
                    stop_intent_ns = time.perf_counter_ns()
                    if entry["arm"] == "disconnect":
                        gate = CancellationEvidence(server_pid, task_id, entry["cutpoint"])
                        gate.observe(observed)
                        gate.record_stop_intent()
                        disconnect_finished_ns = call.disconnect()
        for line in follower.read():
            raw_logs.append({"received_ns": time.perf_counter_ns(), "line": line})
            log_event = normalize_server_log(line, server_pid=server_pid, sequence=sequence)
            sequence += 1
            if log_event and log_event.task_id == task_id:
                if gate:
                    gate.observe(log_event)
                if log_event.kind == "release":
                    release_seen = True
        if gate and observed.kind == "idle" and release_seen:
            gate.observe(observed)
            idle_after_release = True
        elif not gate and observed.kind == "idle" and release_seen:
            idle_after_release = True
        if gate and gate.generation_sync_boundary_established:
            break
        if gate and release_seen and idle_after_release and gate.cancel is None:
            # Some transports can close locally without causing the server's
            # response reader to post cancellation. Natural release then makes
            # reuse safe, but it is evidence against cancellation support.
            break
        if entry["arm"] == "finish" and call.done and idle_after_release:
            break
        if call.done and actual_cutpoint is None:
            raise RuntimeError("Target completed before the requested generation cutpoint")
        time.sleep(poll_interval)
    else:
        call.disconnect()
        raise TimeoutError("Target request or cancellation evidence exceeded its finite deadline")

    transport = call.wait(2)
    if task_id is None or actual_cutpoint is None:
        raise RuntimeError("Target task/cutpoint was not observed")
    cancellation_supported = bool(gate and gate.generation_sync_boundary_established)
    disconnected_but_completed = bool(gate and not gate.cancel and release_seen and idle_after_release)
    if entry["arm"] == "disconnect" and not (cancellation_supported or disconnected_but_completed):
        raise RuntimeError(f"Cancellation boundary was not established: {gate.to_dict()}")
    if entry["arm"] == "finish":
        response_content(transport.raw, stream=entry["stream"])
        if transport.error or not release_seen or not idle_after_release:
            raise RuntimeError("Finish control lacked natural response, release, or idle evidence")

    reuse = _run_reuse(base_url, stream=entry["stream"], original_task_id=task_id,
                       server_pid=server_pid, deadline=deadline, poll_interval=poll_interval)
    if cancellation_supported:
        gate.record_reuse(task_id=reuse["task_id"], correct=reuse["correct"],
                          natural_terminal=reuse["natural_terminal"], final_idle=reuse["final_idle"])
        if not gate.reuse_verified:
            raise RuntimeError("Reuse was not verified after cancellation")
    (folder / f"{prefix}-response.raw").write_bytes(transport.raw)
    (folder / f"{prefix}-reuse-response.raw").write_bytes(reuse.pop("response_raw"))
    _save(folder / f"{prefix}-slots.json", raw_slots)
    _save(folder / f"{prefix}-reuse-slots.json", reuse.pop("slots"))
    _save(folder / f"{prefix}-events.json", raw_logs)
    with server_log.open("rb") as source:
        source.seek(initial_log_offset)
        (folder / f"{prefix}-server-log.raw").write_bytes(source.read(follower.position-initial_log_offset))
    return {"task_id": task_id, "actual_cutpoint": actual_cutpoint,
            "stop_intent_ns": stop_intent_ns, "disconnect_finished_ns": disconnect_finished_ns,
            "transport_finished_ns": transport.finished_ns, "release_seen": release_seen,
            "idle_after_release": idle_after_release, "reuse": reuse,
            "evidence": gate.to_dict() if gate else None,
            "cancellation_supported": cancellation_supported if gate else None,
            "outcome": ("reuse_verified" if cancellation_supported else
                        "disconnect_not_cancelled" if disconnected_but_completed else
                        "finish_control_completed")}


def run_trials(folder: Path, *, base_url: str, server_log: Path, repetitions: int = 4,
               deadline: float = 30, poll_interval: float = .02) -> dict:
    """Run the frozen repeated generation experiment, aborting on uncertainty."""
    health = _get_json(base_url.rstrip("/") + "/health")
    identity = _server_identity()
    initial = normalize_slot(_poll_slot(base_url), server_pid=identity["pid"], sequence=0)
    if not isinstance(health, dict) or health.get("status") != "ok" or initial.kind != "idle":
        raise RuntimeError("Server must be healthy and idle before creating the trial batch")
    plan = manifest(repetitions)
    plan.update(created_utc=datetime.now(timezone.utc).isoformat(), status="running",
                configuration={"base_url": base_url, "server_log": str(server_log),
                               "server_pid": identity["pid"], "deadline_s": deadline,
                               "poll_interval_s": poll_interval})
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "manifest.json"
    _save(path, plan)
    for entry in plan["entries"]:
        entry["status"] = "running"
        entry["started_utc"] = datetime.now(timezone.utc).isoformat()
        _save(path, plan)
        try:
            current = _server_identity()
            if current["pid"] != identity["pid"]:
                raise RuntimeError("Server PID changed during the batch")
            entry["result"] = run_generation_trial(
                entry, base_url=base_url, server_log=server_log, server_pid=identity["pid"],
                folder=folder, deadline=deadline, poll_interval=poll_interval)
            entry["status"] = "recorded"
        except (Exception, KeyboardInterrupt) as exc:
            entry["status"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            plan["status"] = "aborted"
            abort_remaining(plan["entries"], entry["index"])
        entry["finished_utc"] = datetime.now(timezone.utc).isoformat()
        _save(path, plan)
        print(f"{entry['index']+1}/{len(plan['entries'])}: {entry['pair_id']} {entry['arm']} {entry['status']}", flush=True)
        if plan["status"] == "aborted":
            break
    else:
        plan["status"] = "completed"
        _save(path, plan)
    return plan


def _distribution(values: Iterable[float | None]) -> dict:
    retained = [value for value in values if value is not None]
    return {"n": len(retained), "mean": statistics.mean(retained) if retained else None,
            "median": statistics.median(retained) if retained else None,
            "min": min(retained) if retained else None, "max": max(retained) if retained else None,
            "sample_sd": statistics.stdev(retained) if len(retained) > 1 else None}


def _trial_prefix(entry: dict) -> str:
    return f"{entry['index']:03d}-{entry['mode']}-{entry['cutpoint']}-{entry['arm']}"


def _derive_trial(folder: Path, entry: dict) -> dict:
    if entry.get("status") != "recorded":
        return {"index": entry["index"], "status": entry.get("status"), "error": entry.get("error")}
    prefix = _trial_prefix(entry)
    required = [f"{prefix}-{suffix}" for suffix in
                ("request.json", "response.raw", "reuse-response.raw", "slots.json",
                 "reuse-slots.json", "events.json", "server-log.raw")]
    missing = [name for name in required if not (folder / name).is_file()]
    if missing:
        raise ValueError(f"Trial {entry['index']} is missing artifacts: {missing}")
    events = json.loads((folder / f"{prefix}-events.json").read_text(encoding="utf-8"))
    slots = json.loads((folder / f"{prefix}-slots.json").read_text(encoding="utf-8"))
    task_id = entry["result"]["task_id"]
    cancel = [event for event in events if _CANCEL_RE.search(event["line"])
              and int(_CANCEL_RE.search(event["line"]).group(1)) == task_id]
    release = [event for event in events if _RELEASE_RE.search(event["line"])
               and int(_RELEASE_RE.search(event["line"]).group(1)) == task_id]
    if len(release) != 1 or len(cancel) > 1:
        raise ValueError(f"Trial {entry['index']} has ambiguous cancel/release evidence")
    outcome = entry["result"]["outcome"]
    if (outcome == "reuse_verified") != (len(cancel) == 1):
        raise ValueError(f"Trial {entry['index']} outcome disagrees with cancellation log")
    if outcome == "disconnect_not_cancelled" and cancel:
        raise ValueError(f"Trial {entry['index']} claims no cancellation but has one")
    release_ns = release[0]["received_ns"]
    stop_ns = entry["result"]["stop_intent_ns"]
    idle = [item["query_finished_ns"] for item in slots
            if item["query_finished_ns"] >= stop_ns and isinstance(item.get("value"), list)
            and len(item["value"]) == 1 and not item["value"][0].get("is_processing")]
    if not idle:
        raise ValueError(f"Trial {entry['index']} lacks a post-release idle observation")
    starts = [item["query_started_ns"] for item in slots]
    return {"index": entry["index"], "pair_id": entry["pair_id"], "repeat": entry["repeat"],
            "mode": entry["mode"], "cutpoint": entry["cutpoint"], "arm": entry["arm"],
            "position": entry["position"], "status": entry["status"], "outcome": outcome,
            "task_id": task_id, "actual_cutpoint": entry["result"]["actual_cutpoint"],
            "cutpoint_overshoot": entry["result"]["actual_cutpoint"] - entry["cutpoint"],
            "cancel_received_ns": cancel[0]["received_ns"] if cancel else None,
            "release_received_ns": release_ns, "idle_observed_ns": min(idle),
            "stop_to_cancel_s": ((cancel[0]["received_ns"] - stop_ns) / 1e9) if cancel else None,
            "stop_to_release_s": (release_ns - stop_ns) / 1e9,
            "stop_to_idle_s": (min(idle) - stop_ns) / 1e9,
            "socket_shutdown_s": ((entry["result"]["disconnect_finished_ns"] - stop_ns) / 1e9)
                                 if entry["result"]["disconnect_finished_ns"] is not None else None,
            "reuse_latency_s": entry["result"]["reuse"]["elapsed_ns"] / 1e9,
            "reuse_task_id": entry["result"]["reuse"]["task_id"],
            "reuse_verified": all(entry["result"]["reuse"][key]
                                  for key in ("correct", "natural_terminal", "final_idle")),
            "max_slot_query_gap_s": max((right-left)/1e9 for left, right in zip(starts, starts[1:]))
                                    if len(starts) > 1 else None}


def regenerate_report(manifest_path: Path, calibration_metadata: Path | None = None) -> dict:
    folder = manifest_path.parent
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source.get("schema") != SCHEMA or source.get("test_kind") != "real_model_cancellation_feasibility":
        raise ValueError("Expected an EP-010 cancellation trial manifest")
    trials = [_derive_trial(folder, entry) for entry in source["entries"]]
    recorded = [trial for trial in trials if trial["status"] == "recorded"]
    groups = []
    for mode in MODES:
        for cutpoint in CUTPOINTS:
            for arm in ARMS:
                selected = [trial for trial in recorded if trial["mode"] == mode
                            and trial["cutpoint"] == cutpoint and trial["arm"] == arm]
                groups.append({"mode": mode, "cutpoint": cutpoint, "arm": arm,
                               "n": len(selected),
                               "outcomes": dict(Counter(trial["outcome"] for trial in selected)),
                               "cutpoint_overshoot": _distribution(trial["cutpoint_overshoot"] for trial in selected),
                               "stop_to_cancel_s": _distribution(trial["stop_to_cancel_s"] for trial in selected),
                               "stop_to_release_s": _distribution(trial["stop_to_release_s"] for trial in selected),
                               "stop_to_idle_s": _distribution(trial["stop_to_idle_s"] for trial in selected),
                               "reuse_latency_s": _distribution(trial["reuse_latency_s"] for trial in selected),
                               "max_slot_query_gap_s": _distribution(trial["max_slot_query_gap_s"] for trial in selected)})
    pairs = []
    for pair_id in sorted({trial["pair_id"] for trial in recorded}):
        pair = [trial for trial in recorded if trial["pair_id"] == pair_id]
        by_arm = {trial["arm"]: trial for trial in pair}
        if set(by_arm) != set(ARMS):
            continue
        finish, disconnect = by_arm["finish"], by_arm["disconnect"]
        pairs.append({"pair_id": pair_id, "mode": finish["mode"], "cutpoint": finish["cutpoint"],
                      "repeat": finish["repeat"], "first_arm": pair[0]["arm"],
                      "avoided_remaining_s": finish["stop_to_release_s"]-disconnect["stop_to_release_s"]})
    pair_groups = []
    for mode in MODES:
        for cutpoint in CUTPOINTS:
            selected = [pair for pair in pairs if pair["mode"] == mode and pair["cutpoint"] == cutpoint]
            pair_groups.append({"mode": mode, "cutpoint": cutpoint, "included_pairs": len(selected),
                                "excluded_pairs": source["repetitions"]-len(selected),
                                "avoided_remaining_s": _distribution(pair["avoided_remaining_s"] for pair in selected),
                                "by_first_arm": {arm: _distribution(pair["avoided_remaining_s"] for pair in selected
                                                                     if pair["first_arm"] == arm) for arm in ARMS}})
    files = sorted(path for path in folder.iterdir() if path.is_file()
                   and path.name not in ("report.json", "report.md"))
    provenance = None
    if calibration_metadata is not None:
        calibration = json.loads(calibration_metadata.read_text(encoding="utf-8"))
        if (calibration.get("schema") != SCHEMA or calibration.get("test_kind") != "cancellation_calibration"
                or calibration.get("server", {}).get("pid") != source["configuration"]["server_pid"]):
            raise ValueError("Calibration metadata does not identify the trial server process")
        provenance = {"calibration_metadata": str(calibration_metadata),
                      "calibration_metadata_sha256": sha256(calibration_metadata),
                      "server_pid": calibration["server"]["pid"], "build_info": calibration["props"]["build_info"],
                      "model_path": calibration["props"]["model_path"], "sha256": calibration["sha256"]}
    report = {"schema": SCHEMA + ".report", "manifest_status": source["status"],
              "planned": len(source["entries"]), "recorded": len(recorded),
              "failed": sum(entry["status"] == "failed" for entry in source["entries"]),
              "unattempted": sum(entry["status"].startswith("unattempted") for entry in source["entries"]),
              "outcomes": dict(Counter(trial["outcome"] for trial in recorded)),
              "all_reuse_verified": all(trial["reuse_verified"] for trial in recorded),
              "trials": trials, "groups": groups, "pairs": pair_groups, "provenance": provenance,
              "evidence_sha256": {path.name: sha256(path) for path in files},
              "limitations": ("Single pinned build/model/GPU and four dependent development pairs per condition. "
                              "Client monotonic receipt times include polling, log-flush, transport, and observer delay. "
                              "Paired avoided time is descriptive across separate requests, not a known counterfactual. "
                              "No prompt-phase GPU-complete acknowledgement, kernel-preemption, energy, or general tail claim. "
                              "The trial manifest did not snapshot the exact runner source hash; calibration provenance pins "
                              "the same server process/binary/model but its probe hash predates final runner additions.")}
    _save(folder / "report.json", report)
    (folder / "report.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report: dict) -> str:
    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"
    lines = ["# EP-010 cancellation feasibility results", "",
             f"Batch: {report['manifest_status']}; recorded {report['recorded']} / {report['planned']}; "
             f"failed {report['failed']}; unattempted {report['unattempted']}.", "",
             "Times use client monotonic observation timestamps. Cancel values are null when no server cancel event occurred.", "",
             "| Mode | Cutpoint | Arm | n | Outcomes | Stop-to-cancel median s | Stop-to-release median s | Stop-to-idle median s | Reuse median s |",
             "| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |"]
    for group in report["groups"]:
        lines.append(f"| {group['mode']} | {group['cutpoint']} | {group['arm']} | {group['n']} | "
                     f"`{json.dumps(group['outcomes'], sort_keys=True)}` | {fmt(group['stop_to_cancel_s']['median'])} | "
                     f"{fmt(group['stop_to_release_s']['median'])} | {fmt(group['stop_to_idle_s']['median'])} | "
                     f"{fmt(group['reuse_latency_s']['median'])} |")
    lines += ["", "Paired avoided remaining time is finish-arm stop-to-release minus disconnect-arm stop-to-release; positive favors disconnect.", "",
              "| Mode | Cutpoint | Included / excluded pairs | Median [min, max] avoided s | Finish-first / disconnect-first mean s |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for group in report["pairs"]:
        values = group["avoided_remaining_s"]
        lines.append(f"| {group['mode']} | {group['cutpoint']} | {group['included_pairs']} / {group['excluded_pairs']} | "
                     f"{fmt(values['median'])} [{fmt(values['min'])}, {fmt(values['max'])}] | "
                     f"{fmt(group['by_first_arm']['finish']['mean'])} / {fmt(group['by_first_arm']['disconnect']['mean'])} |")
    lines += ["", f"All recorded trials verified distinct correct reuse: {report['all_reuse_verified']}.", "",
              report["limitations"], ""]
    return "\n".join(lines)


def calibration_payload(*, stream: bool, short: bool = False) -> dict:
    if short:
        prompt, budget = "Reply with exactly CALIBRATION_OK and nothing else.", 16
    else:
        prompt, budget = (
            "Write a numbered sequence beginning at 1. Put one integer and a comma in each item, "
            "continue without commentary, and do not stop voluntarily.", 512)
    return {
        "model": "edgepilot", "messages": [{"role": "user", "content": prompt}],
        "temperature": 0, "seed": 0, "max_tokens": budget, "ignore_eos": not short,
        "stream": stream, "cache_prompt": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _get_json(url: str, timeout: float = 3) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def collect_call(url: str, payload: dict, *, poll_url: str, deadline: float = 30,
                 poll_interval: float = .02) -> tuple[TransportResult, list[dict]]:
    """Run one natural-completion request while retaining raw slot snapshots."""
    if deadline <= 0 or poll_interval <= 0:
        raise ValueError("deadline and poll_interval must be positive")
    call = RawHTTPCall(url, payload, timeout=deadline)
    call.start()
    started = time.perf_counter()
    slots = []
    while not call.done:
        query_started = time.perf_counter_ns()
        try:
            value = _get_json(poll_url, timeout=min(3, deadline))
            error = None
        except Exception as exc:
            value, error = None, f"{type(exc).__name__}: {exc}"
        slots.append({"query_started_ns": query_started,
                      "query_finished_ns": time.perf_counter_ns(),
                      "value": value, "error": error})
        remaining = deadline - (time.perf_counter() - started)
        if remaining <= 0:
            call.disconnect()
            raise TimeoutError("Calibration request exceeded its finite deadline")
        time.sleep(min(poll_interval, remaining))
    return call.wait(0), slots


def _server_identity() -> dict:
    import psutil
    processes = []
    for process in psutil.process_iter(["name", "exe", "cmdline", "create_time"]):
        if (process.info["name"] or "").lower() == "llama-server.exe":
            processes.append({"pid": process.pid, **process.info})
    if len(processes) != 1:
        raise RuntimeError(f"Expected exactly one exclusively owned llama-server, found {len(processes)}")
    command = processes[0].get("cmdline") or []
    required = (("-np", "1"), ("--slots", None), ("--log-timestamps", None))
    for flag, expected in required:
        if flag not in command:
            raise RuntimeError(f"Server command is missing required {flag}")
        if expected is not None and command[command.index(flag) + 1] != expected:
            raise RuntimeError(f"Server command must use {flag} {expected}")
    return processes[0]


def run_calibration(folder: Path, *, base_url: str, server_log: Path,
                    deadline: float = 30, poll_interval: float = .02) -> dict:
    """Capture schemas and natural controls; this does not test cancellation."""
    base_url = base_url.rstrip("/")
    health = _get_json(base_url + "/health")
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError(f"Server is not healthy: {health}")
    identity = _server_identity()
    props = _get_json(base_url + "/props")
    initial_slots = _get_json(base_url + "/slots")
    if not isinstance(props, dict) or props.get("total_slots") != 1:
        raise RuntimeError("Calibration requires exactly one server slot")
    if not server_log.is_file():
        raise FileNotFoundError(f"Server stderr log not found: {server_log}")
    folder.mkdir(parents=True, exist_ok=False)
    log_offset = server_log.stat().st_size
    executable = Path(identity["exe"])
    dll = executable.with_name("llama-server-impl.dll")
    model_path = Path(props["model_path"])
    metadata = {
        "schema": SCHEMA, "test_kind": "cancellation_calibration",
        "created_utc": datetime.now(timezone.utc).isoformat(), "base_url": base_url,
        "server": identity, "health": health, "props": props, "initial_slots": initial_slots,
        "configuration": {"deadline_s": deadline, "poll_interval_s": poll_interval},
        "sha256": {"executable": sha256(executable), "model": sha256(model_path),
                   "probe": sha256(Path(__file__)),
                   "server_impl_dll": sha256(dll) if dll.is_file() else None},
        "server_log": str(server_log), "server_log_initial_offset": log_offset,
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    records = []
    endpoint = base_url + "/v1/chat/completions"
    try:
        for stream in (False, True):
            for short in (False, True):
                name = f"{'stream' if stream else 'nonstream'}-{'reuse' if short else 'full'}"
                payload = calibration_payload(stream=stream, short=short)
                (folder / f"{name}-request.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                result, slots = collect_call(endpoint, payload, poll_url=base_url + "/slots",
                                             deadline=deadline, poll_interval=poll_interval)
                (folder / f"{name}-response.raw").write_bytes(result.raw)
                (folder / f"{name}-slots.json").write_text(json.dumps(slots, indent=2), encoding="utf-8")
                content = response_content(result.raw, stream=stream)
                normalized = [normalize_slot(item["value"], server_pid=identity["pid"], sequence=index)
                              for index, item in enumerate(slots) if item["error"] is None]
                active = [item for item in normalized if item.kind == "active"]
                record = {"name": name, "stream": stream, "short_reuse": short,
                          "payload_sha256": stable_json_hash(payload),
                          "started_ns": result.started_ns, "finished_ns": result.finished_ns,
                          "elapsed_ns": result.finished_ns - result.started_ns,
                          "transport_error": result.error,
                          "response_bytes": len(result.raw), "slot_observations": len(slots),
                          "task_ids": sorted({item.task_id for item in active}),
                          "max_decoded_tokens": max((item.decoded_tokens for item in active), default=None),
                          "short_marker_observed": content.startswith("CALIBRATION_OK") if short else None}
                records.append(record)
                if result.error or (short and not record["short_marker_observed"]):
                    raise RuntimeError(f"Calibration control failed: {record}")
    except Exception as exc:
        status, error = "aborted", f"{type(exc).__name__}: {exc}"
    else:
        status, error = "completed", None
    log_bytes = server_log.read_bytes()
    (folder / "server-stderr-slice.raw").write_bytes(log_bytes[log_offset:])
    summary = {"schema": SCHEMA, "status": status, "error": error, "records": records,
               "server_pid": identity["pid"], "run_directory": str(folder)}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrate = subparsers.add_parser("calibrate", help="Capture live schemas and natural controls")
    calibrate.add_argument("--base-url", default="http://127.0.0.1:8080")
    calibrate.add_argument("--server-log", type=Path, required=True)
    calibrate.add_argument("--deadline", type=float, default=30)
    calibrate.add_argument("--poll-interval", type=float, default=.02)
    trials = subparsers.add_parser("run", help="Run frozen repeated generation cancellation trials")
    trials.add_argument("--base-url", default="http://127.0.0.1:8080")
    trials.add_argument("--server-log", type=Path, required=True)
    trials.add_argument("--repetitions", type=int, default=4)
    trials.add_argument("--deadline", type=float, default=30)
    trials.add_argument("--poll-interval", type=float, default=.02)
    report_parser = subparsers.add_parser("report", help="Regenerate a report from retained trial evidence")
    report_parser.add_argument("manifest", type=Path)
    report_parser.add_argument("--calibration-metadata", type=Path)
    args = parser.parse_args()
    configure_paths()
    if args.command == "calibrate":
        folder = ROOT / "runs" / ("cancellation-calibration-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                                  + "-" + uuid.uuid4().hex[:8])
        try:
            summary = run_calibration(folder, base_url=args.base_url, server_log=args.server_log,
                                      deadline=args.deadline, poll_interval=args.poll_interval)
        except Exception as exc:
            parser.exit(1, f"Calibration startup failed: {exc}\n")
        print(json.dumps(summary, indent=2))
        return int(summary["status"] != "completed")
    if args.command == "run":
        folder = ROOT / "runs" / ("cancellation-trials-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                                  + "-" + uuid.uuid4().hex[:8])
        try:
            result = run_trials(folder, base_url=args.base_url, server_log=args.server_log,
                                repetitions=args.repetitions, deadline=args.deadline,
                                poll_interval=args.poll_interval)
        except Exception as exc:
            parser.exit(1, f"Trial startup failed: {exc}\n")
        print(f"Manifest: {folder / 'manifest.json'}")
        return int(result["status"] != "completed")
    if args.command == "report":
        try:
            report = regenerate_report(args.manifest, args.calibration_metadata)
        except (ValueError, KeyError, OSError) as exc:
            parser.exit(1, f"Report failed: {exc}\n")
        print(render_report(report))
        return int(report["manifest_status"] != "completed" or report["recorded"] != report["planned"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
