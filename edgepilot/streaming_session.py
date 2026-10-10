"""Background observation of one verified, single-slot llama.cpp session."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Callable
import urllib.request
import uuid

from .streaming_contracts import EvidenceKind, SessionIdentity, StreamingEvidence


OWNED_SESSION_SCHEMA = "edgepilot.owned-streaming-session.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _llama_processes() -> list[dict]:
    import psutil
    found = []
    for process in psutil.process_iter(["name", "exe", "cmdline", "create_time"]):
        if (process.info.get("name") or "").lower() == "llama-server.exe":
            found.append({"pid": process.pid, **process.info})
    return found


def _process_record(pid: int) -> dict:
    import psutil
    process = psutil.Process(pid)
    with process.oneshot():
        return {"pid": pid, "name": process.name(), "exe": process.exe(),
                "cmdline": process.cmdline(), "create_time": process.create_time()}


def _listener_pids(host: str, port: int) -> set[int | None]:
    import psutil
    return {connection.pid for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN and connection.laddr
            and connection.laddr.ip == host and connection.laddr.port == port}


@dataclass(frozen=True)
class OwnedServerConfig:
    """Pinned inputs for a fresh local single-slot llama.cpp session."""

    executable: Path
    model: Path
    run_dir: Path
    host: str = "127.0.0.1"
    port: int = 8080
    alias: str = "edgepilot"
    startup_timeout_s: float = 30
    request_timeout_s: float = 1
    synchronization_audited: bool = False
    tuning_args: tuple[str, ...] = (
        "-ngl", "99", "-c", "2048", "-t", "4", "-b", "256", "-ub", "128",
        "-fa", "off", "--jinja",
    )

    def __post_init__(self):
        if self.host != "127.0.0.1":
            raise ValueError("Owned streaming sessions require the IPv4 loopback host")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Server port must be an integer from 1 through 65535")
        if not self.alias.strip() or self.startup_timeout_s <= 0 or self.request_timeout_s <= 0:
            raise ValueError("Alias and positive startup/request bounds are required")
        if type(self.synchronization_audited) is not bool:
            raise ValueError("Synchronization audit flag must be boolean")
        if (not isinstance(self.tuning_args, tuple)
                or any(not isinstance(argument, str) or not argument for argument in self.tuning_args)):
            raise ValueError("Tuning arguments must be an immutable tuple of nonempty strings")
        forbidden = {"-m", "--model", "--alias", "--host", "--port", "-np", "--parallel",
                     "--slots", "--log-timestamps"}
        if any(argument in forbidden for argument in self.tuning_args):
            raise ValueError("Tuning arguments cannot override owned-session identity flags")

    def command(self) -> tuple[str, ...]:
        return (str(self.executable), "-m", str(self.model), "--alias", self.alias,
                *self.tuning_args, "-np", "1", "--slots", "--log-timestamps",
                "--host", self.host, "--port", str(self.port))


class OwnedServerSession:
    """Launch, bind, and clean up one server without adopting external processes."""

    def __init__(self, config: OwnedServerConfig, process, log_file, lock_path: Path,
                 lock_token: str, identity: SessionIdentity, manifest: dict):
        self.config, self.process, self.log_file = config, process, log_file
        self.lock_path, self.lock_token = lock_path, lock_token
        self.identity, self.manifest = identity, manifest
        self.log_path = config.run_dir / "server.log"
        self.base_url = f"http://{config.host}:{config.port}"
        self.closed = False

    @classmethod
    def launch(cls, config: OwnedServerConfig) -> "OwnedServerSession":
        executable, model = config.executable.resolve(), config.model.resolve()
        if not executable.is_file() or not model.is_file():
            raise FileNotFoundError("Owned server executable and model must both exist")
        config.run_dir.mkdir(parents=True, exist_ok=False)
        lock_path = config.run_dir.parent / ".edgepilot-streaming.lock"
        lock_token = uuid.uuid4().hex
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Owned streaming lock already exists: {lock_path}") from exc
        process = log_file = None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock:
                json.dump({"token": lock_token, "run_dir": str(config.run_dir)}, lock)
            existing = _llama_processes()
            if existing:
                raise RuntimeError(f"Refusing to adopt {len(existing)} existing llama-server process(es)")
            listeners = _listener_pids(config.host, config.port)
            if listeners:
                raise RuntimeError(f"Requested endpoint is already occupied by PID(s): {sorted(listeners, key=str)}")
            log_path = config.run_dir / "server.log"
            log_file = log_path.open("xb")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            command = list(config.command())
            command[0], command[2] = str(executable), str(model)
            process = subprocess.Popen(command, cwd=executable.parent, stdin=subprocess.DEVNULL,
                                       stdout=log_file, stderr=subprocess.STDOUT,
                                       creationflags=creationflags)
            deadline = time.monotonic() + config.startup_timeout_s
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Owned llama-server exited during startup with code {process.returncode}")
                try:
                    record = _process_record(process.pid)
                    bound = _listener_pids(config.host, config.port)
                    health = get_json(f"http://{config.host}:{config.port}/health",
                                      config.request_timeout_s)
                    props = get_json(f"http://{config.host}:{config.port}/props",
                                     config.request_timeout_s)
                    slots = get_json(f"http://{config.host}:{config.port}/slots",
                                     config.request_timeout_s)
                    cls._validate_startup(config, command, process.pid, record, bound,
                                          health, props, slots)
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            else:
                raise TimeoutError(f"Owned llama-server did not verify before startup deadline: {last_error}")
            artifacts = {"executable": _sha256(executable), "model": _sha256(model),
                         "streaming_session_source": _sha256(Path(__file__))}
            dll = executable.with_name("llama-server-impl.dll")
            artifacts["server_impl_dll"] = _sha256(dll) if dll.is_file() else None
            configuration_sha256 = _stable_hash({"command": command, "artifacts": artifacts,
                                                 "build_info": props.get("build_info")})
            identity = SessionIdentity("owned-" + uuid.uuid4().hex, process.pid,
                                       int(record["create_time"] * 1_000_000_000),
                                       configuration_sha256, config.synchronization_audited)
            manifest = {"schema": OWNED_SESSION_SCHEMA, "status": "running",
                        "session": asdict(identity), "base_url": f"http://{config.host}:{config.port}",
                        "command": command, "process": record, "health": health, "props": props,
                        "initial_slots": slots, "artifacts_start_sha256": artifacts,
                        "server_log": str(log_path)}
            (config.run_dir / "session.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return cls(config, process, log_file, lock_path, lock_token, identity, manifest)
        except Exception:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if log_file is not None:
                log_file.close()
            cls._release_lock(lock_path, lock_token)
            raise

    @staticmethod
    def _validate_startup(config: OwnedServerConfig, command: list[str], pid: int, record: dict,
                          listeners: set[int | None], health: object, props: object,
                          slots: object) -> None:
        if listeners != {pid}:
            raise RuntimeError("Loopback listener is not exclusively bound to the launched PID")
        if Path(record["exe"]).resolve() != config.executable.resolve():
            raise RuntimeError("Launched process executable identity changed")
        if record.get("cmdline") != command:
            raise RuntimeError("Launched process command line differs from the owned configuration")
        if not isinstance(health, dict) or health.get("status") != "ok":
            raise RuntimeError("Owned server is not healthy")
        if not isinstance(props, dict) or props.get("total_slots") != 1:
            raise RuntimeError("Owned server must expose exactly one slot")
        if Path(props.get("model_path", "")).resolve() != config.model.resolve():
            raise RuntimeError("Owned server reported a different model path")
        if (not isinstance(slots, list) or len(slots) != 1 or not isinstance(slots[0], dict)
                or slots[0].get("is_processing") is not False):
            raise RuntimeError("Owned server must start with one explicitly idle slot")

    def identity_matches(self) -> bool:
        if self.closed or self.process.poll() is not None:
            return False
        try:
            record = _process_record(self.identity.server_pid)
            return (int(record["create_time"] * 1_000_000_000) == self.identity.server_started_ns
                    and Path(record["exe"]).resolve() == self.config.executable.resolve()
                    and _listener_pids(self.config.host, self.config.port) == {self.identity.server_pid})
        except Exception:
            return False

    def create_monitor(self, **kwargs) -> "StreamingSessionMonitor":
        if not self.identity_matches():
            raise RuntimeError("Owned server identity is no longer valid")
        return StreamingSessionMonitor(self.base_url, self.log_path, self.identity,
                                       identity_check=self.identity_matches, **kwargs)

    @staticmethod
    def _release_lock(path: Path, token: str) -> None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("token") == token:
                path.unlink()
        except (FileNotFoundError, OSError, ValueError, AttributeError):
            pass

    def close(self, timeout_s: float = 5) -> None:
        if self.closed:
            return
        recovered = False
        try:
            finish_hashes = {"executable": _sha256(self.config.executable.resolve()),
                             "model": _sha256(self.config.model.resolve()),
                             "streaming_session_source": _sha256(Path(__file__))}
            dll = self.config.executable.resolve().with_name("llama-server-impl.dll")
            finish_hashes["server_impl_dll"] = _sha256(dll) if dll.is_file() else None
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    recovered = True
                    self.process.kill()
                    self.process.wait(timeout=timeout_s)
            self.manifest.update(status="closed", returncode=self.process.returncode,
                                 forced_kill_recovery=recovered,
                                 artifacts_finish_sha256=finish_hashes,
                                 artifact_identity_preserved=(
                                     finish_hashes == self.manifest["artifacts_start_sha256"]))
            (self.config.run_dir / "session.json").write_text(
                json.dumps(self.manifest, indent=2), encoding="utf-8")
        finally:
            self.closed = True
            self.log_file.close()
            self._release_lock(self.lock_path, self.lock_token)


_LAUNCH_RE = re.compile(r"\| task\s+(\d+) \| processing task")
_CANCEL_RE = re.compile(r"cancel task, id_task = (\d+)")
_FINAL_RE = re.compile(r"\| task\s+(\d+) \|\s+total time =")
_ERROR_RE = re.compile(r"task id = (\d+), error:")
_RELEASE_RE = re.compile(r"\| task\s+(\d+) \| stop processing:")


@dataclass(frozen=True)
class LogRecord:
    start_offset: int
    end_offset: int
    line: str


class SessionLogFollower:
    """Read complete appended lines and retain byte offsets for server ordering."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise FileNotFoundError(path)
        self.path = path
        self.position = path.stat().st_size
        self._partial = b""
        self._partial_start = self.position

    def read(self) -> list[LogRecord]:
        size = self.path.stat().st_size
        if size < self.position:
            raise RuntimeError("Server log was truncated or replaced")
        with self.path.open("rb") as source:
            source.seek(self.position)
            chunk = source.read()
            self.position = source.tell()
        data = self._partial + chunk
        base = self._partial_start
        records = []
        cursor = 0
        for part in data.splitlines(keepends=True):
            if not part.endswith((b"\n", b"\r")):
                self._partial = part
                self._partial_start = base + cursor
                break
            end = cursor + len(part)
            records.append(LogRecord(base + cursor, base + end,
                                     part.decode("utf-8", errors="replace").rstrip("\r\n")))
            cursor = end
        else:
            self._partial = b""
            self._partial_start = self.position
        return records


def parse_server_log(record: LogRecord, *, request_id: str, session: SessionIdentity,
                     observed_at_ns: int) -> StreamingEvidence | None:
    for pattern, kind in ((_LAUNCH_RE, EvidenceKind.TASK_MAPPED),
                          (_CANCEL_RE, EvidenceKind.CANCEL_LOG),
                          (_FINAL_RE, EvidenceKind.FINAL_TIMING_LOG),
                          (_ERROR_RE, EvidenceKind.ERROR_LOG),
                          (_RELEASE_RE, EvidenceKind.RELEASE_LOG)):
        match = pattern.search(record.line)
        if match:
            return StreamingEvidence(kind, request_id, session, observed_at_ns,
                                     task_id=int(match.group(1)), log_offset=record.start_offset)
    return None


def strict_slot_evidence(value: object, *, request_id: str, session: SessionIdentity,
                         query_started_ns: int, observed_at_ns: int) -> tuple[StreamingEvidence, ...]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Expected exactly one slot object")
    slot = value[0]
    processing = slot.get("is_processing")
    if type(processing) is not bool:
        raise ValueError("Slot is_processing must be an explicit boolean")
    if not processing:
        return (StreamingEvidence(EvidenceKind.SLOT_IDLE, request_id, session, observed_at_ns,
                                  query_started_ns=query_started_ns, active_task_ids=()),)
    task_id = slot.get("id_task")
    next_token = slot.get("next_token")
    if (type(task_id) is not int or task_id < 0 or not isinstance(next_token, list) or
            len(next_token) != 1 or not isinstance(next_token[0], dict)):
        raise ValueError("Active slot lacks calibrated task fields")
    decoded = next_token[0].get("n_decoded")
    if type(decoded) is not int or decoded < 0:
        raise ValueError("Active slot has invalid generation progress")
    mapped = StreamingEvidence(EvidenceKind.TASK_MAPPED, request_id, session,
                               observed_at_ns, task_id=task_id,
                               active_task_ids=(task_id,))
    if decoded == 0:
        return (mapped,)
    generated = StreamingEvidence(EvidenceKind.GENERATION_OBSERVED, request_id, session,
                                  observed_at_ns, task_id=task_id, decoded_tokens=decoded,
                                  active_task_ids=(task_id,))
    return mapped, generated


def get_json(url: str, timeout_s: float) -> object:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return json.load(response)


class StreamingSessionMonitor:
    """One bounded observer thread; it never mutates mission or reducer state."""

    def __init__(self, base_url: str, log_path: Path, session: SessionIdentity, *,
                 clock_ns: Callable[[], int] = time.perf_counter_ns,
                 slot_getter: Callable[[], object] | None = None,
                 identity_check: Callable[[], bool] = lambda: True,
                 poll_interval_s: float = .02, request_timeout_s: float = 1,
                 queue_capacity: int = 256):
        if poll_interval_s <= 0 or request_timeout_s <= 0 or queue_capacity <= 0:
            raise ValueError("Monitor bounds must be positive")
        self.base_url, self.log_path, self.session = base_url.rstrip("/"), log_path, session
        self.clock_ns, self.identity_check = clock_ns, identity_check
        self.slot_getter = slot_getter or (lambda: get_json(self.base_url + "/slots", request_timeout_s))
        self.poll_interval_s = poll_interval_s
        self.events: queue.Queue[StreamingEvidence] = queue.Queue(queue_capacity)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._overflow = threading.Event()
        self._overflow_request: str | None = None
        self._active_request: str | None = None
        self._follower: SessionLogFollower | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Monitor already started")
        self._thread = threading.Thread(target=self._run, name="edgepilot-stream-monitor", daemon=True)
        self._thread.start()

    def observe_request(self, request_id: str) -> None:
        if not request_id.strip():
            raise ValueError("Request ID is required")
        follower = SessionLogFollower(self.log_path)
        with self._state_lock:
            if self._active_request is not None:
                raise RuntimeError("Monitor is already observing a request")
            self._active_request = request_id
            self._follower = follower

    def clear_request(self, request_id: str) -> None:
        with self._state_lock:
            if self._active_request is None:
                return
            if request_id != self._active_request:
                raise RuntimeError("Monitor request identity mismatch")
            self._active_request, self._follower = None, None

    def _emit(self, event: StreamingEvidence) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self._overflow_request = event.request_id
            self._overflow.set()

    def _failure(self, request_id: str, detail: str) -> None:
        self._emit(StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request_id, self.session,
                                     self.clock_ns(), detail=detail))

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                request_id, follower = self._active_request, self._follower
            if request_id is not None:
                try:
                    if not self.identity_check():
                        raise RuntimeError("session_identity_changed")
                    query_started = self.clock_ns()
                    value = self.slot_getter()
                    observed = self.clock_ns()
                    for event in strict_slot_evidence(value, request_id=request_id, session=self.session,
                                                      query_started_ns=query_started,
                                                      observed_at_ns=observed):
                        self._emit(event)
                    if follower is None:
                        raise RuntimeError("monitor_log_follower_missing")
                    records = follower.read()
                    for record in records:
                        event = parse_server_log(record, request_id=request_id, session=self.session,
                                                 observed_at_ns=self.clock_ns())
                        if event is not None:
                            self._emit(event)
                    if records:
                        self._emit(StreamingEvidence(EvidenceKind.LOG_CONTINUITY, request_id,
                                                     self.session, self.clock_ns(),
                                                     log_offset=records[-1].end_offset))
                except Exception as exc:
                    self._failure(request_id, f"{type(exc).__name__}: {exc}")
                    with self._state_lock:
                        if self._active_request == request_id:
                            self._active_request, self._follower = None, None
            self._stop.wait(self.poll_interval_s)

    def drain(self, limit: int = 256) -> list[StreamingEvidence]:
        if limit <= 0:
            raise ValueError("Drain limit must be positive")
        events = []
        if self._overflow.is_set():
            self._overflow.clear()
            request_id, self._overflow_request = self._overflow_request, None
            if request_id is not None:
                events.append(StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request_id,
                                                self.session, self.clock_ns(),
                                                detail="monitor_event_queue_overflow"))
        for _ in range(limit - len(events)):
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                break
        return events

    def close(self, timeout_s: float = 2) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise TimeoutError("Session monitor did not stop before cleanup deadline")

