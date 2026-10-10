# EP-016 architecture: acknowledged streaming mission worker

Design date: 10 October 2026. Status: implemented and verified on the pinned runtime;
EP-016 is complete for its controlled generation-phase scope. Scope follows [EP-011](RESEARCH_DECISION.md) and the
pinned, generation-only [EP-010 findings](CANCELLATION_PROBE_PLAN.md).

## Outcome and boundaries

A mission can request that an obsolete streaming request stop, continue advancing
the scene while acknowledgement is pending, and submit replacement work only
after a verified terminal boundary. The worker must also support an otherwise
identical streaming finish control. Prompt processing and unconfirmed phases
continue naturally when transport/server state is healthy.

The adapter implements the mechanism. EP-017 defines and compares automatic
stopping policies. FIFO and newest-pending retain their existing meanings and
defaults. EP-015 relevance is advisory evidence, not an unconditional cancellation
trigger: known target unavailability can invalidate mission assumptions while a
wait response remains executable.

## Implementation and live verification

The implementation is split across typed contracts, a pure evidence reducer, raw
streaming transport, task/log/slot monitoring, an exclusively owned server
session, the acknowledged worker, and an explicit mission harness. The ordinary
resident-server mission command retains its previous noncancelling behavior.

On 10 October 2026, the owned finish control completed the nominal mission with
one naturally completed request and verified reuse eligibility. The first
cancellation attempt then failed closed on an exclusive-end/inclusive-start log
offset bug; no replacement request was dispatched. After a focused regression
fix, run `streaming-mission-20261010-162820-cancel-goal-replacement-goal-replacement-e4f1336b`
recorded 209 unsupported prompt-phase decisions, one generation-phase stop, the
matching task-scoped cancel and release, a fresh post-release idle query, and a
saved reader trace. Its cancelled terminal became reusable about 59.9 ms after
the recorded stop intent; the distinct replacement task completed naturally and
the goal-replacement mission succeeded. These are integration controls, not a
policy comparison, energy measurement, or general cancellation claim.

EP-016 exposes explicit stop requests to a controlled integration harness. Its
first intervention is a predeclared goal replacement during an actual mission
request; it does not silently enable cancellation for every invalidating event.

## Findings from the existing implementation

The probe supplies useful raw-socket, SSE, log-following, and evidence examples.
It cannot be imported wholesale as a production acknowledgement authority:

- `normalize_slot` treats a missing/falsy `is_processing` as idle. Production
  parsing requires an explicit boolean, the expected slot ID, and the single-slot
  schema; missing/malformed fields are errors.
- `CancellationEvidence` stores stop intent as a boolean, accepts an idle event
  when the old task is absent even if another task is present, and defaults the
  synchronization audit to true. Production evidence needs explicit provenance,
  intent/action timestamps, an actually empty slot, and process start identity.
- Probe `sequence` values mix sampled slot replies with subsequently read log
  lines. A larger client sequence number does not establish server causality.
  Fresh-idle authorization must require a query sent after release was observed.
- The runner does not feed a complete natural-termination classification into
  its gate. Cancel/release logs alone cannot rule out EOS/error/limit races on
  short mission plans. The synthetic `ignore_eos` trial is insufficient here.
- `RawHTTPCall.start()` connects/sends synchronously, `response_content()` drops
  finish metadata, and ordinary EOF does not explicitly close the owned socket.
  These require production treatment before reuse.
- Finding one process named llama-server does not bind the selected HTTP endpoint
  or supplied log to that process, nor prove exclusive access.

Keep EP-010 source/results intact. Add focused production components; share or
extract helpers only when their behavior is independently adequate and covered.

## Components and ownership

| Component | Responsibility |
| --- | --- |
| `edgepilot/streaming_contracts.py` | Immutable session identity, evidence, stop receipts, worker status and terminal records |
| `edgepilot/streaming_evidence.py` | Pure terminal-state reducer; no sockets, sleeps, scene access, or policy decisions |
| `edgepilot/streaming_worker.py` | Owned streaming transport, strict response assembly, background monitoring, bounded command/event queues, and the public worker facade |
| `edgepilot/streaming_session.py` | Exclusive local server session, process/endpoint/log binding, pinned inventory, lifecycle and bounded cleanup |
| `robot/model.py` | Backward-compatible result extensions and a separate cancellable-worker protocol |
| `mission_runtime.py` | Retain active request until terminal result; explicit cancelled/uncertain outcomes; existing guardrails and scene ownership |
| `mission.py` and a small EP-016 harness | Explicit transport choice, streaming finish control, controlled stop interventions, manifests and measurements |

The mission loop owns scene state and consumes immutable worker events. Background
I/O produces observations and executes commands; it never mutates the scene or
installs plans. The pure reducer is driven by the worker facade on the mission
thread, making ordering tests deterministic. Connect/send/receive, slot polling,
log reads/writes, shutdown and joins run outside the mission loop. `submit`,
`request_cancel`, and `poll` must not wait for network or thread completion.

Use one transport reader and one session-monitor/controller thread per active
session. Only one slot query may be in flight. Bound buffers and per-poll event
work; queue overflow or evidence loss poisons the session rather than dropping
events. Observations timestamped later than the loop's sampled `now_ns` wait for
the next poll. All application timestamps use one episode-relative monotonic
clock; server log order uses file offsets, not clock subtraction.

## Session identity and supported deployment

Initially support an explicitly owned, local single-slot server launched from the
existing Windows binary/model on D:, with the audited flags plus slot monitoring
and timestamped logs. The session owns fresh stdout/stderr files, PID and process
creation time, the bound loopback endpoint, and an exclusive local run lock.
Verify listener-to-PID binding, health/props/slot schema, executable/DLL/model
hashes, build, arguments, and source/configuration hashes before requests.

Do not infer authority over an existing user-owned process. Refuse to start a
second resident model if one is already running; provide the documented manual
transition when necessary. Do not silently attach to an unverified log or claim
the loopback endpoint authenticates other local clients. The first release
requires uncontested use; unexpected tasks, log replacement/truncation, process
restart, or identity drift poison the session.

Recheck process identity and evidence continuity at dispatch, before actual
shutdown, and before reuse. Hash immutable artifacts at session startup and
finish, not every observation tick. A restart always creates a new session and
invalidates all old evidence.

## Worker interface and terminal semantics

Retain the existing `ModelWorker` for non-streaming callers. Add a cancellable
protocol with nonblocking `request_cancel(request_id, reason, now_ns)` and an
immutable status snapshot. Receipts distinguish recorded intent, already pending,
already terminal, wrong request, and unsupported/unconfirmed phase. Recorded
intent means only that the command was accepted, not that the socket was closed.
An unsupported attempt is not latched for automatic cancellation later in
generation; a policy/harness must make a new decision from current information.

Append defaulted fields to `ModelResult` so existing positional callers survive:
termination (`completed`, `cancelled`, `failed`), terminal-observed time, occupied
duration, session/task identity, and optional evidence reference. Keep transport
start/finish timestamps and usage distinct from acknowledgement delay. Cancelled
results contain no installable plan. Failures use the existing failure fields;
uncertain state stops admission even after the terminal failure is consumed.

`poll(now_ns)` emits at most one terminal result for a request. Normal completion
also waits for its required boundary in this adapter. Consuming a result and
clearing the episode's active reference does not override a poisoned worker's
independent refusal to accept another request. No result is delivered before its
terminal-observed time, even if HTTP bytes finished earlier.

At each scene step: apply all due external events; advance/monitor skills; poll
worker evidence and consume an available terminal result; if still active, allow
the explicit harness/policy to request a stop using current state; then admit new
work only when permitted. This makes a same-step goal change reject a completed
old response without issuing an unnecessary stop. The I/O controller rechecks
phase/task/session immediately before shutting down the socket.

## Evidence and state transitions

The worker remains occupied from submission through terminal acknowledgement.
Use these conceptual states, with typed terminal outcomes:

`idle -> running -> stop_pending -> verifying -> terminal_ready -> idle`

Natural completion goes directly from running to verifying. Any uncertain path
goes to `poisoned`, emits one failure, and requires session retirement. Verified
terminal readiness also requires reader exit and successful required trace
retention, preventing old reader data from being attached to a subsequent call.

For cancellation, require all of:

1. Verified pinned session identity and an unambiguous client-request/server-task
   mapping. Use the owned single slot and task-launch log after the request's
   recorded start offset, corroborated by active slot observations. Conflicting
   or missing mapping cannot authorize a stop. Do not equate response IDs with
   server task IDs without calibration.
2. Positive generation evidence for that task, recorded before actual stop action;
   preserve decoded-token progress and observation age. Partial SSE content is
   not a token counter. Recheck generation when servicing the stop command.
3. Request-scoped intent, actual socket shutdown completion, matching cancel and
   release events in audited server-log order, and an applicable synchronization
   argument for the recorded generation configuration.
4. A classified termination cause, using the pinned-source rule below and a
   continuous task log interval. Do not treat absence of an SSE terminal marker
   after disconnect as proof. If observable evidence cannot distinguish the race,
   classify it inconclusive and retire the session; additional instrumentation
   would be a separately scoped decision.
5. A fresh, successful idle query whose send time follows observation of the
   matching release, explicit `is_processing=false`, no conflicting task, stable
   process identity, and continuity of task lifecycle logs through that boundary.

A complete natural response that races stop uses the natural path: valid HTTP
framing, complete SSE terminal metadata, mapped release and fresh idle. It never
counts as cancellation success. Valid terminal `length` may establish natural
server completion but must still fail the existing plan decoder. Missing or
contradictory completion evidence after a disconnect is uncertain, not success.

Server-log ordering, client observation ordering and physical compute completion
are separate facts. A post-release fresh idle query supplies an observed bound
under the pinned source argument, not a directly measured GPU stop timestamp.

### Terminal-cause rule from the pinned source

Inspected [server-context.cpp at ce8caa6e60a03093351d6016a818720e0d46f0fb](https://raw.githubusercontent.com/ggml-org/llama.cpp/ce8caa6e60a03093351d6016a818720e0d46f0fb/tools/server/server-context.cpp):
the non-speculative generation stop path calls final timing output and final
response before release; task errors call `send_error`, which logs the task ID;
the cancellation handler releases the matching slot directly. The release log
precedes setting slot state idle. These observations support the following
conservative inference for the owned, non-speculative session:

- Parse final task timing records, task errors, cancellation and release. Final
  timing records must be distinguished from periodic generation-rate logs.
- A final timing record before release means natural stop won; an error means
  failure. Neither counts as cancellation, even if a cleanup cancel is present.
- Classify curtailed cancellation only with the full acknowledgement chain and
  no natural/error record in the complete task interval through release. Unknown
  lifecycle records or incomplete log continuity make the result inconclusive.

The retained EP-010 stream finish/stop logs exhibit the expected distinction.
Live calibration must verify this parser against ordinary mission EOS/limit/error
and race cases before enabling cancellation; this design is not that verification.

## Streaming response and failure handling

Preserve raw bytes while incrementally decoding HTTP framing, UTF-8 and SSE.
Validate HTTP status, bounded headers/body, chunk/content-length termination,
choice index, finish reason, final marker and optional usage records. Permit
role-only and empty-choice usage frames; reject malformed/truncated or conflicting
streams. Reconstruct the existing response envelope only after complete natural
termination, then use `decode_plan` unchanged. Never fabricate `finish_reason=stop`.

Socket timeout is not an episode watchdog. Use finite connect/read, request,
acknowledgement, and cleanup bounds in one monotonic domain. Missing acknowledgement,
reader/monitor failure, corrupted evidence, or trace-write failure prevents reuse.
Unsupported phase alone does not poison an otherwise healthy natural request.
Episode deadline stops admission and requests bounded drain; emergency cleanup
may terminate only the owned server and is recorded as recovery, never cancellation.
`close` must be bounded and retain partial evidence and cleanup outcome even after
an exception. The CLI writes failure/summary artifacts from its finalization path.

## Verification and implementation slices

1. **MEDIUM:** Implement typed evidence/state contracts and the pure reducer.
   Lock tests for intent/action ordering, false/missing idle, stale slot replies,
   foreign tasks, PID reuse, duplicate/late evidence, natural races, unsupported
   phases, timeouts, and no reuse on ambiguity. Pause for ASTRA HIGH only if the
   pinned-source rule fails live calibration or exposes another release path.
2. **MEDIUM:** Add owned session, transport and strict stream reconstruction.
   Use synchronized HTTP stubs to test split UTF-8/HTTP/SSE, truncated responses,
   final usage/finish markers, disconnect during connect/read, bounded close,
   blocked monitor, queue overflow, and file/identity failures. Reuse probe helper
   concepts without changing historical probe behavior.
3. **MEDIUM:** Integrate result semantics, opt-in streaming finish mode and the
   controlled intervention harness. Test scene progress during acknowledgement,
   same-step completion, late old results, exactly one terminal per request,
   queue/coalescing cleanup, and unchanged guardrails. Preserve non-stream defaults.
4. **MEDIUM:** Calibrate source/log assumptions and run predeclared real mission
   finish/stop pairs. Capture distinct server task identity and correct next-mission
   completion after each acknowledged cancellation. Keep before-generation and
   completed-before-stop attempts in denominators; do not lengthen mission outputs
   or retry selectively to manufacture supported cases.
5. **LOW:** Update user docs and trackers after evidence review. Mark EP-016 complete
   only after offline ordering tests and live task-scoped acknowledgement/reuse
   succeed; otherwise report the implemented slice and remaining evidence gap.

For the initial live development check, freeze two order-counterbalanced pairs
per nominal, goal-replacement, and station-unavailable scenario; finish arms use
identical streaming/monitoring settings. Controlled stop arms have explicit
request IDs/triggers and separate labels from policy benchmarks. Add matched
non-streaming and streaming finish runs to expose transport/monitoring overhead.
Calibrate event timing separately, freeze it before the manifest, and retain all
unsupported/raced/failed/unattempted entries. These small samples are integration
evidence, not a scheduling-benefit claim.

Measure transport time, slot-query intervals/RTTs, log receipt, intent-to-shutdown,
intent-to-observed-release, intent-to-fresh-idle, total worker occupancy, next-task
dispatch/first-data/terminal latency, scene observation gaps, reconstruction cost,
mission outcome and guardrail refusals. Report unavailable usage as null, preserve
raw traces/source snapshots, and do not equate elapsed worker time with GPU compute
or energy. EP-017 will decide how policies use this mechanism.
