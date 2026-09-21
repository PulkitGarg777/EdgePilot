# EdgePilot resident model runtime probe

Revision 1.1 adds an explicit JSON schema to constrain generation to the wait action and required reason, while retaining independent validation. The directory name remains runtime_probe_v1 for compatibility. Summaries now distinguish rejected_invalid_plan from infrastructure failures and include the raw response file path. Schema constraints do not replace state validation.

Reference: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

This integration scaffold checks that Python observation ticks continue while a resident llama.cpp server handles a request, and that a superseded goal cannot receive an accepted plan. Python 3.11 or later; standard library only.

## Windows usage

Extract the archive into D:\EdgePilot. It creates runtime_probe_v1 without replacing your environment, models, or baseline files. Keep the existing llama-server running on 127.0.0.1:8080 with alias edgepilot.

Run from D:\EdgePilot:

```powershell
.\.venv\Scripts\python.exe .\runtime_probe_v1\probe.py
.\.venv\Scripts\python.exe .\runtime_probe_v1\probe.py --change-goal-after 0
```

The ordinary run should report accepted for a correctly formatted wait response. The second replaces the goal immediately after request submission, before the coordinator accepts any result, and should report rejected_stale_goal. Use --change-goal-after 0.1 later to inject a delayed change; if a response is accepted before that time, the goal-change event will not happen.

Every run creates a unique runs/probe-* directory with request.json, events.jsonl, response.json when available, and summary.json. elapsed_seconds uses a monotonic clock. All paths default to the current directory; run from D:\EdgePilot to keep results on D:.

## Meaning of results

The HTTP request runs in a background thread. The main thread records synthetic observation ticks with a target interval of 50 ms; missed ticks are not backfilled. Inspect their actual gaps. This is an integration check, not a real-time guarantee or a simulated robot benchmark.

request_to_decision_seconds includes dispatch, HTTP generation, polling, validation and logging. It excludes model startup and the readiness check. It is not time to first token. The response is non-streaming; usage and any server timing fields are retained in response.json. Prompt reuse is disabled in the request, but these short runs are correctness checks rather than a controlled performance study.

Only the wait action with a bounded textual reason is accepted; no action is executed. The guard rejects malformed, unsupported and truncated output. Current-state skill preconditions, streaming, physical simulation, cancellation, restart policies and concurrency-safe executor dispatch are future work. In particular, rejecting an old response does not stop the underlying GPU computation. The worker waits for the response or a socket timeout; this probe must not be used to claim measured cancellation savings.

## Offline tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\runtime_probe_v1 -p test_probe.py -v
```

Tests use a delayed local HTTP stub to check observation progress during inference, stale-goal rejection, and rejection of malformed, unsupported or truncated plans. They do not test CUDA or your installed llama.cpp build.
