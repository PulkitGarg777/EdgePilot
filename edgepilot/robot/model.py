"""Bounded model skill plans and provenance; shared HTTP transport, no cancellation."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Protocol

from ..contracts import Goal, Observation, Request, FailureKind
from ..http_worker import HttpWorker
from .contracts import Action, MAX_ACTIONS, NS, Plan, Snapshot, integer

PROMPT_VERSION = "station-planner-v1"
ACTION_SCHEMA = {
    "type": "object", "properties": {"actions": {"type": "array", "minItems": 1, "maxItems": MAX_ACTIONS,
        "items": {"anyOf": [
            {"type": "object", "properties": {"skill": {"type": "string", "enum": ["navigate_to", "inspect"]},
              "target": {"type": "string", "enum": ["station_a", "station_b"]}},
             "required": ["skill", "target"], "additionalProperties": False},
            {"type": "object", "properties": {"skill": {"type": "string", "enum": ["wait"]},
              "duration_ns": {"type": "integer", "minimum": 1, "maximum": 10*NS}},
             "required": ["skill", "duration_ns"], "additionalProperties": False},
            {"type": "object", "properties": {"skill": {"type": "string", "enum": ["return_home"]}},
             "required": ["skill"], "additionalProperties": False},
        ]}}}, "required": ["actions"], "additionalProperties": False,
}


def prompt_for(snapshot: Snapshot) -> str:
    state = {"goal_version": snapshot.mission.version, "required_stations": snapshot.mission.required_stations,
             "already_inspected": snapshot.inspected, "robot_position": asdict(snapshot.position) if snapshot.position else None,
             "stations": [asdict(s) for s in snapshot.stations], "home": {"x": 0, "y": 0}}
    return (
        "Plan a complete mission for this simulated robot. Return only JSON with exactly one field, actions. "
        "For each required station not already inspected, navigate_to that station, then inspect it. "
        "After all required inspections, return_home. Only inspect a station after navigating to it. "
        "Do not repeat inspections already completed. Do not omit required stations or invent targets. "
        "Skills: navigate_to and inspect take target; return_home takes no arguments; "
        "wait takes integer duration_ns up to 10000000000. "
        "If a remaining required station is unavailable or unknown, wait rather than skip it. "
        "Otherwise produce the full navigation/inspection/return sequence, without wait. "
        "Example action format: {\"skill\":\"navigate_to\",\"target\":\"station_a\"}. "
        "Current state: " + json.dumps(state, sort_keys=True, separators=(",", ":"))
    )


@dataclass(frozen=True)
class ModelRequest:
    request_id: str
    created_at_ns: int
    snapshot: Snapshot
    model_id: str
    prompt: str
    prompt_sha256: str
    max_tokens: int
    deadline_ns: int | None = None
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.request_id, self.model_id, self.prompt, self.prompt_version)):
            raise ValueError("Request and model identity required")
        integer(self.created_at_ns, "request time")
        if not isinstance(self.snapshot, Snapshot) or self.snapshot.observed_at_ns > self.created_at_ns:
            raise ValueError("Request needs a snapshot already observed")
        integer(self.max_tokens, "output budget", 1)
        if self.deadline_ns is not None:
            integer(self.deadline_ns, "request deadline")
            if self.deadline_ns < self.created_at_ns:
                raise ValueError("Deadline precedes creation")
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != self.prompt_sha256:
            raise ValueError("Prompt hash does not match")


def make_request(request_id: str, now_ns: int, snapshot: Snapshot, model_id: str = "edgepilot",
                 max_tokens: int = 384, deadline_ns: int | None = None) -> ModelRequest:
    prompt = prompt_for(snapshot)
    return ModelRequest(request_id, now_ns, snapshot, model_id, prompt,
                        hashlib.sha256(prompt.encode("utf-8")).hexdigest(), max_tokens, deadline_ns)


def payload_for(request: ModelRequest) -> dict:
    return {"model": request.model_id, "messages": [{"role": "user", "content": request.prompt}],
            "temperature": 0, "seed": 0, "max_tokens": request.max_tokens, "stream": False,
            "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object", "schema": ACTION_SCHEMA}}


def decode_plan(response: Any, request: ModelRequest) -> Plan:
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or not response["choices"]:
        raise ValueError("Expected a nonempty choices array")
    choice = response["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("Expected a choice with a message object")
    if choice.get("finish_reason") != "stop":
        raise ValueError("Generation did not finish normally; truncated plans cannot execute")
    if not isinstance(choice["message"].get("content"), str):
        raise ValueError("Expected message content as a JSON string")
    content = json.loads(choice["message"]["content"])
    if not isinstance(content, dict) or set(content) != {"actions"} or not isinstance(content["actions"], list):
        raise ValueError("Expected exactly one actions array")
    # IDs are bound to the HTTP request by the client, never invented by the model.
    return Plan(request.request_id, request.snapshot.mission.version, request.snapshot.snapshot_id,
                tuple(Action.from_dict(action) for action in content["actions"]))


@dataclass(frozen=True)
class ModelResult:
    request_id: str
    started_at_ns: int
    finished_at_ns: int
    response: Any = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    server_state_uncertain: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelWorker(Protocol):
    def submit(self, request: ModelRequest) -> None: ...
    def poll(self, now_ns: int) -> ModelResult | None: ...
    def close(self) -> None: ...


class ModelHttpWorker:
    def __init__(self, base_url: str, run_dir: Path, clock_ns: Callable[[], int], socket_timeout: float = 20):
        self.request: ModelRequest | None = None
        self.run_dir = run_dir
        self.transport = HttpWorker(base_url, run_dir, lambda: clock_ns()/NS, socket_timeout,
                                    payload_builder=lambda _: payload_for(self.request))

    def submit(self, request: ModelRequest):
        if self.request is not None:
            raise RuntimeError("Model worker occupied")
        (self.run_dir / f"{request.request_id}-planning-request.json").write_text(
            json.dumps(asdict(request), indent=2), encoding="utf-8")
        self.request = request
        # Compatibility envelope for the already-tested resident HTTP transport.
        old = Request(request.request_id, request.created_at_ns/NS,
                      Goal(request.snapshot.mission.version, request.snapshot.mission.created_at_ns/NS,
                           request.snapshot.mission.created_at_ns/NS, "Inspect required stations and return home"),
                      Observation(request.snapshot.snapshot_id, request.snapshot.observed_at_ns/NS, "structured_scene"))
        try:
            self.transport.submit(old, request.created_at_ns/NS)
        except Exception:
            self.request = None
            raise

    def poll(self, now_ns: int) -> ModelResult | None:
        result = self.transport.poll(now_ns/NS)
        if result is None:
            return None
        usage = result.response.get("usage", {}) if isinstance(result.response, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        def count(name):
            value = usage.get(name)
            return value if type(value) is int and value >= 0 else None
        self.request = None
        return ModelResult(result.request_id, round(result.started_at*NS), min(now_ns, round(result.finished_at*NS)),
                           result.response, result.error, result.failure_kind, result.server_state_uncertain,
                           count("prompt_tokens"), count("completion_tokens"))

    def close(self):
        self.transport.close()
