"""Offline EP-016 session observation tests."""

from collections import deque
import json
from pathlib import Path
import threading

import pytest

from edgepilot.streaming_contracts import EvidenceKind, SessionIdentity
from edgepilot.streaming_session import (
    LogRecord, OwnedServerConfig, OwnedServerSession, SessionLogFollower,
    StreamingSessionMonitor, parse_server_log, strict_slot_evidence,
)
import edgepilot.streaming_session as streaming_session


SESSION = SessionIdentity("session-1", 41, 100, "abc", True)


class FakeProcess:
    pid = 321

    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def owned_config(tmp_path, **changes):
    executable = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    executable.write_bytes(b"server")
    model.write_bytes(b"model")
    values = {"executable": executable, "model": model, "run_dir": tmp_path / "run",
              "startup_timeout_s": .1}
    values.update(changes)
    return OwnedServerConfig(**values)


def install_owned_server_fakes(monkeypatch, config, process=None):
    process = process or FakeProcess()
    launched = []
    listener_calls = 0

    def listeners(host, port):
        nonlocal listener_calls
        listener_calls += 1
        return set() if listener_calls == 1 else {process.pid}

    def get_json(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/props"):
            return {"total_slots": 1, "model_path": str(config.model), "build_info": "test"}
        return [{"is_processing": False}]

    def popen(command, **kwargs):
        launched.append((command, kwargs))
        return process

    record = {"pid": process.pid, "name": "llama-server.exe", "exe": str(config.executable),
              "cmdline": list(config.command()), "create_time": 12.5}
    monkeypatch.setattr(streaming_session, "_llama_processes", lambda: [])
    monkeypatch.setattr(streaming_session, "_listener_pids", listeners)
    monkeypatch.setattr(streaming_session, "_process_record", lambda pid: record)
    monkeypatch.setattr(streaming_session, "get_json", get_json)
    monkeypatch.setattr(streaming_session.subprocess, "Popen", popen)
    return process, launched


def test_owned_session_binds_process_endpoint_manifest_and_cleanup(tmp_path, monkeypatch):
    config = owned_config(tmp_path, synchronization_audited=True)
    process, launched = install_owned_server_fakes(monkeypatch, config)
    session = OwnedServerSession.launch(config)
    try:
        assert session.identity.server_pid == process.pid
        assert session.identity.server_started_ns == 12_500_000_000
        assert session.identity.synchronization_audited is True
        assert session.identity_matches()
        command = launched[0][0]
        assert command[command.index("-np") + 1] == "1"
        assert "--slots" in command and "--log-timestamps" in command
        manifest = json.loads((config.run_dir / "session.json").read_text())
        assert manifest["status"] == "running"
        assert manifest["initial_slots"] == [{"is_processing": False}]
        assert (tmp_path / ".edgepilot-streaming.lock").is_file()
    finally:
        session.close()
    manifest = json.loads((config.run_dir / "session.json").read_text())
    assert process.terminated and not process.killed
    assert manifest["status"] == "closed" and manifest["artifact_identity_preserved"]
    assert not (tmp_path / ".edgepilot-streaming.lock").exists()


def test_owned_session_refuses_existing_server_without_launching(tmp_path, monkeypatch):
    config = owned_config(tmp_path)
    monkeypatch.setattr(streaming_session, "_llama_processes", lambda: [{"pid": 99}])
    monkeypatch.setattr(streaming_session.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(RuntimeError, match="Refusing to adopt"):
        OwnedServerSession.launch(config)
    assert not (tmp_path / ".edgepilot-streaming.lock").exists()


def test_owned_session_lock_is_exclusive_and_tuning_cannot_override_identity(tmp_path):
    config = owned_config(tmp_path)
    (tmp_path / ".edgepilot-streaming.lock").write_text("occupied")
    with pytest.raises(RuntimeError, match="lock already exists"):
        OwnedServerSession.launch(config)
    with pytest.raises(ValueError, match="cannot override"):
        OwnedServerConfig(config.executable, config.model, tmp_path / "other",
                          tuning_args=("--port", "9000"))


def test_strict_slot_requires_explicit_single_slot_schema():
    with pytest.raises(ValueError, match="explicit boolean"):
        strict_slot_evidence([{}], request_id="r1", session=SESSION,
                             query_started_ns=1, observed_at_ns=2)
    with pytest.raises(ValueError, match="exactly one"):
        strict_slot_evidence([], request_id="r1", session=SESSION,
                             query_started_ns=1, observed_at_ns=2)
    idle = strict_slot_evidence([{"is_processing": False}], request_id="r1", session=SESSION,
                                query_started_ns=1, observed_at_ns=2)
    assert idle[0].kind == EvidenceKind.SLOT_IDLE and idle[0].query_started_ns == 1


def test_active_slot_maps_task_and_positive_generation_only():
    prefill = strict_slot_evidence(
        [{"is_processing": True, "id_task": 7, "next_token": [{"n_decoded": 0}]}],
        request_id="r1", session=SESSION, query_started_ns=1, observed_at_ns=2)
    assert [item.kind for item in prefill] == [EvidenceKind.TASK_MAPPED]
    generation = strict_slot_evidence(
        [{"is_processing": True, "id_task": 7, "next_token": [{"n_decoded": 3}]}],
        request_id="r1", session=SESSION, query_started_ns=3, observed_at_ns=4)
    assert [item.kind for item in generation] == [EvidenceKind.TASK_MAPPED,
                                                  EvidenceKind.GENERATION_OBSERVED]


@pytest.mark.parametrize("line, kind, task", [
    ("I slot launch_slot_: id 0 | task 12 | processing task, is_child = 0", "task_mapped", 12),
    ("W srv stop: cancel task, id_task = 13", "cancel_log", 13),
    ("I slot print_timing: id 0 | task 14 | total time = 3 ms", "final_timing_log", 14),
    ("E srv task id = 15, error: failed", "error_log", 15),
    ("I slot release: id 0 | task 16 | stop processing: n_tokens = 4", "release_log", 16),
])
def test_parses_only_task_scoped_lifecycle_records(line, kind, task):
    event = parse_server_log(LogRecord(10, 10 + len(line), line), request_id="r1",
                             session=SESSION, observed_at_ns=20)
    assert event.kind == EvidenceKind(kind) and event.task_id == task and event.log_offset == 10


def test_log_follower_preserves_partial_lines_and_offsets(tmp_path):
    path = tmp_path / "server.log"
    path.write_bytes(b"old\n")
    follower = SessionLogFollower(path)
    with path.open("ab") as output:
        output.write(b"first\npart")
    first = follower.read()
    assert [record.line for record in first] == ["first"]
    with path.open("ab") as output:
        output.write(b"ial\n")
    second = follower.read()
    assert [record.line for record in second] == ["partial"]
    assert first[0].end_offset == second[0].start_offset


def wait_events(monitor, predicate, timeout=.5):
    deadline = threading.Event()
    found = []
    for _ in range(500):
        found.extend(monitor.drain())
        if predicate(found):
            return found
        deadline.wait(timeout / 500)
    return found


def test_monitor_emits_slot_log_and_continuity_evidence(tmp_path):
    path = tmp_path / "server.log"
    path.write_text("", encoding="utf-8")
    slots = deque([
        [{"is_processing": True, "id_task": 7, "next_token": [{"n_decoded": 2}]}],
        [{"is_processing": False}],
    ])
    last = slots[-1]
    monitor = StreamingSessionMonitor("http://127.0.0.1:8080", path, SESSION,
                                      slot_getter=lambda: slots.popleft() if slots else last,
                                      poll_interval_s=.001)
    monitor.start()
    try:
        monitor.observe_request("r1")
        events = wait_events(monitor, lambda values: any(
            event.kind == EvidenceKind.GENERATION_OBSERVED for event in values))
        assert any(event.kind == EvidenceKind.TASK_MAPPED for event in events)
        with path.open("a", encoding="utf-8") as output:
            output.write("I slot release: id 0 | task 7 | stop processing: n_tokens = 4\n")
            output.flush()
        events += wait_events(monitor, lambda values: any(
            event.kind == EvidenceKind.RELEASE_LOG for event in values))
        kinds = {event.kind for event in events}
        assert EvidenceKind.RELEASE_LOG in kinds and EvidenceKind.LOG_CONTINUITY in kinds
    finally:
        monitor.close()


def test_monitor_identity_failure_fails_closed(tmp_path):
    path = tmp_path / "server.log"
    path.write_text("", encoding="utf-8")
    monitor = StreamingSessionMonitor("http://127.0.0.1:8080", path, SESSION,
                                      slot_getter=lambda: [{"is_processing": False}],
                                      identity_check=lambda: False, poll_interval_s=.001,
                                      queue_capacity=1)
    monitor.start()
    try:
        monitor.observe_request("r1")
        events = wait_events(monitor, lambda values: bool(values))
        assert events[0].kind == EvidenceKind.EVIDENCE_FAILURE
        assert "session_identity_changed" in events[0].detail
    finally:
        monitor.close()


def test_monitor_queue_overflow_is_reported_instead_of_dropping_evidence(tmp_path):
    path = tmp_path / "server.log"
    path.write_text("", encoding="utf-8")
    monitor = StreamingSessionMonitor(
        "http://127.0.0.1:8080", path, SESSION,
        slot_getter=lambda: [{"is_processing": True, "id_task": 7,
                              "next_token": [{"n_decoded": 2}]}],
        poll_interval_s=.001, queue_capacity=1)
    monitor.start()
    try:
        monitor.observe_request("r1")
        threading.Event().wait(.02)
        events = monitor.drain()
        assert events[0].kind == EvidenceKind.EVIDENCE_FAILURE
        assert events[0].detail == "monitor_event_queue_overflow"
    finally:
        monitor.close()
