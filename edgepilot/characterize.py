"""Counterbalanced sequential live-model trials and trace-based metric regeneration."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys
import uuid

from .__main__ import ROOT, configure_paths, sha256
from .mission_compare import compare
from .mission_runtime import SCENARIOS, TRACE_SCHEMA

SCHEMA = "edgepilot.characterization.v1"
DEFAULT_SCENARIOS = ("nominal", "station-unavailable", "goal-replacement", "goal-burst", "irrelevant")
POLICIES = ("fifo", "newest-pending")


def schedule(scenarios, repetitions):
    if type(repetitions) is not int or repetitions < 2 or repetitions % 2:
        raise ValueError("Use a positive even number of pairs, at least two, for balanced order")
    if not scenarios or len(set(scenarios)) != len(scenarios) or any(s not in SCENARIOS for s in scenarios):
        raise ValueError("Provide distinct known scenarios")
    entries = []
    for repeat in range(repetitions):
        # Rotate scenario order as well; policy order is balanced within every scenario.
        offset = repeat % len(scenarios)
        for scenario in (*scenarios[offset:], *scenarios[:offset]):
            order = POLICIES if repeat % 2 == 0 else POLICIES[::-1]
            for position, policy in enumerate(order):
                entries.append({"index": len(entries), "pair": repeat, "scenario": scenario,
                                "policy": policy, "position": position, "status": "planned"})
    return entries


def save(path, value):
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    pending.replace(path)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def succeeded(summary):
    return summary["mission_outcome"] == "succeeded" and summary["episode_outcome"] == "completed"


def unsafe_to_continue(summary):
    return (not summary.get("finished") or summary.get("episode_outcome") == "transport_state_uncertain"
            or any(summary.get("terminal_counts", {}).get(s, 0) for s in ("worker_failed", "http_timed_out")))


def run_batch(folder, scenarios, repetitions, *, base_url="http://127.0.0.1:8080", model="edgepilot",
              launch=subprocess.run):
    manifest = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
                "test_kind": "real_model_simulation", "scenarios": list(scenarios), "repetitions": repetitions,
                "protocol": "Sequential pairs, alternating first policy and rotating scenario order; no retries or warmup exclusions.",
                "configuration": {"base_url": base_url, "model": model},
                "entries": schedule(scenarios, repetitions), "status": "running"}
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "manifest.json"
    save(path, manifest)  # Entire plan exists before the first inference.
    references = {}
    for entry in manifest["entries"]:
        command = [sys.executable, "-m", "edgepilot.mission", "--policy", entry["policy"],
                   "--scenario", entry["scenario"], "--base-url", base_url, "--model", model]
        entry.update(status="running", command=command, started_utc=datetime.now(timezone.utc).isoformat())
        save(path, manifest)
        try:
            process = launch(command, cwd=ROOT, capture_output=True, text=True)
            (folder/f"{entry['index']:03d}-stdout.txt").write_text(process.stdout, encoding="utf-8")
            (folder/f"{entry['index']:03d}-stderr.txt").write_text(process.stderr, encoding="utf-8")
            entry["exit_code"] = process.returncode
            summary = json.loads(process.stdout)
            run = Path(summary["run_directory"])
            entry["run_directory"] = str(run)
            entry["files_sha256"] = {name: sha256(run/name) for name in ("metadata.json", "summary.json", "events.jsonl")}
            if load(run/"summary.json") != summary:
                raise ValueError("Printed and persisted summary differ")
            if (summary["trace_schema"] != TRACE_SCHEMA or summary["test_kind"] != "real_model_simulation"
                    or summary["policy"] != entry["policy"] or summary["scenario"] != entry["scenario"]):
                raise ValueError("Episode identity does not match the scheduled trial")
            # Check every repetition against an opposite-policy reference, not only its partner.
            other = references.get((entry["scenario"], POLICIES[1-POLICIES.index(entry["policy"])]))
            if other:
                compare(other, run)
            references.setdefault((entry["scenario"], entry["policy"]), run)
            entry["status"] = "recorded"
            if unsafe_to_continue(summary) or (process.returncode and succeeded(summary)):
                raise RuntimeError("Worker/infrastructure failure; stop before dispatching another episode")
        except (Exception, KeyboardInterrupt) as exc:
            entry["status"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            manifest["status"] = "aborted"
        entry["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save(path, manifest)
        print(f"{entry['index']+1}/{len(manifest['entries'])}: {entry['scenario']} {entry['policy']} {entry['status']}", flush=True)
        if manifest["status"] == "aborted":
            break
    else:
        manifest["status"] = "completed"
        save(path, manifest)
    return manifest


def distribution(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "sample_sd": statistics.stdev(values) if len(values) > 1 else None}


def regenerate(path):
    manifest = load(path)
    if manifest["schema"] != SCHEMA or manifest["test_kind"] != "real_model_simulation":
        raise ValueError("Expected a live-model characterization manifest")
    runs, references = {}, {}
    for entry in manifest["entries"]:
        if "files_sha256" not in entry:
            continue
        run = Path(entry["run_directory"])
        for name, expected in entry["files_sha256"].items():
            if sha256(run/name) != expected:
                raise ValueError(f"Trace changed: {run/name}")
        summary = load(run/"summary.json")
        if summary["policy"] != entry["policy"] or summary["scenario"] != entry["scenario"]:
            raise ValueError("Manifest identity does not match trace")
        other = references.get((entry["scenario"], POLICIES[1-POLICIES.index(entry["policy"])]))
        if other:
            compare(other, run)
        references.setdefault((entry["scenario"], entry["policy"]), run)
        with (run/"events.jsonl").open(encoding="utf-8") as source:
            events = [json.loads(line) for line in source]
        summary["event_lateness_ms"] = [e["lateness_ns"]/1e6 for e in events if e["event"] == "world_event"]
        summary["observations_during_inference"] = sum(e["event"] == "observation" and e.get("worker_occupied", False) for e in events)
        runs[entry["index"]] = summary
    groups, pairs = [], []
    for scenario in manifest["scenarios"]:
        for policy in POLICIES:
            entries = [e for e in manifest["entries"] if e["scenario"] == scenario and e["policy"] == policy]
            summaries = [runs[e["index"]] for e in entries if e["index"] in runs]
            successes = [s for s in summaries if succeeded(s)]
            groups.append({"scenario": scenario, "policy": policy, "planned": len(entries),
                "attempted": sum(e["status"] != "planned" for e in entries), "recorded": len(summaries),
                "successful_missions": len(successes), "failed_attempts": sum(e["status"] == "failed" for e in entries),
                "episode_outcomes": dict(Counter(s["episode_outcome"] for s in summaries)),
                "goal_statuses": dict(Counter(g["status"] for s in summaries for g in s["goals"])),
                "terminal_counts": dict(sum((Counter(s["terminal_counts"]) for s in summaries), Counter())),
                "success_elapsed_s": distribution([s["elapsed_ns"]/1e9 for s in successes]),
                "final_goal_latency_s": distribution([s["goals"][-1]["latency_ns"]/1e9 if s["goals"][-1]["latency_ns"] is not None else None for s in summaries]),
                "final_goal_null_latency": sum(s["goals"][-1]["latency_ns"] is None for s in summaries),
                "http_elapsed_s": distribution([v/1e9 for s in summaries for v in s["http_elapsed_ns"]]),
                "rejected_http_s": distribution([s["rejected_worker_wall_ns"]/1e9 for s in summaries]),
                "max_observation_gap_ms": distribution([s["max_observation_gap_ns"]/1e6 for s in summaries if s["max_observation_gap_ns"] is not None]),
                "event_lateness_ms": distribution([v for s in summaries for v in s["event_lateness_ms"]]),
                "observations_during_inference": sum(s["observations_during_inference"] for s in summaries)})
        effects = []
        for repeat in range(manifest["repetitions"]):
            entries = [e for e in manifest["entries"] if e["scenario"] == scenario and e["pair"] == repeat]
            by_policy = {e["policy"]: runs[e["index"]] for e in entries if e["index"] in runs and e["status"] == "recorded"}
            if set(by_policy) == set(POLICIES) and all(succeeded(s) for s in by_policy.values()):
                fifo, newest = (by_policy[p] for p in POLICIES)
                effects.append({"pair": repeat, "first_policy": entries[0]["policy"],
                                "elapsed_delta_s": (newest["elapsed_ns"]-fifo["elapsed_ns"])/1e9})
        pairs.append({"scenario": scenario, "planned_pairs": manifest["repetitions"],
                      "excluded_pairs": manifest["repetitions"]-len(effects), "effects": effects,
                      "newest_minus_fifo_elapsed_s": distribution([e["elapsed_delta_s"] for e in effects]),
                      "by_first_policy": {p: distribution([e["elapsed_delta_s"] for e in effects if e["first_policy"] == p]) for p in POLICIES}})
    report = {"schema": SCHEMA, "manifest_status": manifest["status"], "groups": groups, "pairs": pairs,
              "telemetry": {"http_time": "measured wall proxy, not GPU compute/energy", "validation_cpu_wall": "per-episode traces; CPU granularity limits apply",
                            "ram_vram": None, "gpu_compute": None, "energy": None, "background_load": "uncontrolled, not measured", "rendering": "headless"},
              "limitations": "Development samples, resident warm server, no excluded warmups/retries. Paired elapsed effects require both missions successful; exclusions are counted. "
                             "Null goals are not zero latency. HTTP samples within a run are not independent repetitions. "
                             "Descriptive statistics only; no confidence intervals or generalization claim from this small dependent time series. No inference cancellation."}
    save(path.parent/"report.json", report)
    (path.parent/"report.md").write_text(render(report), encoding="utf-8")
    return report


def render(report):
    def fmt(value):
        return "n/a" if value is None else f"{value:.3f}"
    lines = ["# Repeated live-model mission characterization", "", f"Batch status: {report['manifest_status']}.", "",
             "Elapsed summaries include successful missions only. Counts retain failed/unattempted runs; latency nulls are separate.", "",
             "| Scenario | Policy | Success / attempted / planned | Elapsed median [min, max] s | Final-goal latency median s / nulls | HTTP mean / SD s | Max observation gap ms |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for g in report["groups"]:
        d, h = g["success_elapsed_s"], g["http_elapsed_s"]
        lines.append(f"| {g['scenario']} | {g['policy']} | {g['successful_missions']} / {g['attempted']} / {g['planned']} | "
                     f"{fmt(d['median'])} [{fmt(d['min'])}, {fmt(d['max'])}] | {fmt(g['final_goal_latency_s']['median'])} / {g['final_goal_null_latency']} | "
                     f"{fmt(h['mean'])} / {fmt(h['sample_sd'])} | {fmt(g['max_observation_gap_ms']['max'])} |")
    lines += ["", "Paired elapsed difference = newest-pending minus FIFO; negative favors newest-pending. Both missions must succeed.", "",
              "| Scenario | Included / excluded pairs | Mean delta [min, max] s | Mean when FIFO first / newest first s |",
              "| --- | --- | --- | --- |"]
    for p in report["pairs"]:
        d = p["newest_minus_fifo_elapsed_s"]
        lines.append(f"| {p['scenario']} | {d['n']} / {p['excluded_pairs']} | {fmt(d['mean'])} [{fmt(d['min'])}, {fmt(d['max'])}] | "
                     f"{fmt(p['by_first_policy']['fifo']['mean'])} / {fmt(p['by_first_policy']['newest-pending']['mean'])} |")
    lines += ["", "Goal outcomes, terminal counts, and event delivery (all recorded runs):", ""]
    for g in report["groups"]:
        lines.append(f"- {g['scenario']} / {g['policy']}: goals `{json.dumps(g['goal_statuses'], sort_keys=True)}`; "
                     f"terminals `{json.dumps(g['terminal_counts'], sort_keys=True)}`; max event lateness "
                     f"{fmt(g['event_lateness_ms']['max'])} ms; {g['observations_during_inference']} observations during inference.")
    lines += ["", report["limitations"], "", "Telemetry availability: " + json.dumps(report["telemetry"], sort_keys=True), ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=4, help="Even number of pairs per scenario")
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(DEFAULT_SCENARIOS))
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="edgepilot")
    parser.add_argument("--report", type=Path, help="Regenerate reports from an existing manifest; no inference")
    args = parser.parse_args()
    configure_paths()
    try:
        if args.report:
            path = args.report
        else:
            schedule(args.scenarios, args.repetitions)
            folder = ROOT/"runs"/("characterization-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
            print(f"Batch directory: {folder}", flush=True)
            run_batch(folder, args.scenarios, args.repetitions, base_url=args.base_url, model=args.model)
            path = folder/"manifest.json"
        report = regenerate(path)
        print(render(report))
        print(f"Manifest: {path}")
        return int(report["manifest_status"] != "completed" or any(g["successful_missions"] != g["planned"] for g in report["groups"]))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Characterization failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
