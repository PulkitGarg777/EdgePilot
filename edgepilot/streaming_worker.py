"""EP-016 streaming model worker with acknowledged terminal reuse gating."""

from dataclasses import asdict
import json
from pathlib import Path
from typing import Callable, Protocol

from .contracts import FailureKind
from .robot.contracts import NS
from .robot.model import ModelRequest, ModelResult, payload_for
from .streaming_contracts import (
    EvidenceKind, StopReceipt, StopReceiptKind, StreamingEvidence, TerminalKind,
    WorkerState,
)
from .streaming_evidence import EvidenceState, reduce_evidence
from .streaming_transport import OwnedStreamingCall, StreamingTransportResult, reconstruct_chat_response


class EvidenceMonitor(Protocol):
    session: object
    def observe_request(self, request_id: str) -> None: ...
    def clear_request(self, request_id: str) -> None: ...
    def drain(self, limit: int = 256) -> list[StreamingEvidence]: ...
    def close(self, timeout_s: float = 2) -> None: ...


class StreamingCall(Protocol):
    def start(self) -> None: ...
    def request_disconnect(self) -> int: ...
    @property
    def done(self) -> bool: ...
    def result(self) -> StreamingTransportResult: ...
    def close(self, timeout_s: float = 2) -> StreamingTransportResult: ...


class AcknowledgedStreamingWorker:
    """One request at a time; clear occupancy only after reducer terminal state."""

    def __init__(self, base_url: str, run_dir: Path, clock_ns: Callable[[], int],
                 monitor: EvidenceMonitor, *, socket_timeout_s: float = 20,
                 call_factory: Callable[[str, dict], StreamingCall] | None = None):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.run_dir, self.clock_ns, self.monitor = run_dir, clock_ns, monitor
        self.socket_timeout_s = socket_timeout_s
        self.call_factory = call_factory or (
            lambda url, payload: OwnedStreamingCall(url, payload, timeout_s=socket_timeout_s,
                                                    clock_ns=clock_ns))
        self.request: ModelRequest | None = None
        self.call: StreamingCall | None = None
        self.evidence: EvidenceState | None = None
        self.response: dict | None = None
        self.transport: StreamingTransportResult | None = None
        self._deferred: list[StreamingEvidence] = []
        self._transport_recorded = False
        self._result_emitted = False
        self.session_poisoned = False

    def submit(self, request: ModelRequest) -> None:
        if self.session_poisoned:
            raise RuntimeError("Streaming session is poisoned and cannot be reused")
        if self.request is not None:
            raise RuntimeError("Streaming worker remains occupied until terminal acknowledgement")
        payload = {**payload_for(request), "stream": True, "stream_options": {"include_usage": True}}
        record = {"request": asdict(request), "url": self.url, "payload": payload,
                  "streaming_schema": "edgepilot.streaming.v1"}
        (self.run_dir / f"{request.request_id}-stream-request.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        call = self.call_factory(self.url, payload)
        self.monitor.observe_request(request.request_id)
        try:
            call.start()
        except Exception:
            self.monitor.clear_request(request.request_id)
            raise
        self.request, self.call = request, call
        self.evidence = EvidenceState(request.request_id, self.monitor.session)
        self.response = self.transport = None
        self._deferred.clear()
        self._transport_recorded = self._result_emitted = False

    def _apply(self, event: StreamingEvidence) -> None:
        if self.evidence is None:
            raise RuntimeError("Evidence arrived without an active request")
        self.evidence = reduce_evidence(self.evidence, event)

    def request_cancel(self, request_id: str, reason: str, now_ns: int) -> StopReceipt:
        request, state, call = self.request, self.evidence, self.call
        if request is None or state is None or call is None or request_id != request.request_id:
            return StopReceipt(request_id, StopReceiptKind.WRONG_REQUEST, now_ns, reason)
        if state.state in (WorkerState.TERMINAL_READY, WorkerState.POISONED):
            return StopReceipt(request_id, StopReceiptKind.ALREADY_TERMINAL, now_ns, reason)
        if state.stop_intent_ns is not None:
            return StopReceipt(request_id, StopReceiptKind.ALREADY_PENDING, now_ns, reason)
        if state.task_id is None or state.generation_at_ns is None:
            return StopReceipt(request_id, StopReceiptKind.UNSUPPORTED_PHASE, now_ns,
                               "generation_not_confirmed")
        self._apply(StreamingEvidence(EvidenceKind.STOP_INTENDED, request_id, state.session,
                                      now_ns, task_id=state.task_id, detail=reason))
        try:
            shutdown_ns = call.request_disconnect()
        except Exception as exc:
            self._apply(StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request_id, state.session,
                                          self.clock_ns(), detail=f"disconnect_failed: {exc}"))
            return StopReceipt(request_id, StopReceiptKind.RECORDED, now_ns, "disconnect_failed")
        self._apply(StreamingEvidence(EvidenceKind.SOCKET_SHUTDOWN, request_id, state.session,
                                      shutdown_ns, task_id=state.task_id))
        return StopReceipt(request_id, StopReceiptKind.RECORDED, now_ns, reason)

    def _transport_events(self, now_ns: int) -> list[StreamingEvidence]:
        request, call, state = self.request, self.call, self.evidence
        if self._transport_recorded or request is None or call is None or state is None or not call.done:
            return []
        result = call.result()
        if result.finished_ns > now_ns:
            return []
        self.transport, self._transport_recorded = result, True
        events = []
        raw_path = self.run_dir / f"{request.request_id}-stream-response.raw"
        try:
            raw_path.write_bytes(result.raw)
        except OSError as exc:
            return [StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request.request_id,
                                      state.session, now_ns,
                                      detail=f"raw_trace_write_failed: {exc}")]
        if result.error is not None:
            return [StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request.request_id,
                                      state.session, result.finished_ns,
                                      detail="transport_error: " + result.error)]
        if not result.disconnect_requested:
            try:
                self.response = reconstruct_chat_response(result.raw)
                reason = self.response["choices"][0]["finish_reason"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                return [StreamingEvidence(EvidenceKind.EVIDENCE_FAILURE, request.request_id,
                                          state.session, result.finished_ns,
                                          detail=f"stream_reconstruction_failed: {exc}")]
            events.append(StreamingEvidence(EvidenceKind.STREAM_TERMINAL, request.request_id,
                                            state.session, result.finished_ns,
                                            stream_finish_reason=reason))
        events.append(StreamingEvidence(EvidenceKind.READER_EXITED, request.request_id,
                                        state.session, result.finished_ns))
        events.append(StreamingEvidence(EvidenceKind.TRACE_SAVED, request.request_id,
                                        state.session, max(result.finished_ns, self.clock_ns())))
        return events

    def poll(self, now_ns: int) -> ModelResult | None:
        if self.request is None or self.evidence is None:
            return None
        self._deferred.extend(self.monitor.drain())
        self._deferred.extend(self._transport_events(now_ns))
        self._deferred.sort(key=lambda event: event.observed_at_ns)
        remaining = []
        for event in self._deferred:
            if event.observed_at_ns <= now_ns:
                self._apply(event)
            else:
                remaining.append(event)
        self._deferred = remaining
        terminal = self.evidence.terminal
        if terminal is None or self._result_emitted:
            return None
        request, transport = self.request, self.transport
        started = transport.started_ns if transport else request.created_at_ns
        finished = terminal.observed_at_ns
        usage = self.response.get("usage", {}) if isinstance(self.response, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        kind = terminal.kind
        error = None if kind in (TerminalKind.COMPLETED, TerminalKind.CANCELLED) else terminal.reason
        failure = None if error is None else FailureKind.ERROR
        result = ModelResult(
            request.request_id, started, finished,
            response=self.response if kind == TerminalKind.COMPLETED else None,
            error=error, failure_kind=failure,
            server_state_uncertain=kind == TerminalKind.UNCERTAIN,
            prompt_tokens=usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else None,
            completion_tokens=(usage.get("completion_tokens")
                               if type(usage.get("completion_tokens")) is int else None),
            termination=kind.value, terminal_observed_at_ns=finished,
            occupied_ns=finished-started, session_id=terminal.session_id,
            server_task_id=terminal.task_id, evidence_ref=request.request_id + "-stream-response.raw",
        )
        if kind == TerminalKind.UNCERTAIN:
            self.session_poisoned = True
        self._result_emitted = True
        self.monitor.clear_request(request.request_id)
        self.request = self.call = self.evidence = None
        self.response = self.transport = None
        self._deferred.clear()
        return result

    def close(self, timeout_s: float = 2) -> None:
        failure = None
        if self.call is not None:
            try:
                self.call.close(timeout_s)
            except Exception as exc:
                failure = exc
        try:
            self.monitor.close(timeout_s)
        except Exception as exc:
            failure = failure or exc
        if failure is not None:
            raise failure
