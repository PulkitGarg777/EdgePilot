"""Compare paired live-model mission traces; do not mix them with scripted demos."""

import argparse
import json
from pathlib import Path

from .mission_runtime import TRACE_SCHEMA


def compare(first: Path, second: Path) -> str:
    runs = []
    for folder in (first, second):
        summary = json.loads((folder/"summary.json").read_text(encoding="utf-8"))
        meta = json.loads((folder/"metadata.json").read_text(encoding="utf-8"))
        if summary["trace_schema"] != TRACE_SCHEMA or summary["test_kind"] != "real_model_simulation":
            raise ValueError("Provide real-model mission traces")
        runs.append((summary, meta))
    left, right = runs
    for key in ("scenario", "model_sha256", "source_sha256", "packages", "python", "prompt_version", "action_schema", "generation"):
        if left[1][key] != right[1][key]:
            raise ValueError(f"Cannot compare different {key}")
    for key in ("build_info", "total_slots", "default_generation_settings"):
        if left[1]["server_props"][key] != right[1]["server_props"][key]:
            raise ValueError(f"Cannot compare different server {key}")
    configs = [{k:v for k,v in m["configuration"].items() if k != "policy"} for _,m in runs]
    if configs[0] != configs[1]:
        raise ValueError("Episode configurations differ")
    if {s["policy"] for s,_ in runs} != {"fifo", "newest-pending"}:
        raise ValueError("Provide one run per policy")
    lines = [f"Scenario: {left[0]['scenario']}. One real-model sample per policy; no statistical claim.", "",
             "| Policy | Mission | Episode | Dispatched | Accepted | Stale | Coalesced | Elapsed (s) | Rejected HTTP wall (s) |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for summary,_ in runs:
        counts = summary["terminal_counts"]
        lines.append(f"| {summary['policy']} | {summary['mission_outcome']} | {summary['episode_outcome']} | "
                     f"{summary['requests_dispatched']} | {counts.get('accepted',0)} | "
                     f"{counts.get('rejected_stale_goal',0)} | {counts.get('coalesced_before_inference',0)} | "
                     f"{summary['elapsed_ns']/1e9:.3f} | {summary['rejected_worker_wall_ns']/1e9:.3f} |")
    lines += ["", "| Policy | Goal | Plan outcome | First accepted-plan latency (s) | Mission outcome |",
              "| --- | ---: | --- | ---: | --- |"]
    for summary,_ in runs:
        outcomes = {r["goal_version"]:r["outcome"] for r in summary["mission_outcomes"]}
        for goal in summary["goals"]:
            latency = "n/a" if goal["latency_ns"] is None else f"{goal['latency_ns']/1e9:.3f}"
            lines.append(f"| {summary['policy']} | {goal['goal_version']} | {goal['status']} | "
                         f"{latency} | {outcomes.get(goal['goal_version'], 'unfinished')} |")
    for summary,_ in runs:
        lines += ["", f"{summary['policy']} terminal counts: `{json.dumps(summary['terminal_counts'],sort_keys=True)}`.",
                  f"Trace: `{summary['run_directory']}`."]
    lines += ["", "Accepted plans can be partial/wait plans; only actual inspections plus return home count as mission success. "
              "Superseded/censored goals have null latency. Actual trajectories and sampled prompt state can diverge despite "
              "identical external scripts. HTTP wall time is not GPU compute or energy. No inference cancellation."]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    try:
        print(compare(args.first, args.second))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, str(exc)+"\n")


if __name__ == "__main__":
    main()
