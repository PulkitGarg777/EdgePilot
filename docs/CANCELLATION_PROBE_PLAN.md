# EP-010 cancellation feasibility protocol

Designed 30 September 2026; completed 1 October 2026. Status: source audit, isolated probe, calibration, repeated real-model generation trials, and deterministic reporting complete. EP-010 is complete with a streaming-generation-only feasibility result. This protocol supplies evidence for EP-011; production cancellation belongs to EP-016 only if the research decision permits it.

## Result

Final batch: `runs/cancellation-trials-20260930-125525-12c1ac56` (local ignored evidence), with its [manifest](../runs/cancellation-trials-20260930-125525-12c1ac56/manifest.json), [JSON report](../runs/cancellation-trials-20260930-125525-12c1ac56/report.json), and [readable report](../runs/cancellation-trials-20260930-125525-12c1ac56/report.md). All 32 target trials and all 16 counterbalanced finish/disconnect pairs were recorded, with no failures, censoring, or exclusions. Every trial was followed by correct natural completion of a distinct short reuse task. Report regeneration was byte/hash stable; 32 focused offline tests passed.

Streaming disconnect during confirmed generation produced a matching task-scoped cancel event, release on the audited synchronized generation path, an idle slot observation, and verified reuse in 8/8 trials. Median observed stop-to-release was 46 ms at the 64-token cutpoint and 35 ms at 256 tokens. Median paired avoided remaining time was 3.701 s and 2.168 s respectively.

Non-streaming disconnect produced a cancel event in 0/8 trials. Every request continued naturally to the 512-token limit before release and reuse. Median paired avoided remaining time was -0.024 s at 64 tokens and -0.025 s at 256 tokens, so this path supplies no useful cancellation mechanism in the tested build.

These are four dependent development pairs per mode/cutpoint on one build, model, GPU, and process. Client observation times include polling, transport, log-flush, and observer delay. The result does not establish prompt-phase compute completion, arbitrary GPU kernel preemption, energy savings, tail guarantees, or cross-configuration support. The trial manifest did not snapshot the exact runner source hash; same-process calibration pins the executable, implementation DLL, model, and server PID, and the final report records the source-provenance limitation.

## Question and boundary

Can the pinned resident server stop an admitted request before its normal completion, establish a defensible computation-complete boundary, and serve another request correctly? What delay is observable between the client's stop intention, server-side release, and successful reuse?

Use the existing Windows environment, Qwen3-0.6B Q8_0, and one GPU-offloaded slot. Investigate the existing HTTP interface first. Keep the mission scheduler and HttpWorker unchanged while feasibility is measured. These synthetic requests measure runtime behavior, not mission quality, physical safety, GPU-time savings, or energy savings.

## Verified source findings

The installed executable reports `0.4.1-dev`, build `11065`, commit `ce8caa6e6`, Clang 20.1.8, Windows x86_64. GitHub resolves the commit to `ce8caa6e60a03093351d6016a818720e0d46f0fb`. Prefer the full commit below: cached web renderings of the build-tag URLs had different line layouts, so function names and freshly fetched commit-pinned source are the audit anchors.

| Evidence | Finding | Consequence |
| --- | --- | --- |
| [server-http.cpp](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server-http.cpp), request construction and `process_handler_response` | Connection-closed checks reach request handlers. Streaming completion cleanup releases the response object. | A socket disconnect is a candidate cancellation trigger; the client's close call is not terminal evidence. |
| [server-queue.h](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server-queue.h), `server_response_reader` destructor; [server-queue.cpp](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server-queue.cpp), `next`, `stop`, `yield_to_queue` | Cleanup posts internal cancellation tasks at high priority and removes pending work. Non-stream response waiting checks disconnection through its polling path. The cancellation log precedes processing of that task. | Record cancellation intent and subsequent release separately. Streaming and non-streaming paths require separate trials. |
| [server-context.cpp](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server-context.cpp), `process_single_task`, `release`, `decode`, `metrics_flush_idle`, `get_slots` | Cancellation releases its matching task; mutating tasks are declined while decode is yielding. Output-producing decode synchronizes. Intermediate prefill may defer synchronization until later. Slot snapshots can be serviced during decode. | Generation-phase release has a stronger synchronization argument. An idle snapshot or release log alone cannot certify prompt-phase GPU completion. |
| [server.cpp](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server.cpp), route registrations; `server-context.cpp`, control handling | Inspected routes provide completions, monitoring, and a reasoning-end control. The latter does not provide general cancellation acknowledgement. | Do not invent a `/cancel` endpoint or treat reasoning-end control as request termination. |
| [llama.h](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/include/llama.h), abort callback and `llama_synchronize`; [llama-context.cpp](https://github.com/ggml-org/llama.cpp/blob/ce8caa6e60a03093351d6016a818720e0d46f0fb/src/llama-context.cpp), `synchronize` | Abort callback support is documented as CPU-only; synchronization waits for backend work. | Native APIs are not evidence of immediate GPU kernel interruption. A native fallback would need its own measured boundary. |

These findings support testing cooperative cancellation between compute steps. They do not establish a successful stop, a latency bound, or a usable production acknowledgement API on this machine.

## Isolated implementation

Proposed files: `edgepilot/cancellation_probe.py`, `tests/test_cancellation_probe.py`; a results document only after measurements. Reuse small inventory/hash helpers if appropriate; do not refactor unrelated modules. Use the standard library and existing dependencies.

The probe coordinates a transport with an explicitly owned socket, a single sequential slot monitor, and a server-log reader. Give transport reads a separate thread or subprocess so stop intention can call `shutdown(SHUT_RDWR)` and close the actual socket. Merely abandoning a Future or response wrapper is insufficient. Test ownership/termination with a local HTTP stub. Preserve any transport error induced by the intended close as evidence, not as an unexpected failure.

One coordinator owns the trial state and all reuse admission. The log reader timestamps receipt of each line and retains raw text. The slot monitor records send/receive times and raw JSON. All observations enter the coordinator without making their own state transitions.

Use a dedicated, exclusively owned server process with one slot and a fresh run directory. Reuse the documented launch flags and enable `--slots`, `--log-timestamps`, and a verified verbosity sufficient for task/phase/release evidence (start at `-lv 3`). Avoid an additional resident instance if another llama-server is running. Do not terminate an existing user-owned server. The runner should refuse ambiguous ownership and supply manual launch/stop instructions if needed. User-run commands should be available alongside automated execution.

Record binary and `llama-server-impl.dll` hashes, model hash, server PID/start identity, exact arguments, `--version`, `/props`, Python/packages, probe source hashes, and schema `edgepilot.cancellation_probe.v1`. Disable speculative decoding and retain the established context/batch settings. Preserve pinned-source URLs and hashes of any downloaded audit sources. No new model, native build, or installation is required initially.

## Workloads and calibration

Use `/v1/chat/completions` so the non-streaming case exercises the existing application's route. Freeze one short synthetic prompt asking for a long numbered sequence, temperature/seed zero, thinking disabled, prompt caching disabled, and a finite 512-token output budget. Test the server's `ignore_eos` extension and confirm its effective setting before relying on it to prevent early EOS. If it is not accepted or the response still ends early, retain that outcome and revise the calibration protocol explicitly; do not silently substitute a different route.

Keep rendered prompt plus output comfortably within the existing 2048-token context. Store the exact payload and prompt hash. Streaming differs only in transport mode and required stream options. An SSE chunk is not a token; generation thresholds must use verified server token counters or another validated token-count signal.

Calibration, saved separately from trials:

1. Verify health, build/model identity, one idle slot, and uncontested server ownership. Inspect the actual slot/log schemas, map a client trial to the server task ID, and test parser assumptions.
2. Run one full request in each transport mode. Confirm the finite budget, natural terminal evidence, observed generation counters, and successful short reuse. If the budget is too short to observe both chosen cutpoints, revise and freeze before the manifest is created.
3. Compare one extra full control with slot polling disabled against an otherwise identical monitored control. This is a descriptive check for gross observation overhead, not a statistical correction. Use one outstanding `/slots` query at a time, initially targeting 20 ms; record actual gaps and round-trip times. Any poll-rate or logging change creates a new protocol/configuration hash.

No uncontrolled retries, unbounded generation, or server restarts inside a trial batch. A restart establishes a new process identity and batch.

## Repeated generation-phase trials

Create the complete manifest before dispatch. For each of two modes (`stream=false`, `stream=true`) and two target cutpoints (64 and 256 decoded tokens), run four matched finish/disconnect pairs. Alternate which arm runs first; rotate condition order. This yields 16 pairs / 32 target requests, each followed by one short reuse request only after the required boundary. Calibration is excluded and separately labelled.

Both arms wait until the mapped task is confirmed generating at or beyond the cutpoint, with adequate budget remaining. Record actual observed progress; polling can overshoot. If the target has completed, skip the stop action and retain `completed_before_stop`. If the phase/counter cannot be established, record `phase_unconfirmed`. Neither is cancellation success, and neither is silently rerun.

In the finish arm, record a pseudo-stop timestamp at the cutpoint and let the response finish. In the disconnect arm, record stop intention, perform socket shutdown/close, and wait for evidence. Keep late data/errors and the superseded trial ID. Never infer completion from a client-thread exit.

Generation-phase terminal evidence requires all of:

- The original server task was active and generating before stop intention.
- A cancellation event for that task precedes its release in server event order; the release corresponds to curtailed generation, with natural limit/EOS/error completion ruled out using the validated counters and logs. A cleanup cancellation log by itself is insufficient.
- The audited generation synchronization path applies to the recorded configuration; the parser recognizes the corresponding release. If ordering or phase is ambiguous, classify it as inconclusive.
- A subsequent fresh slot response shows idle under the same server process, with no conflicting task.

Only then submit the short reuse request. Record its distinct server task ID, response correctness/format, natural terminal event, and final idle slot. In the finish arm, require the normal terminal response, corresponding release, and idle observation before reuse.

Use separate evidence labels: `server_task_released`, `generation_sync_boundary_established`, and `reuse_verified`. A successful empirical protocol is a candidate for EP-016, not an already-integrated acknowledgement contract. No result claims arbitrary mid-kernel preemption.

## Prompt-phase diagnostic

After generation trials, optionally run up to four fixed attempts (two per transport mode) using a longer, token-counted prompt that still fits the same context with a finite output budget. Freeze that diagnostic manifest separately. Trigger only on positive server evidence of prompt processing; lack of a first token is not sufficient phase evidence.

Record whether the task stops before generation and how it releases. The current audit does not justify interpreting prompt-phase idle/release as a GPU-complete acknowledgement. Report `logical_release_only` unless a separately audited observable synchronization barrier can be established. Do not send an ordinary reuse request while computation state remains uncertain. End the diagnostic batch and retire its owned process; cleanup is not cancellation success. A later diagnostic needs a new process identity.

If prompt processing is too brief to hit, report `phase_not_observed`. Do not increase context, move to CPU, or install a native adapter simply to obtain a positive result. A follow-up may be designed if this coverage matters to EP-011.

## Timing and retained evidence

Use client `perf_counter_ns` relative to one run origin. Preserve server log timestamps in their own domain. Never subtract unaligned client/server clocks. Log receipt includes buffering and reader delay.

Record dispatch, last active-slot query interval, actual generation cutpoint, stop/pseudo-stop intention, socket shutdown completion, cancellation-log receipt, release-log receipt, each idle query interval, reuse dispatch, first response data, terminal response, and final idle observation. The release message occurs before all release bookkeeping; measure each observable separately.

Primary metrics are stop-intention to observed terminal evidence, stop-intention to verified idle, and reuse request latency. These include detection/transport/observer overhead. An idle response received after a valid boundary supplies an observation-time upper bound, not an exact GPU stop timestamp. An earlier active snapshot is only a coarse lower-bound observation and may describe lagging logical state. Report uncertainty rather than fabricating a kernel-stop interval.

Within finish controls, measure pseudo-stop to natural terminal response. Paired contrasts against disconnect arms are descriptive counterfactual estimates across different runs; they are not the cancelled request's known residual time. Report per-condition counts, actual cutpoints, median/range, finish/cancel arm order, censored and raced trials, and all failure reasons. Four pairs do not establish tail guarantees or justify a precise confidence interval.

Artifacts: `manifest.json`, `metadata.json`, raw server stdout/stderr, client event JSONL, raw slot observations, request payloads, full/partial response bytes, `summary.json`, and regenerable `report.json`/`report.md`. Hash evidence after files close; retain unmatched/duplicate events for audit. A non-streaming cancelled request can legitimately have no response body. Missing counters or backend timing remain null.

## Failure handling and offline tests

Calibration chooses and records a generous finite per-trial deadline (initial ceiling 30 seconds) plus a bounded cleanup timeout. Expiry censors the measurement and aborts the batch. A hard watchdog may stop only the server and transport processes owned by the probe; that outcome is infrastructure recovery, never verified cancellation. Leave unattempted manifest entries visible. No next request is dispatched on uncertain state, server exit, identity drift, or corrupted evidence.

Targeted tests must establish:

- Disconnect request, client completion, cancellation-posted log, and idle snapshot cannot individually authorize reuse.
- Mapped generation cancellation/release and subsequent idle permit reuse; wrong IDs, duplicate/late logs, PID changes, missing phase, and prompt-only release do not.
- Natural completion racing a stop remains a distinct result; a late old response cannot become the next trial's result.
- The raw socket actually disconnects under a local stub; streaming fragment parsing handles split UTF-8/SSE records and ignores role-only frames for progress.
- Watchdogs bound an unresponsive reader; batch abortion preserves evidence and unattempted denominators.
- Report regeneration verifies hashes and configurations without inference; timings use one clock domain.

## Decision outputs and handoff

Report feasibility separately for streaming generation, non-streaming generation, and prompt processing. Valid conclusions include supported cooperative generation cancellation in the tested configuration, a technically observable but too-slow stop, logical release without sufficient compute acknowledgement, or an inconclusive/unsupported interface. A failed observation protocol does not prove the backend can never cancel.

EP-010 closed after recording both a supported streaming-generation result and an unsupported non-streaming result. EP-011 must retain this phase/transport restriction: generation-only support cannot become an all-phase cancellation claim. An instrumented native/server barrier is a separately scoped follow-up if required; it must not be silently introduced into these binary-matched trials.

Handoff: the completed [EP-011 decision](RESEARCH_DECISION.md) permits EP-016 within this measured boundary. Implement only the verified streaming-generation path first, preserve the exact task-scoped acknowledgement chain, and keep unsupported phases noncancelling. Tracker and result documentation were completed on LOW after evidence interpretation.
