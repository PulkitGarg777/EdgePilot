"""Offline contract tests using a delayed local HTTP stub; no model required."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from probe import run, validate_response


def reply(content='{"action":"wait","reason":"No mission assigned."}', finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': content}}]}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_json({'status': 'ok'})

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        time.sleep(0.25)
        if self.path.startswith('/invalid/'):
            self.send_json(reply('{"action":"wait","reason":"ok","extra":true}'))
        else:
            self.send_json(reply())

    def log_message(self, *args):
        pass


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_observations_continue_during_http_request(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = run(self.url, folder)
            self.assertEqual(summary['outcome'], 'accepted')
            events = [json.loads(line) for line in (Path(summary['run_directory']) / 'events.jsonl').read_text().splitlines()]
            received_at = next(e['elapsed_seconds'] for e in events if e['event'] == 'response_received')
            active_ticks = [e for e in events if e['event'] == 'observation_tick' and 0.02 < e['elapsed_seconds'] < received_at]
            self.assertGreaterEqual(len(active_ticks), 2)

    def test_late_result_never_accepted_after_goal_change(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = run(self.url, folder, change_goal_after=0)
            self.assertEqual(summary['outcome'], 'rejected_stale_goal')
            self.assertIsNone(summary['accepted_plan'])
            self.assertEqual(summary['final_goal_version'], 2)

    def test_invalid_plan_is_rejected_and_raw_response_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = run(self.url + '/invalid', folder)
            self.assertEqual(summary['outcome'], 'rejected_invalid_plan')
            self.assertIsNone(summary['accepted_plan'])
            self.assertIn('extra', summary['error'])
            self.assertTrue(Path(summary['response_file']).exists())

    def test_malformed_unsupported_or_truncated_plans_are_rejected(self):
        invalid = [reply('not json'), reply('[]'), reply('{"action":"move","reason":"go"}'),
                   reply('{"action":"wait","reason":""}'), reply(finish='length'),
                   reply('{"action":"wait","reason":"ok","command":"extra"}')]
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    validate_response(response)


if __name__ == '__main__':
    unittest.main()
