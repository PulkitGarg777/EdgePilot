"""Run a guarded mission through a freshly owned streaming llama.cpp session."""

import argparse
import json
from pathlib import Path
import time
import uuid

from .__main__ import ROOT, configure_paths
from .contracts import Policy
from .mission_runtime import MissionEpisode, SCENARIOS, TRACE_SCHEMA
from .robot.contracts import NS
from .simulation import seconds_to_ns
from .streaming_session import OwnedServerConfig, OwnedServerSession
from .streaming_worker import AcknowledgedStreamingWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("finish", "cancel-goal-replacement"), default="finish")
    parser.add_argument("--policy", choices=[policy.value for policy in Policy],
                        default=Policy.NEWEST_PENDING.value)
    parser.add_argument("--scenario", choices=SCENARIOS, default="nominal")
    parser.add_argument("--executable", type=Path,
                        default=ROOT / "tools/llama-b11065/llama-server.exe")
    parser.add_argument("--model-path", type=Path,
                        default=ROOT / "models/Qwen3-0.6B-Q8_0.gguf")
    parser.add_argument("--model", default="edgepilot")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--deadline", type=seconds_to_ns, default=45 * NS)
    parser.add_argument("--request-ttl", type=seconds_to_ns, default=None)
    parser.add_argument("--socket-timeout", type=seconds_to_ns, default=20 * NS)
    parser.add_argument("--startup-timeout", type=seconds_to_ns, default=60 * NS)
    parser.add_argument("--tick", type=seconds_to_ns, default=NS // 20)
    return parser


def validate_args(parser: argparse.ArgumentParser, args) -> None:
    values = (args.max_tokens, args.max_requests, args.deadline, args.socket_timeout,
              args.startup_timeout, args.tick)
    if min(values) <= 0 or args.request_ttl == 0:
        parser.error("Budgets, intervals, and deadlines must be positive")
    if (args.arm == "cancel-goal-replacement"
            and (args.scenario != "goal-replacement" or args.policy != Policy.NEWEST_PENDING.value)):
        parser.error("The cancellation arm requires --scenario goal-replacement and --policy newest-pending")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    configure_paths()
    run_dir = ROOT / "runs" / ("streaming-mission-" + time.strftime("%Y%m%d-%H%M%S")
                               + "-" + args.arm + "-" + args.scenario + "-"
                               + uuid.uuid4().hex[:8])
    session = worker = None
    episode = None
    try:
        config = OwnedServerConfig(args.executable, args.model_path, run_dir,
                                   port=args.port,
                                   startup_timeout_s=args.startup_timeout / NS,
                                   request_timeout_s=min(3, args.socket_timeout / NS),
                                   synchronization_audited=(args.arm == "cancel-goal-replacement"))
        session = OwnedServerSession.launch(config)
        start_ns = time.perf_counter_ns()
        clock = lambda: time.perf_counter_ns() - start_ns
        monitor = session.create_monitor(clock_ns=clock)
        monitor.start()
        worker = AcknowledgedStreamingWorker(session.base_url, run_dir, clock, monitor,
                                             socket_timeout_s=args.socket_timeout / NS)
        with (run_dir / "events.jsonl").open("w", encoding="utf-8") as log:
            def emit(**event):
                log.write(json.dumps({"trace_schema": TRACE_SCHEMA, **event}) + "\n")
                log.flush()

            episode = MissionEpisode(
                Policy(args.policy), worker, SCENARIOS[args.scenario], emit=emit,
                deadline_ns=args.deadline, model_id=args.model, max_tokens=args.max_tokens,
                max_requests=args.max_requests, request_ttl_ns=args.request_ttl,
                validation_clock=time.thread_time_ns,
                validation_wall_clock=time.perf_counter_ns,
                cancel_on_goal_replacement=(args.arm == "cancel-goal-replacement"),
            )
            while not episode.step(clock()):
                time.sleep(args.tick / NS)
    except Exception as exc:
        failure = {"error": f"{type(exc).__name__}: {exc}", "run_directory": str(run_dir)}
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        print(json.dumps(failure, indent=2))
        return_code = 1
    else:
        test_kind = ("owned_streaming_finish_control" if args.arm == "finish"
                     else "owned_streaming_cancellation_verification")
        summary = {**episode.summary(), "scenario": args.scenario, "arm": args.arm,
                   "test_kind": test_kind,
                   "run_directory": str(run_dir),
                   "session_id": session.identity.session_id,
                   "synchronization_basis": (
                       "Pinned-source terminal order, strict single-reader/log-offset implementation, "
                       "offline race tests, and EP-010 generation-cancellation calibration. The current "
                       "run must still establish its own complete acknowledgement and reuse chain."
                       if args.arm == "cancel-goal-replacement" else
                       "Cancellation synchronization is intentionally unaudited for the finish control.")}
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        failed = (summary["episode_outcome"] != "completed"
                  or summary["mission_outcome"] != "succeeded"
                  or any(summary["terminal_counts"].get(state, 0)
                         for state in ("worker_failed", "http_timed_out")))
        if args.arm == "cancel-goal-replacement":
            failed = (failed or summary["acknowledged_cancellations"] != 1
                      or summary["cancellation_receipt_counts"].get("recorded", 0) != 1)
        return_code = int(failed)
    finally:
        cleanup_error = None
        if worker is not None:
            try:
                worker.close()
            except Exception as exc:
                cleanup_error = exc
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            (run_dir / "cleanup-failure.json").write_text(
                json.dumps({"error": f"{type(cleanup_error).__name__}: {cleanup_error}"}, indent=2),
                encoding="utf-8")
            return_code = 1
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
