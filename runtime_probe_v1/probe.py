"""Resident-model integration probe. Standard library only; Python 3.11+."""
import argparse
import concurrent.futures
import json
import math
from pathlib import Path
import time
import urllib.request
import uuid

PROBE_VERSION = '1.1'
WAIT_PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': ['wait']},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500},
    },
    'required': ['action', 'reason'],
    'additionalProperties': False,
}


def read_json(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def validate_response(response):
    choice = response['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Response did not finish normally; reject truncated output.')
    plan = json.loads(choice['message']['content'])
    if not isinstance(plan, dict) or set(plan) != {'action', 'reason'}:
        raise ValueError(f'Expected exactly action and reason fields; got {sorted(plan) if isinstance(plan, dict) else type(plan).__name__}.')
    if plan['action'] != 'wait':
        raise ValueError('This probe only permits the wait action.')
    if not isinstance(plan['reason'], str) or not 1 <= len(plan['reason'].strip()) <= 500:
        raise ValueError('Reason must contain 1 to 500 nonblank characters.')
    return plan


def run(base_url, output_root, change_goal_after=None, tick_seconds=0.05):
    # Loading/health is outside the timed request interval.
    health = read_json(base_url.rstrip('/') + '/health')
    if health.get('status') != 'ok':
        raise RuntimeError(f'Server is not ready: {health}')
    run_dir = Path(output_root) / ('probe-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    request_id = uuid.uuid4().hex
    request_goal_version = current_goal_version = 1
    payload = {
        'model': 'edgepilot',
        'messages': [{'role': 'user', 'content': 'A robot is idle with no mission. Return only a JSON object with exactly two fields: action set to wait and a short reason.'}],
        'temperature': 0, 'max_tokens': 128, 'stream': False,
        'cache_prompt': False,
        'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {'type': 'json_object', 'schema': WAIT_PLAN_SCHEMA},
    }
    (run_dir / 'request.json').write_text(json.dumps({
        'probe_version': PROBE_VERSION, 'request_id': request_id, 'goal_version': request_goal_version,
        'url': base_url, 'tick_target_seconds': tick_seconds,
        'change_goal_after_seconds': change_goal_after, 'payload': payload,
    }, indent=2), encoding='utf-8')
    started = time.perf_counter()
    ticks = []
    outcome, error, plan = 'failed', None, None
    with (run_dir / 'events.jsonl').open('w', encoding='utf-8') as events:
        def emit(kind, **fields):
            events.write(json.dumps({'elapsed_seconds': time.perf_counter() - started,
                                     'event': kind, **fields}) + '\n')
            events.flush()
        emit('request_submitted', request_id=request_id, goal_version=request_goal_version)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def invoke():
                worker_started = time.perf_counter()
                response = read_json(base_url.rstrip('/') + '/v1/chat/completions', payload)
                return response, time.perf_counter() - worker_started
            future = pool.submit(invoke)
            next_tick = started
            while True:
                now = time.perf_counter()
                if change_goal_after is not None and current_goal_version == 1 and now - started >= change_goal_after:
                    current_goal_version = 2
                    emit('goal_changed', goal_version=2, superseded_request_id=request_id)
                if now >= next_tick:
                    ticks.append(now - started)
                    emit('observation_tick', goal_version=current_goal_version)
                    # Missed ticks are not backfilled; real gaps remain visible.
                    next_tick = now + tick_seconds
                if future.done():
                    break
                time.sleep(min(0.01, max(0, next_tick - time.perf_counter())))
            try:
                response, http_elapsed = future.result()
                (run_dir / 'response.json').write_text(json.dumps(response, indent=2), encoding='utf-8')
                emit('response_received', request_id=request_id, http_elapsed_seconds=http_elapsed)
                if request_goal_version != current_goal_version:
                    outcome = 'rejected_stale_goal'
                    emit(outcome, request_id=request_id, request_goal_version=request_goal_version,
                         current_goal_version=current_goal_version)
                else:
                    try:
                        plan = validate_response(response)
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        outcome = 'rejected_invalid_plan'
                        error = f'{type(exc).__name__}: {exc}'
                        emit(outcome, request_id=request_id, error=error)
                    else:
                        outcome = 'accepted'
                        emit(outcome, request_id=request_id, plan=plan)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                emit('failed', error=error)
    summary = {
        'probe_version': PROBE_VERSION,
        'outcome': outcome, 'error': error, 'request_id': request_id,
        'response_file': str((run_dir / 'response.json').resolve()) if (run_dir / 'response.json').exists() else None,
        'request_goal_version': request_goal_version, 'final_goal_version': current_goal_version,
        'request_to_decision_seconds': time.perf_counter() - started,
        'observation_ticks': len(ticks),
        'max_observation_gap_seconds': max((b-a for a, b in zip(ticks, ticks[1:])), default=None),
        'accepted_plan': plan, 'run_directory': str(run_dir.resolve()),
        'note': 'Observation ticks are synthetic, not robot sensing or physics. No action was executed. Stale rejection does not cancel GPU work.',
    }
    (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('Use a finite nonnegative number of seconds.')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--output-dir', default='runs')
    parser.add_argument('--change-goal-after', type=nonnegative, default=None,
                        help='Seconds after dispatch to replace goal. Use 0 for guaranteed supersession before result acceptance.')
    args = parser.parse_args()
    try:
        summary = run(args.base_url, args.output_dir, args.change_goal_after)
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\nCheck that llama-server is running and /health reports ok.\n')
    print(json.dumps(summary, indent=2))
    return 1 if summary['outcome'] in ('failed', 'rejected_invalid_plan') else 0


if __name__ == '__main__':
    raise SystemExit(main())
