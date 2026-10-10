"""Owned HTTP/1.1 streaming transport and strict llama.cpp SSE reconstruction."""

from dataclasses import dataclass
import codecs
import json
import socket
import threading
import time
from typing import Callable
from urllib.parse import urlsplit


MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class StreamingTransportResult:
    started_ns: int
    finished_ns: int
    raw: bytes
    error: str | None
    disconnect_requested: bool
    disconnect_finished_ns: int | None


class OwnedStreamingCall:
    """A background HTTP call whose exact socket can be shut down by its owner."""

    def __init__(self, url: str, payload: dict, *, timeout_s: float = 20,
                 clock_ns: Callable[[], int] = time.perf_counter_ns,
                 max_response_bytes: int = MAX_BODY_BYTES + MAX_HEADER_BYTES):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Streaming cancellation initially supports loopback HTTP only")
        if timeout_s <= 0 or max_response_bytes <= 0:
            raise ValueError("Timeout and response bound must be positive")
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.target = parsed.path or "/"
        if parsed.query:
            self.target += "?" + parsed.query
        self.payload, self.timeout_s = payload, timeout_s
        self.clock_ns, self.max_response_bytes = clock_ns, max_response_bytes
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._disconnect_requested = threading.Event()
        self._raw = bytearray()
        self._started_ns = 0
        self._finished_ns = 0
        self._disconnect_finished_ns: int | None = None
        self._error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Call already started")
        self._thread = threading.Thread(target=self._run, name="edgepilot-stream-http", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        connection = None
        try:
            self._started_ns = self.clock_ns()
            body = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
            request = (
                f"POST {self.target} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                "Accept: text/event-stream\r\nConnection: close\r\n\r\n"
            ).encode("ascii") + body
            connection = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
            connection.settimeout(self.timeout_s)
            with self._lock:
                if self._disconnect_requested.is_set():
                    connection.close()
                    connection = None
                    return
                self._socket = connection
            connection.sendall(request)
            while not self._disconnect_requested.is_set():
                chunk = connection.recv(65536)
                if not chunk:
                    break
                if len(self._raw) + len(chunk) > self.max_response_bytes:
                    raise ValueError("Streaming response exceeded configured byte bound")
                self._raw.extend(chunk)
        except Exception as exc:
            if not self._disconnect_requested.is_set():
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                if self._socket is connection:
                    self._socket = None
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
            self._finished_ns = self.clock_ns()
            self._done.set()

    def request_disconnect(self) -> int:
        """Record intent and close the owned socket without waiting for reader exit."""
        with self._lock:
            if self._disconnect_finished_ns is not None:
                return self._disconnect_finished_ns
            self._disconnect_requested.set()
            connection, self._socket = self._socket, None
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            self._disconnect_finished_ns = self.clock_ns()
            return self._disconnect_finished_ns

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def result(self) -> StreamingTransportResult:
        if not self.done:
            raise RuntimeError("Transport has not reached reader exit")
        return StreamingTransportResult(self._started_ns, self._finished_ns, bytes(self._raw),
                                        self._error, self._disconnect_requested.is_set(),
                                        self._disconnect_finished_ns)

    def close(self, timeout_s: float = 2) -> StreamingTransportResult:
        if self._thread is None:
            raise RuntimeError("Call has not started")
        if not self.done:
            self.request_disconnect()
        if not self._done.wait(timeout_s):
            raise TimeoutError("Streaming reader did not exit before cleanup deadline")
        self._thread.join(0)
        return self.result()


def _decode_chunked(body: bytes, *, max_body_bytes: int) -> bytes:
    decoded = bytearray()
    while True:
        line, marker, rest = body.partition(b"\r\n")
        if not marker:
            raise ValueError("Truncated HTTP chunk size")
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError as exc:
            raise ValueError("Invalid HTTP chunk size") from exc
        body = rest
        if size == 0:
            # Accept an empty trailer section or bounded trailer fields.
            if body == b"\r\n":
                return bytes(decoded)
            if not body.endswith(b"\r\n\r\n"):
                raise ValueError("Truncated HTTP trailers")
            return bytes(decoded)
        if len(body) < size + 2 or body[size:size + 2] != b"\r\n":
            raise ValueError("Truncated HTTP chunk data")
        decoded.extend(body[:size])
        if len(decoded) > max_body_bytes:
            raise ValueError("HTTP body exceeded configured byte bound")
        body = body[size + 2:]


def http_body(raw: bytes, *, max_body_bytes: int = MAX_BODY_BYTES) -> tuple[dict[str, str], bytes]:
    if len(raw) > MAX_HEADER_BYTES + max_body_bytes:
        raise ValueError("HTTP response exceeded configured byte bound")
    head, marker, body = raw.partition(b"\r\n\r\n")
    if not marker or len(head) > MAX_HEADER_BYTES:
        raise ValueError("Missing or oversized HTTP headers")
    lines = head.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or parts[0] != "HTTP/1.1" or not parts[1].isdigit():
        raise ValueError("Invalid HTTP status line")
    if int(parts[1]) != 200:
        raise ValueError(f"HTTP request failed with status {parts[1]}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            raise ValueError("Invalid HTTP header")
        key = name.strip().lower()
        if key in headers:
            raise ValueError("Duplicate HTTP header")
        headers[key] = value.strip()
    transfer = headers.get("transfer-encoding", "").lower()
    if transfer:
        if transfer != "chunked" or "content-length" in headers:
            raise ValueError("Unsupported or ambiguous HTTP framing")
        body = _decode_chunked(body, max_body_bytes=max_body_bytes)
    elif "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > max_body_bytes or len(body) != length:
            raise ValueError("Truncated or oversized HTTP body")
    else:
        raise ValueError("Response lacks explicit HTTP framing")
    return headers, body


def _sse_records(body: bytes) -> list[str]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        text = decoder.decode(body, final=True).replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("SSE body is not valid UTF-8") from exc
    if not text.endswith("\n\n"):
        raise ValueError("Truncated SSE record")
    records = []
    for block in text[:-2].split("\n\n"):
        values = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if values:
            records.append("\n".join(values))
    return records


def reconstruct_chat_response(raw: bytes) -> dict:
    """Validate a complete SSE response and rebuild the existing response envelope."""
    headers, body = http_body(raw)
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "text/event-stream":
        raise ValueError("Expected text/event-stream response")
    content: list[str] = []
    finish_reason = None
    usage = None
    done = False
    for record in _sse_records(body):
        if record == "[DONE]":
            if done:
                raise ValueError("Duplicate SSE done marker")
            done = True
            continue
        if done:
            raise ValueError("SSE data followed the done marker")
        try:
            value = json.loads(record)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed SSE JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("choices"), list):
            raise ValueError("SSE record lacks a choices array")
        if value.get("usage") is not None:
            if usage is not None or not isinstance(value["usage"], dict):
                raise ValueError("Invalid or duplicate streaming usage")
            usage = value["usage"]
        for choice in value["choices"]:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                raise ValueError("Only choice index zero is supported")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ValueError("Streaming choice lacks a delta object")
            piece = delta.get("content")
            if piece is not None:
                if not isinstance(piece, str) or finish_reason is not None:
                    raise ValueError("Invalid content after stream termination")
                content.append(piece)
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str) or finish_reason is not None:
                    raise ValueError("Invalid or duplicate finish reason")
                finish_reason = reason
    if not done or finish_reason is None:
        raise ValueError("Streaming response lacks terminal evidence")
    result = {"choices": [{"index": 0, "finish_reason": finish_reason,
                            "message": {"role": "assistant", "content": "".join(content)}}]}
    if usage is not None:
        result["usage"] = usage
    return result
