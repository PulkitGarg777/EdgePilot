"""EP-016 owned transport and strict SSE reconstruction tests."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from edgepilot.streaming_transport import OwnedStreamingCall, reconstruct_chat_response


def http(body: bytes, *, content_type="text/event-stream", chunked=False, status="200 OK"):
    headers = f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
    if chunked:
        framed = b"".join(f"{len(part):x}\r\n".encode() + part + b"\r\n"
                          for part in (body[:7], body[7:])) + b"0\r\n\r\n"
        return headers.encode() + b"Transfer-Encoding: chunked\r\n\r\n" + framed
    return headers.encode() + f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def sse(*records):
    return "".join("data: " + (record if isinstance(record, str) else json.dumps(record)) + "\n\n"
                   for record in records).encode()


def test_reconstructs_chunked_content_finish_and_usage():
    body = sse(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "café"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        "[DONE]",
    )
    result = reconstruct_chat_response(http(body, chunked=True))
    assert result["choices"][0]["message"]["content"] == "café"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["completion_tokens"] == 2


@pytest.mark.parametrize("raw, message", [
    (http(sse({"choices": [{"delta": {"content": "x"}, "finish_reason": None}]})),
     "terminal evidence"),
    (http(b'data: {"choices": []}', chunked=False), "Truncated SSE"),
    (http(sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}, "[DONE]", "[DONE]")),
     "Duplicate SSE done"),
    (http(sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}, "[DONE]"),
          content_type="application/json"), "text/event-stream"),
    (http(sse({"choices": [{"index": 1, "delta": {}, "finish_reason": "stop"}]}, "[DONE]")),
     "choice index zero"),
])
def test_rejects_incomplete_or_ambiguous_streams(raw, message):
    with pytest.raises(ValueError, match=message):
        reconstruct_chat_response(raw)


def test_owned_call_connects_in_background_and_preserves_raw_response(tmp_path):
    entered, release = threading.Event(), threading.Event()
    body = sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            entered.set()
            release.wait(2)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    call = OwnedStreamingCall(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", {"stream": True})
    try:
        call.start()
        assert entered.wait(2) and not call.done
        release.set()
        for _ in range(1000):
            if call.done:
                break
            threading.Event().wait(.001)
        result = call.close()
        assert result.error is None and not result.disconnect_requested
        assert reconstruct_chat_response(result.raw)["choices"][0]["message"]["content"] == "ok"
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_disconnect_closes_owned_socket_and_reader_exits():
    entered, client_closed = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            entered.set()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"8\r\ndata: {}\r\n")
                self.wfile.flush()
                self.connection.recv(1)
            except OSError:
                pass
            finally:
                client_closed.set()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    call = OwnedStreamingCall(f"http://127.0.0.1:{server.server_port}/chat", {"stream": True})
    try:
        call.start()
        assert entered.wait(2)
        disconnected_at = call.request_disconnect()
        result = call.close()
        assert client_closed.wait(2)
        assert result.disconnect_requested and result.disconnect_finished_ns == disconnected_at
        assert result.error is None and call.done
    finally:
        server.shutdown()
        server.server_close()


def test_response_size_bound_fails_closed():
    body = b"x" * 200

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    call = OwnedStreamingCall(f"http://127.0.0.1:{server.server_port}/chat", {}, max_response_bytes=100)
    try:
        call.start()
        for _ in range(1000):
            if call.done:
                break
            threading.Event().wait(.001)
        result = call.close()
        assert "exceeded configured byte bound" in result.error
    finally:
        server.shutdown()
        server.server_close()
