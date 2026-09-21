"""Compare two completed real-model pilot traces with the same scenario/configuration."""

import argparse
import json
from pathlib import Path
import statistics


def read_run(folder):
    folder = Path(folder)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return summary, metadata, events


def compare(first, second):
    left, right = read_run(first), read_run(second)
    for field in ("scenario", "model_sha256", "source_sha256", "packages", "python"):
        if left[1][field] != right[1][field]:
            raise ValueError(f"Cannot pair different {field}")
    for field in ("build_info", "total_slots", "default_generation_settings"):
        if left[1]["server_props"][field] != right[1]["server_props"][field]:
            raise ValueError(f"Cannot pair different server {field}")
    for field in ("tick", "deadline", "socket_timeout", "base_url"):
        if left[1]["configuration"][field] != right[1]["configuration"][field]:
            raise ValueError(f"Cannot pair different {field}")
    if {left[0]["policy"], right[0]["policy"]} != {"fifo", "newest-pending"}:
        raise ValueError("Provide one FIFO run and one newest-pending run")
    lines = [f"Scenario: {left[0]['scenario']}. Real-model HTTP episodes; one sample per policy.", "",
             "| Policy | Dispatched | Accepted | Stale | Coalesced | HTTP median (s) | Rejected worker wall (s) | Max tick gap (s) |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for summary, _, events in (left, right):
        counts = summary["terminal_counts"]
        elapsed = [e["http_elapsed_seconds"] for e in events if e["event"] == "response_received"]
        median = f"{statistics.median(elapsed):.3f}" if elapsed else "n/a"
        gap = summary["max_observation_gap_seconds"]
        gap_text = f"{gap:.3f}" if gap is not None else "n/a"
        lines.append(f"| {summary['policy']} | {summary['requests_dispatched']} | {counts.get('accepted', 0)} | "
                     f"{counts.get('rejected_stale_goal', 0)} | {counts.get('coalesced_before_inference', 0)} | "
                     f"{median} | {summary['rejected_worker_wall_seconds']:.3f} | {gap_text} |")
    lines += ["", "| Goal | FIFO status / acceptance latency (s) | Newest-pending status / acceptance latency (s) |",
              "| --- | --- | --- |"]
    by_policy = {run[0]["policy"]: run[0] for run in (left, right)}
    goals = {policy: {g["version"]: g for g in summary["goals"]} for policy, summary in by_policy.items()}
    for version in sorted(set(goals["fifo"]) | set(goals["newest-pending"])):
        values = []
        for policy in ("fifo", "newest-pending"):
            record = goals[policy].get(version)
            if record is None:
                values.append("not admitted")
            elif record["latency_seconds"] is None:
                values.append(record["status"] + " / n/a")
            else:
                values.append(f"{record['status']} / {record['latency_seconds']:.3f}")
        lines.append(f"| {version} | {values[0]} | {values[1]} |")
    for summary, _, events in (left, right):
        lateness = max((e["lateness_seconds"] for e in events if e["event"] == "scenario_event"), default=0)
        lines += ["", f"{summary['policy']}: {summary['episode_outcome']}; terminal counts "
                  f"`{json.dumps(summary['terminal_counts'], sort_keys=True)}`; max event lateness {lateness:.4f} s.",
                  f"Trace: `{summary['run_directory']}`."]
    lines += ["", "Latencies start when the coordinator observes the goal; scheduled-time latencies are also in summary.json. "
              "Superseded/censored goals have null latency, never zero. Wall-clock schedules match; actual delivery jitter is logged. "
              "HTTP durations include server queueing/processing and transport. Rejected worker wall time includes failed attempts "
              "and is only a discarded-work proxy, not GPU compute or energy. No inference cancellation or robot execution."]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    try:
        print(compare(args.first, args.second))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
