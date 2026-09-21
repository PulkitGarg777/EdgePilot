"""Run guarded simulated missions against the existing resident llama.cpp model."""

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

from .__main__ import ROOT, configure_paths, get_json, sha256
from .contracts import Policy
from .mission_runtime import MissionEpisode, SCENARIOS, TRACE_SCHEMA
from .robot.contracts import NS
from .robot.model import ACTION_SCHEMA, ModelHttpWorker, PROMPT_VERSION
from .simulation import seconds_to_ns


def inventory(args) -> dict:
    health = get_json(args.base_url.rstrip("/") + "/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"Server not ready: {health}")
    props = get_json(args.base_url.rstrip("/") + "/props")
    import psutil
    processes = [{"pid": p.pid, **p.info} for p in psutil.process_iter(["name", "exe", "cmdline"])
                 if (p.info["name"] or "").lower() == "llama-server.exe"]
    return {
        "trace_schema": TRACE_SCHEMA, "configuration": vars(args), "scenario": [asdict(e) for e in SCENARIOS[args.scenario]],
        "test_kind": "real_model_simulation", "health": health, "server_props": props,
        "server_processes": processes, "model_sha256": sha256(Path(props["model_path"])),
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in sorted((ROOT/"edgepilot").rglob("*.py"))},
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout,
        "prompt_version": PROMPT_VERSION, "action_schema": ACTION_SCHEMA,
        "generation": {"temperature": 0, "seed": 0, "stream": False, "cache_prompt": False, "enable_thinking": False},
        "timing": "Episode-relative perf_counter_ns; inventory/model hashing excluded. Validation CPU uses thread_time_ns; "
                  "validation wall time uses perf_counter_ns. Zero CPU samples can be below clock granularity.",
        "clock_info": {name: vars(time.get_clock_info(name)) for name in ("perf_counter", "thread_time")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=[p.value for p in Policy], required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="nominal")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="edgepilot")
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--deadline", type=seconds_to_ns, default=45*NS)
    parser.add_argument("--request-ttl", type=seconds_to_ns, default=None,
                        help="Optional request lifetime in seconds, including queue wait. Disabled by default.")
    parser.add_argument("--socket-timeout", type=seconds_to_ns, default=20*NS)
    parser.add_argument("--tick", type=seconds_to_ns, default=NS//20)
    args = parser.parse_args()
    if min(args.max_tokens, args.max_requests, args.deadline, args.socket_timeout, args.tick) <= 0 or args.request_ttl == 0:
        parser.error("Budgets, intervals, and deadlines must be positive")
    configure_paths()
    run_dir = ROOT / "runs" / ("mission-" + time.strftime("%Y%m%d-%H%M%S") + "-" + args.policy + "-" +
                               args.scenario + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        metadata = inventory(args)
    except Exception as exc:
        (run_dir/"startup-failure.json").write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), encoding="utf-8")
        parser.exit(1, f"Startup failed: {exc}\nDetails: {run_dir}\n")
    (run_dir/"metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    start_ns = time.perf_counter_ns()
    clock = lambda: time.perf_counter_ns()-start_ns
    worker = ModelHttpWorker(args.base_url, run_dir, clock, args.socket_timeout/NS)
    with (run_dir/"events.jsonl").open("w", encoding="utf-8") as log:
        def emit(**event):
            log.write(json.dumps({"trace_schema": TRACE_SCHEMA, **event})+"\n")
            log.flush()
        episode = MissionEpisode(Policy(args.policy), worker, SCENARIOS[args.scenario], emit=emit,
                                 deadline_ns=args.deadline, model_id=args.model, max_tokens=args.max_tokens,
                                 max_requests=args.max_requests, request_ttl_ns=args.request_ttl,
                                 validation_clock=time.thread_time_ns, validation_wall_clock=time.perf_counter_ns)
        try:
            while not episode.step(clock()):
                time.sleep(args.tick/NS)
        finally:
            # Active HTTP is retained/drained after deadline; shutdown never claims cancellation.
            worker.close()
    summary = {**episode.summary(), "scenario": args.scenario, "test_kind": "real_model_simulation",
               "run_directory": str(run_dir)}
    (run_dir/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return int(summary["episode_outcome"] != "completed" or summary["mission_outcome"] != "succeeded" or
               any(summary["terminal_counts"].get(s, 0) for s in ("worker_failed", "http_timed_out")))


if __name__ == "__main__":
    raise SystemExit(main())
