"""Offline batch orchestration/report tests. The launcher never calls a model."""

from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from edgepilot.characterize import distribution, load, regenerate, run_batch, save, schedule
from edgepilot.mission_runtime import TRACE_SCHEMA


@pytest.mark.parametrize("repetitions", [0, 1, 3, -2, True])
def test_requires_even_positive_pair_count(repetitions):
    with pytest.raises(ValueError):
        schedule(["nominal"], repetitions)


def test_balances_order_per_scenario_and_rotates_scenarios():
    entries = schedule(["nominal", "goal-burst"], 4)
    assert len(entries) == 16
    for scenario in ("nominal", "goal-burst"):
        assert Counter(e["policy"] for e in entries if e["scenario"] == scenario and e["position"] == 0) == {
            "fifo": 2, "newest-pending": 2}
    assert entries[0]["scenario"] == "nominal" and entries[4]["scenario"] == "goal-burst"
    with pytest.raises(ValueError):
        schedule(["nominal", "nominal"], 2)


def launcher(root, failure=None):
    calls = []
    def invoke(command, **kwargs):
        calls.append(command)
        policy = command[command.index("--policy")+1]
        scenario = command[command.index("--scenario")+1]
        run = root / f"run-{len(calls)}"
        run.mkdir()
        failed = failure and len(calls) == 1
        duration = 8 if policy == "fifo" else 6
        summary = {"trace_schema": TRACE_SCHEMA, "test_kind": "real_model_simulation", "run_directory": str(run),
            "policy": policy, "scenario": scenario, "finished": True,
            "mission_outcome": "timed_out" if failed else "succeeded",
            "episode_outcome": failure if failed else "completed", "elapsed_ns": duration*10**9,
            "requests_dispatched": 1, "terminal_counts": {"http_timed_out": 1} if failed and failure == "transport_state_uncertain" else {"accepted": 1},
            "rejected_worker_wall_ns": 0, "http_elapsed_ns": [10**9], "max_observation_gap_ns": 50_000_000,
            "goals": [{"goal_version": 1, "status": "superseded", "latency_ns": None},
                      {"goal_version": 2, "status": "censored_episode_deadline" if failed else "accepted", "latency_ns": None if failed else 10**9}],
            "mission_outcomes": [{"goal_version": 1, "outcome": "superseded"},
                                 {"goal_version": 2, "outcome": "timed_out" if failed else "succeeded"}]}
        meta = {k: "fixed" for k in ("model_sha256", "source_sha256", "packages", "python", "prompt_version", "action_schema", "generation")}
        meta.update(scenario=scenario, configuration={"policy": policy, "scenario": scenario},
                    server_props={"build_info": "pinned", "total_slots": 1, "default_generation_settings": {}})
        save(run/"metadata.json", meta)
        save(run/"summary.json", summary)
        events = [{"event": "world_event", "lateness_ns": 2_000_000},
                  {"event": "observation", "worker_occupied": True}]
        (run/"events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        return SimpleNamespace(stdout=json.dumps(summary), stderr="", returncode=int(bool(failed)))
    return invoke, calls


def test_runs_sequential_plan_and_regenerates_paired_metrics(tmp_path):
    launch, calls = launcher(tmp_path)
    folder = tmp_path/"batch"
    manifest = run_batch(folder, ["nominal"], 4, launch=launch)
    assert len(calls) == 8 and manifest["status"] == "completed"
    report = regenerate(folder/"manifest.json")
    assert report["pairs"][0]["newest_minus_fifo_elapsed_s"]["mean"] == -2
    assert report["pairs"][0]["by_first_policy"]["newest-pending"]["n"] == 2
    group = report["groups"][0]
    assert group["successful_missions"] == group["attempted"] == group["planned"] == 4
    assert group["goal_statuses"] == {"superseded": 4, "accepted": 4}
    assert group["event_lateness_ms"]["max"] == 2 and group["observations_during_inference"] == 4
    assert regenerate(folder/"manifest.json") == report  # Deterministic regeneration, no inference.


def test_retains_mission_failure_null_latency_and_excluded_pair(tmp_path):
    launch, calls = launcher(tmp_path, "episode_deadline")
    folder = tmp_path/"batch"
    run_batch(folder, ["nominal"], 2, launch=launch)
    report = regenerate(folder/"manifest.json")
    assert len(calls) == 4
    fifo = report["groups"][0]
    assert fifo["successful_missions"] == 1 and fifo["attempted"] == 2
    assert fifo["final_goal_null_latency"] == 1 and fifo["final_goal_latency_s"]["n"] == 1
    assert report["pairs"][0]["excluded_pairs"] == 1


def test_transport_failure_stops_batch_and_keeps_unattempted_denominator(tmp_path):
    launch, calls = launcher(tmp_path, "transport_state_uncertain")
    folder = tmp_path/"batch"
    manifest = run_batch(folder, ["nominal"], 2, launch=launch)
    report = regenerate(folder/"manifest.json")
    assert len(calls) == 1 and manifest["status"] == "aborted"
    assert [g["attempted"] for g in report["groups"]] == [1, 0]
    assert report["groups"][0]["failed_attempts"] == 1
    assert report["pairs"][0]["excluded_pairs"] == 2


def test_startup_failure_preserves_stderr_and_manifest(tmp_path):
    def failed(*args, **kwargs):
        return SimpleNamespace(stdout="", stderr="Server unavailable", returncode=1)
    folder = tmp_path/"batch"
    manifest = run_batch(folder, ["nominal"], 2, launch=failed)
    assert manifest["status"] == "aborted"
    assert (folder/"000-stderr.txt").read_text() == "Server unavailable"
    report = regenerate(folder/"manifest.json")
    assert report["groups"][0]["recorded"] == 0 and report["groups"][0]["attempted"] == 1


def test_report_refuses_changed_raw_trace(tmp_path):
    launch, _ = launcher(tmp_path)
    folder = tmp_path/"batch"
    manifest = run_batch(folder, ["nominal"], 2, launch=launch)
    run = Path(manifest["entries"][0]["run_directory"])
    (run/"events.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Trace changed"):
        regenerate(folder/"manifest.json")


def test_configuration_drift_aborts_instead_of_pooling_results(tmp_path):
    launch, calls = launcher(tmp_path)
    def changed(*args, **kwargs):
        result = launch(*args, **kwargs)
        if len(calls) == 2:
            path = tmp_path/"run-2"/"metadata.json"
            meta = load(path)
            meta["model_sha256"] = "changed"
            save(path, meta)
        return result
    folder = tmp_path/"batch"
    manifest = run_batch(folder, ["nominal"], 2, launch=changed)
    assert len(calls) == 2 and manifest["status"] == "aborted"
    with pytest.raises(ValueError, match="model_sha256"):
        regenerate(folder/"manifest.json")


def test_empty_and_single_sample_statistics_are_not_zero_or_fake_sd():
    assert distribution([None])["mean"] is None
    assert distribution([2])["sample_sd"] is None
