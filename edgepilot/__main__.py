"""Run a finite wait-only scheduling episode against the existing llama.cpp server."""

import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request
import uuid

from . import __version__
from .contracts import Policy
from .http_worker import HttpWorker
from .scenarios import SCENARIOS
from .scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]


def configure_paths():
    for name, relative in {"TEMP": "tmp", "TMP": "tmp", "PIP_CACHE_DIR": "cache/pip",
                           "HF_HOME": "cache/huggingface"}.items():
        path = ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)


def get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def metadata(args):
    health = get_json(args.base_url.rstrip("/") + "/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"Server not ready: {health}")
    props = get_json(args.base_url.rstrip("/") + "/props")
    model_path = Path(props["model_path"])
    # Read runtime configuration from the running server, not the handoff snapshot.
    import psutil
    processes = []
    for process in psutil.process_iter(["name", "exe", "cmdline"]):
        if (process.info["name"] or "").lower() == "llama-server.exe":
            processes.append({"pid": process.pid, **process.info})
    git = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT,
                         capture_output=True, text=True, check=False)
    sources = list((ROOT / "edgepilot").glob("*.py")) + [ROOT / "runtime_probe_v1/probe.py"]
    recorded_bytes = (ROOT / "runs/model-sha256.txt").read_bytes()
    recorded_hash = recorded_bytes.decode("utf-16" if recorded_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    model_hash = sha256(model_path)
    return {
        "edgepilot_version": __version__, "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "health": health, "server_props": props,
        "server_processes": processes, "model_sha256": model_hash,
        "recorded_model_hash_file": recorded_hash,
        "model_matches_recorded_hash": model_hash.lower() in recorded_hash.lower(),
        "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
        "configuration": vars(args), "scenario": asdict(SCENARIOS[args.scenario]),
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
        "git_status": {"returncode": git.returncode, "stdout": git.stdout, "stderr": git.stderr},
        "timing": "Episode-relative perf_counter; metadata collection and health checks excluded",
        "test_kind": "real_model_http",
    }


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Use finite positive seconds")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=[p.value for p in Policy], required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--deadline", type=positive, default=20,
                        help="Stop admission/acceptance, then drain active HTTP; not a hard process deadline")
    parser.add_argument("--socket-timeout", type=positive, default=10,
                        help="Socket timeout, not a hard overall HTTP deadline")
    parser.add_argument("--tick", type=positive, default=.05)
    args = parser.parse_args()
    configure_paths()
    run_dir = ROOT / "runs" / ("pilot-" + time.strftime("%Y%m%d-%H%M%S") + "-" + args.policy + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        info = metadata(args)
    except Exception as exc:
        (run_dir / "startup-failure.json").write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), encoding="utf-8")
        parser.exit(1, f"Startup failed: {exc}\nDetails: {run_dir}\n")
    (run_dir / "metadata.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    started = time.perf_counter()
    clock = lambda: time.perf_counter() - started
    worker = HttpWorker(args.base_url, run_dir, clock, args.socket_timeout)
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as log:
        def emit(**event):
            log.write(json.dumps(event) + "\n")
            log.flush()
        scheduler = Scheduler(Policy(args.policy), SCENARIOS[args.scenario], worker, emit, args.tick, args.deadline)
        try:
            while not scheduler.step(clock()):
                time.sleep(.005)
        finally:
            worker.close()
    summary = {**scheduler.summary(), "run_directory": str(run_dir), "test_kind": "real_model_http"}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    failures = ("failed", "http_timed_out", "rejected_invalid_plan")
    return int(summary["episode_outcome"] != "completed" or
               any(summary["terminal_counts"].get(state, 0) for state in failures))


if __name__ == "__main__":
    raise SystemExit(main())
