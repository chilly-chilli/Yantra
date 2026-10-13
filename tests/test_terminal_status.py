"""OSC 7501 through a real PTY, with stdout kept machine readable."""
import base64
import io
import json
import os
from pathlib import Path
import pty
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from yantra.terminal_status import TerminalStatus


ROOT = Path(__file__).resolve().parents[1]


class TerminalStatusTests(unittest.TestCase):
    def invoke(self, arguments, *, bootstrap=None, input_text=None, terminal=True):
        with tempfile.TemporaryDirectory() as directory:
            command = ([sys.executable, "-c", bootstrap] if bootstrap else [sys.executable, "-m", "yantra"])
            command += ["--config", str(Path(directory) / "config.json"), *arguments]
            environment = os.environ | {"PYTHONPATH": str(ROOT), "TERM": "xterm-256color"}
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen(command, cwd=directory, env=environment,
                    stdin=slave if input_text is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=slave if terminal else subprocess.PIPE)
                if input_text is not None:
                    os.write(master, input_text.encode())
                stdout, errors = process.communicate(timeout=30)
                output = bytearray()
                if terminal:
                    os.set_blocking(master, False)
                    while True:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                else:
                    output.extend(errors)
                records = []
                for body in re.findall(rb"\x1b\]7501;(.*?)\x1b\\", output):
                    record = dict(pair.decode().split("=", 1) for pair in body.split(b":"))
                    record["msg"] = base64.b64decode(record["msg"]).decode()
                    self.assertEqual(record["app"], "yantra")
                    records.append(record)
                return process.returncode, stdout.decode(), output.decode(), records
            finally:
                if slave is not None:
                    os.close(slave)
                os.close(master)

    def test_json_stdout_remains_one_object_with_terminal_stderr(self):
        code, stdout, errors, records = self.invoke(["--output", "json", "models", "list"])
        self.assertEqual(code, 0, errors)
        self.assertIn("models", json.loads(stdout)["data"])
        self.assertNotIn("\x1b", stdout)
        self.assertEqual([r["state"] for r in records], ["working", "done"])

    def test_redirected_output_has_no_escapes(self):
        code, stdout, errors, records = self.invoke(["--output", "json", "models", "list"], terminal=False)
        self.assertEqual(code, 0, errors)
        json.loads(stdout)
        self.assertEqual(records, [])
        self.assertNotIn("\x1b", stdout + errors)

    def test_interactive_prompt_reports_question_and_resumes(self):
        code, stdout, errors, records = self.invoke(["configure"], input_text="exit\n")
        self.assertEqual(code, 0, errors)
        self.assertEqual([r["state"] for r in records], ["working", "blocked", "working", "done"])
        self.assertEqual(records[1]["kind"], "question")

    def test_research_end_states_and_query_semantics(self):
        for command, business, expected in (("start", "waiting", "blocked"), ("start", "partial", "error"),
                                            ("start", "failed", "error"), ("start", "cancelled", "idle"),
                                            ("start", "completed", "done"), ("status", "failed", "done")):
            bootstrap = ("import yantra.cli as cli\n"
                         f"async def execute(args): return {{'status': {business!r}}}\n"
                         "cli.execute = execute\nraise SystemExit(cli.main())")
            args = ["--output", "json", "start", "--company", "synthetic"] if command == "start" else ["--output", "json", "status", "run-synthetic"]
            with self.subTest(command=command, business=business):
                code, stdout, errors, records = self.invoke(args, bootstrap=bootstrap)
                self.assertEqual(code, 0, errors)
                self.assertEqual(json.loads(stdout)["data"]["status"], business)
                self.assertEqual(records[-1]["state"], expected)
                if expected == "blocked":
                    self.assertEqual(records[-1]["kind"], "permission")

    def test_errors_interrupts_and_eof(self):
        for exception, code, expected in (("RuntimeError('SECRET_SENTINEL')", 1, "error"),
                                           ("KeyboardInterrupt()", 130, "idle"), ("EOFError()", 1, "idle")):
            bootstrap = ("import yantra.cli as cli\n"
                         f"async def execute(args): raise {exception}\n"
                         "cli.execute = execute\nraise SystemExit(cli.main())")
            actual, stdout, errors, records = self.invoke(["--output", "json", "models", "list"], bootstrap=bootstrap)
            self.assertEqual(actual, code, errors)
            self.assertIn("error", json.loads(stdout))
            self.assertNotIn("SECRET_SENTINEL", stdout + errors)
            self.assertEqual(records[-1]["state"], expected)

    def test_messages_are_bounded_utf8_and_no_control_characters(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        with patch.dict(os.environ, {"TERM": "xterm"}):
            TerminalStatus(stream).report("blocked", "标题\n\t\x1b\x85" + "中" * 3000, kind="question")
        encoded = stream.getvalue()
        body = encoded.removeprefix("\x1b]7501;").removesuffix("\x1b\\")
        msg = base64.b64decode(body.split("msg=", 1)[1])
        self.assertLessEqual(len(encoded.encode()), 4096)
        self.assertLessEqual(len(msg), 2048)
        self.assertTrue(all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in msg.decode()))
        with patch.dict(os.environ, {"TERM": "dumb"}):
            stream.seek(0); stream.truncate()
            TerminalStatus(stream).report("working", "test")
            self.assertEqual(stream.getvalue(), "")
