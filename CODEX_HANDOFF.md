# EdgePilot RT development handoff

Prepared for Pulkit Garg on 21 September 2026. Continue development in the existing Windows workspace D:\EdgePilot. This document records user-reported results and the next implementation task. Inspect the actual workspace before changing it; its files and newer user instructions take precedence over this snapshot.

## Goal and research boundary

Build a runtime that schedules local LLM robot planning as goals and observations change. Study when to continue, supersede, cancel, or refresh reasoning on constrained hardware. Aim for a reproducible engineering artifact and evaluate whether a distinct research contribution exists. Publication suitability and energy savings remain unproven.

The eventual application is one simulated mobile robot with bounded skills such as navigate_to, inspect, wait, and return_home. A conventional controller owns motion. Start with direct structured simulator state; camera perception is later work. Initial scope excludes model training, ROS2 integration, adaptive precision, and physical robot deployment.

Guardrails include action schemas, allowed skills, request and goal versions, current skill preconditions, and revalidation before dispatch. A valid JSON object is not necessarily a valid action. Keep independent validation even when generation uses a schema. Mandatory checks and fallback must be identical across scheduling baselines. Do not claim physical safety from simulation checks.

## Verified Windows environment

- Workspace: D:\EdgePilot on an internal HDD, about 405 GB free at setup.
- C: had about 17.5 GB free. Keep project environments, models, downloads, caches, logs, and temporary files on D:.
- Installed RAM: 15.4 GiB reported by the inventory command.
- GPU: NVIDIA GeForce GTX 1650 Ti, 4096 MiB VRAM. CUDA0 detected; about 3294 MiB free at the device check.
- Driver reported 616.92 and CUDA UMD 13.4. This does not establish that a CUDA compiler/toolkit is installed.
- Project interpreter: D:\EdgePilot\.venv\Scripts\python.exe, Python 3.11.4.
- pip: 26.2.1. psutil and pytest imports succeeded.
- Git: 2.40.0.windows.1. Git initialization and an initial commit were suggested but have NOT been confirmed. Inspect git status and existing repository metadata.
- requirements-bootstrap.txt was written using pip freeze.

Existing PowerShell session settings:

```powershell
$env:PIP_CACHE_DIR = "D:\EdgePilot\cache\pip"
$env:HF_HOME = "D:\EdgePilot\cache\huggingface"
$env:TEMP = "D:\EdgePilot\tmp"
$env:TMP = "D:\EdgePilot\tmp"
```

These settings are process-scoped, not guaranteed in a new terminal. Set them in project sessions when installing or running tools. Use the existing virtual environment directly; activation is optional. Confirm which shell your Codex session uses. Do not confuse a WSL Python interpreter with the verified Windows environment or silently migrate the project to Linux.

## Inference runtime and model

- Runtime directory: D:\EdgePilot\tools\llama-b11065. Locate executables recursively if needed.
- Build: b11065-ce8caa6e6, Windows x64 CUDA 12.4 binary with matching CUDA DLL archive.
- Model: D:\EdgePilot\models\Qwen3-0.6B-Q8_0.gguf, official Qwen GGUF.
- Model hash recorded in runs\model-sha256.txt; inspect it rather than inventing a digest.
- All 29/29 layers were confirmed offloaded in the benchmark logs.
- Reported buffers in the larger test: model 604.15 MiB, KV 28 MiB, compute 74.69 MiB. Their sum is not total process VRAM usage.

A llama-server process was started manually in a separate PowerShell window. It may still be running. Check /health before starting another process. Its effective arguments were:

```text
-m D:\EdgePilot\models\Qwen3-0.6B-Q8_0.gguf
--alias edgepilot -ngl 99 -c 2048 -np 1 -t 4 -b 256 -ub 128
-fa off --jinja --host 127.0.0.1 --port 8080
```

Base URL: http://127.0.0.1:8080. GET /health should report status ok. POST /v1/chat/completions is verified. The model stays loaded across requests. The first Python smoke request produced 29 completion tokens from 37 prompt tokens in 0.564 seconds including HTTP request handling, excluding model startup.

## Initial performance evidence

Source directory: runs\baseline-20260921-015224. Results are preliminary microbenchmarks, not paper results.

| Test | CPU mean and SD in tokens per second | GPU mean and SD in tokens per second |
| --- | --- | --- |
| pp256 | 202.89 ± 10.27 | 1175.92 ± 58.91 |
| tg64 | 47.94 ± 2.04 | 121.09 ± 21.48 |

Common flags: -p 256 -n 64 -r 3 -t 4 -b 256 -ub 128 -fa off. CPU used -ngl 0 -nkvo 1 -nopo 1; GPU used -dev CUDA0 -ngl 99. The compiled backend can say CUDA even with CPU offloading disabled. Measured speedups are about 5.80x for prompt processing and 2.53x for generation. These tests exclude tokenization and sampling; GPU generation varied considerably across three repetitions. A prior CLI prompt-speed reading of 1.0 t/s was inconsistent with this benchmark; its cause is not established.

## Existing runtime probe

Inspect runtime_probe_v1\probe.py, test_probe.py, and README.md. The current internal PROBE_VERSION is 1.1 despite the directory name. It uses only the standard library.

Behavior:
- HTTP request in a background thread; synthetic observation ticks in the main loop every 50 ms.
- One outstanding request, identified by request_id and goal_version.
- Monotonic elapsed timing, unique runs/probe-* directories, request/response JSON, events.jsonl, and summary.json.
- JSON schema constrains the reply to exactly action="wait" and a bounded textual reason. Independent validation rejects malformed, unsupported, extra-field, or truncated output.
- Goal supersession rejects the completed result. It does NOT cancel inference, and no robot action is executed.
- HTTP timeout is a socket timeout, not a proven hard overall deadline.

A previous generic JSON-only request generated the wrong fields. The validator correctly rejected it. Revision 1.1 added an explicit response_format schema and separates rejected_invalid_plan from infrastructure failures. Do not fix future validation failures by weakening the validator.

Four local tests passed against a delayed HTTP stub; they cover observation progress, stale rejection, invalid output handling, and preservation of raw responses. User verified revision 1.1 against the real model:

| Run | Outcome | Request to decision | Ticks | Maximum tick gap |
| --- | --- | --- | --- | --- |
| probe-20260921-020616-b712b3de | accepted; wait, reason idle | 0.493970 s | 10 | 0.0504975 s |
| probe-20260921-020626-0d141170 | rejected_stale_goal; no accepted plan | 0.593593 s | 12 | 0.0505474 s |

The stale run used --change-goal-after 0, replacing the goal immediately after submission and before result acceptance. It is not evidence of mid-generation cancellation, hard real-time behavior, or useful robot planning.

## Next task to implement

First inspect the code, run files, environment, and git status. Preserve working models, binaries, logs, and the validated probe. Reuse existing components and introduce a small maintainable package rather than repeatedly replacing the probe script.

Implement the first event-driven scheduling pilot, with no GPU cancellation yet:

1. Define typed observations, goals, requests, results, and terminal states with monotonic timestamps and explicit versions.
2. Keep the observation/event loop independent of inference. Use one resident model and one active request. In the initial HTTP adapter, consider the worker occupied until its request actually returns or fails.
3. Implement two policies: finish every queued request in FIFO order, and finish the active request while coalescing pending updates into only the newest pending request. Use finite scenario scripts so FIFO backlog is bounded by the event count; document that distinction from the bounded pending slot.
4. Both policies must use the same validator: reject superseded goal versions before accepting results. Pending coalescing discards requests before inference; do not label it cancellation. With no executable plan, use the same wait fallback.
5. Add deterministic event scenarios with isolated goal changes, bursts of goal changes, and irrelevant observation updates. Use identical wall-clock event schedules for paired policies. The schedule must keep advancing during inference. Include intervals both shorter and longer than observed service time; do not manufacture only one favorable workload.
6. After old work finishes, dispatch the next applicable request until the event schedule and queued work finish, or a documented episode deadline occurs. Record timeouts and failures explicitly.
7. Log accepted/rejected results, queue/coalescing decisions, request HTTP elapsed time, observation gaps, and latency to an accepted response for each goal that remains current. Superseded goals without an accepted result are censored/superseded, not zero-latency successes. Report worker wall time spent on rejected responses as a proxy for discarded work, not measured GPU compute or energy.
8. Add meaningful offline tests with a controllable fake worker: queue order, pending coalescing, goal-version rejection, loop progress, worker failures, and clean termination. Avoid wall-clock-sensitive timing assertions when a mock clock can verify ordering.
9. Run a short local-server integration episode if the server is ready. Preserve raw traces and distinguish stub tests from real model runs. Record build, model hash, configuration, and package versions.
10. Provide one Windows command per policy plus a small comparison report. Keep later pilot data separate from existing baseline logs. Document scope and how to reproduce the results.

Keep the wait-only workload labelled as a scheduler plumbing experiment. It cannot establish robot task success or justify a paper. After the pilot is correct, introduce bounded meaningful skills, task preconditions, and a small simulated scene before making task-quality claims.

## Later work and boundaries

The candidate research contribution combines task relevance, estimated remaining inference time, and measured restart/cancellation cost. Compare against tuned periodic replanning, newest-pending handling, task-aware cancellation, deadlines, and a symbolic planner where applicable. Do not claim novelty for generic event triggers or adding guardrails.

Actual cancellation needs a separate measured protocol: supersede immediately, request a stop, and wait for verified terminal acknowledgement before worker reuse. Closing an HTTP connection or cancelling a Python Future does not itself prove GPU work stopped. Validate any server cancellation behavior against the pinned build; use a native adapter if necessary. Report actual cancellation delay and wasted work honestly.

Keep development on Windows and D:. Keep model binaries, environments, caches, logs, and archives out of Git. Do not download replacement models or install large toolchains before a concrete need is demonstrated. Inspect existing changes before editing and do not discard user work. Build and test the next task; do not stop at a proposed plan. Report what changed, what was tested, and what remains unverified.

References already consulted:
- https://github.com/ggml-org/llama.cpp/releases/tag/b11065
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md
- https://huggingface.co/Qwen/Qwen3-0.6B-GGUF
- https://arxiv.org/abs/2503.06892 (SafePlan)
- https://arxiv.org/abs/2510.12985 (SENTINEL)

Current master documentation may differ from b11065. Check installed --help and the pinned source when behavior matters.
