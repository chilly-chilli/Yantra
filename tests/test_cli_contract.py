"""Agent contract through real subprocesses; temporary synthetic data only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CLIContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runs = self.root / "custom runs"
        self.library = self.root / "knowledge"
        self.config = self.root / "config.json"

    def invoke(self, *arguments, output="json", bootstrap=None, input_text=""):
        argv = ["--config", str(self.config), "--output", output, *map(str, arguments)]
        command = ([sys.executable, "-c", bootstrap, *argv] if bootstrap else
                   [sys.executable, "-m", "yantra", *argv])
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        environment["YANTRA_CONFIG"] = str(self.config)
        return subprocess.run(command, cwd=self.root, env=environment, input=input_text, text=True,
                              capture_output=True, timeout=45)

    def response(self, process, code=0, command=None):
        self.assertEqual(process.returncode, code, process.stderr + process.stdout)
        result = json.loads(process.stdout)
        self.assertEqual(result["schema_version"], "1")
        self.assertEqual(set(result), {"schema_version", "command", "data" if code == 0 else "error"})
        if command:
            self.assertEqual(result["command"], command)
        if code:
            self.assertIsInstance(result["error"]["retryable"], bool)
        return result

    def state(self, status="waiting", name="run-synthetic", **extra):
        state = {"run_id": name, "mode": "offline-demo", "status": status,
                 "budget": {"tool_calls": 0, "model_calls": 0, "repair_rounds": 0},
                 "limits": {"total_tools": 20, "max_models": 60},
                 "stop_reasons": ["synthetic gap"] if status in {"partial", "failed"} else [],
                 "pending_user": {"question": "confirm synthetic parameters"} if status == "waiting" else None,
                 "assumptions": {"confirmed": False, "parameters": {}}, "artifacts": {},
                 "input_snapshot": {}, "report": None, **extra}
        directory = self.runs / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state.json").write_text(json.dumps(state))
        return name

    def test_parser_errors_and_no_secret_echo(self):
        cases = [("start",), ("status", "run-missing", "--unknown", "SECRET_SENTINEL"),
                 ("start", "--company", "demo", "--mode", "SECRET_SENTINEL"),
                 ("start", "--company", "demo", "--limits", "{SECRET_SENTINEL"),
                 ("start", "--company", "demo", "--limits", "[]"),
                 ("start", "--company", "demo", "--limits", '{"x":NaN}')]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                process = self.invoke(*arguments)
                error = self.response(process, 2)["error"]
                self.assertIn(error["code"], {"argument_error", "invalid_json"})
                self.assertNotIn("SECRET_SENTINEL", process.stdout + process.stderr)

    def test_noninteractive_menus_require_explicit_entry(self):
        for command in ("models", "extensions"):
            with self.subTest(command=command):
                self.assertEqual(self.response(self.invoke(command), 2)["error"]["code"], "subcommand_required")
                self.assertEqual(self.response(self.invoke(command, "interactive"), 2)["error"]["code"], "interactive_required")

    def test_brand_banner_only_in_interactive_terminal(self):
        from yantra import __version__
        bootstrap = """from unittest.mock import patch
import sys
from yantra.cli import main
with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True), patch.dict('os.environ', {'TERM': 'xterm-256color', 'NO_COLOR': ''}):
    raise SystemExit(main())
"""
        for command, answer in (("configure", "exit\n"), ("models", "back\n"), ("extensions", "back\n")):
            result = self.invoke(command, output="text", bootstrap=bootstrap, input_text=answer)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("实 证 研 策", result.stdout)
            self.assertNotIn("实证研策", result.stdout)
            self.assertNotIn("Yantra", result.stdout)
            self.assertEqual(result.stdout.count(f"v{__version__}"), 1)
            self.assertEqual(result.stdout.count("⬤"), 1)
            self.assertIn("█", result.stdout)
            self.assertIn("▀", result.stdout)
            self.assertIn("▄", result.stdout)
            self.assertNotIn("╭", result.stdout)
            self.assertNotIn("\033", result.stdout)
        # The parent may set NO_COLOR; clear it explicitly for the color check.
        color_bootstrap = bootstrap.replace("{'TERM': 'xterm-256color', 'NO_COLOR': ''}",
                                            "{'TERM': 'xterm-256color'}").replace(
                                                "    raise SystemExit(main())", "    __import__('os').environ.pop('NO_COLOR', None)\n    raise SystemExit(main())")
        colored = self.invoke("configure", output="text", input_text="exit\n", bootstrap=color_bootstrap)
        self.assertIn("\033[38;2;225;43;34m⬤\033[0m", colored.stdout)
        plain = self.invoke("configure", output="text", input_text="exit\n",
                            bootstrap=bootstrap.replace("xterm-256color", "dumb"))
        self.assertIn("YANTRA v", plain.stdout)
        self.assertNotIn("⬤", plain.stdout)
        self.response(self.invoke("models", "list", bootstrap=bootstrap))
        redirected = self.invoke("configure", output="text", input_text="exit\n",
                                 bootstrap=bootstrap.replace(
                                     "patch.object(sys.stdout, 'isatty', return_value=True)",
                                     "patch.object(sys.stdout, 'isatty', return_value=False)"))
        self.assertNotIn("█", redirected.stdout)

    def test_missing_and_invalid_run(self):
        for command in ("status", "resume", "cancel", "replay"):
            self.assertEqual(self.response(self.invoke(command, "run-missing", "--runs-dir", self.runs), 2)["error"]["code"], "run_not_found")
        self.assertEqual(self.response(self.invoke("status", "../secret"), 2)["error"]["field"], "run_id")

    def test_status_states_and_resume_context(self):
        for status in ("running", "waiting", "completed", "partial", "failed", "cancelled"):
            name = self.state(status)
            data = self.response(self.invoke("status", name, "--runs-dir", self.runs), command="status")["data"]
            self.assertEqual(data["status"], status)
            self.assertEqual(data["run_id"], name)
            for action in data["next_actions"]:
                argv = action["argv"]
                self.assertEqual(argv[argv.index("--runs-dir") + 1], str(self.runs))
                self.assertEqual(argv[argv.index("--config") + 1], str(self.config))
        self.state("waiting")
        environment = self.root / "model context.env"
        environment.write_text("# controlled fixture; no credentials\n")
        context = self.response(self.invoke("status", name, "--runs-dir", self.runs, "--env-file", environment))["data"]
        for action in context["next_actions"]:
            self.assertEqual(action["argv"][action["argv"].index("--env-file") + 1], str(environment))
        text = self.invoke("status", name, "--runs-dir", self.runs, output="text")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("等待确认", text.stdout)
        self.assertIn(str(self.runs), text.stdout)

    def test_cancel_terminal_and_active(self):
        for status, outcome in (("completed", "already_terminal"), ("partial", "already_terminal"),
                                ("failed", "already_terminal"), ("cancelled", "cancelled"),
                                ("running", "request_accepted"), ("waiting", "request_accepted")):
            name = self.state(status)
            request = self.runs / name / "cancel.request"
            request.unlink(missing_ok=True)
            data = self.response(self.invoke("cancel", name, "--runs-dir", self.runs))["data"]
            self.assertEqual(data["cancellation"], outcome)
            self.assertEqual(request.exists(), outcome == "request_accepted")
            self.assertEqual(json.loads((self.runs / name / "state.json").read_text())["status"], status)

    def test_catalog_and_knowledge_read_commands(self):
        for arguments in (("models", "list"), ("extensions", "list"),
                          ("knowledge", "--knowledge-dir", self.library, "status"),
                          ("knowledge", "--knowledge-dir", self.library, "query", "synthetic"),
                          ("preferences", "--knowledge-dir", self.library, "list")):
            self.response(self.invoke(*arguments), command=" ".join((arguments[0], arguments[-1])) if arguments[0] in {"models", "extensions"} else None)
        self.assertIn("tesseract-auto", self.invoke("models", "list", output="text").stdout)

    def test_disabled_extension_registration_reports_actual_state(self):
        specification = self.root / "tool.json"
        specification.write_text(json.dumps({"description": "controlled synthetic disabled tool",
            "command": [sys.executable, "-c", "print('{}')"], "enabled": False}))
        data = self.response(self.invoke("extensions", "add", "tools", "disabled", "--file", specification))["data"]
        self.assertFalse(data["enabled"])
        saved = self.response(self.invoke("extensions", "list"))["data"]
        self.assertFalse(saved["tools"]["disabled"]["enabled"])

    def test_output_help_and_version_positions(self):
        from yantra import __version__
        import tomllib
        self.assertEqual(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"], __version__)
        for arguments in (("--version",), ("--help",), ("knowledge", "query", "--help")):
            data = self.response(self.invoke(*arguments))["data"]
            if arguments == ("--version",):
                self.assertEqual(data["version"], __version__)
            elif arguments == ("--help",):
                self.assertIn(f"Yantra v{__version__}", data["help"])
        self.assertEqual(self.invoke("--version", output="text").stdout.strip(), "yantra " + __version__)
        process = self.invoke("models", "list", "--output", "json", output="text")
        self.response(process, command="models list")

    def test_preferences_preflight_preserves_requirements(self):
        prefix = ("preferences", "--knowledge-dir", self.library)
        parameters = self.root / "parameters.json"
        parameters.write_text('{"horizon":"18个月"}')
        arguments = (*prefix, "remember", "synthetic", "--meaning", "synthetic default", "--parameters-file", parameters)
        self.response(self.invoke(*arguments, "--apply-to", "run-missing", "--runs-dir", self.runs), 2)
        self.assertEqual(self.response(self.invoke(*prefix, "list"))["data"]["requirements"], [])
        saved = self.response(self.invoke(*arguments))["data"]
        self.response(self.invoke(*prefix, "forget", saved["id"], "--apply-to", "run-missing", "--runs-dir", self.runs), 2)
        self.assertEqual(len(self.response(self.invoke(*prefix, "list"))["data"]["requirements"]), 1)

    def test_json_file_validation_and_mutual_exclusion(self):
        bad = self.root / "bad.json"
        bad.write_text("{SECRET_SENTINEL")
        for flag, arguments in (("limits", ("start", "--company", "demo")),
                                ("pricing", ("start", "--company", "demo")),
                                ("parameters", ("resume", "run-test")),
                                ("changes", ("knowledge", "edit-fact", "entry", "--reason", "test")),
                                ("budget", ("knowledge", "rebuild"))):
            with self.subTest(flag=flag):
                error = self.response(self.invoke(*arguments, "--" + flag + "-file", bad), 2)["error"]
                self.assertEqual(error["code"], "invalid_json")
                self.assertEqual(error["field"], "--" + flag + "-file")
                self.response(self.invoke(*arguments, "--" + flag, "{}", "--" + flag + "-file", bad), 2)

    def test_offline_start_resume_compare_replay_and_budget_stop(self):
        from test_adk_structure import test_materials
        inputs = test_materials(self.root / "inputs")
        limits = self.root / "limits.json"
        limits.write_text('{"total_tools":20}')
        arguments = ("start", "--company", "DEMO", "--cutoff", "2025-12-31", "--materials-dir", inputs,
                     "--runs-dir", self.runs, "--limits-file", limits, "--reference-price", "5",
                     "--price-time", "2025-12-31", "--price-source", "synthetic-fixture")
        started = self.response(self.invoke(*arguments), command="start")["data"]
        self.assertEqual(started["status"], "waiting")
        name = started["run_id"]
        self.assertIsNotNone(started["pending_user"])
        waiting = self.response(self.invoke("resume", name, "--runs-dir", self.runs))["data"]
        self.assertEqual(waiting["status"], "waiting")
        parameters = self.root / "parameters.json"
        parameters.write_text('{"growth_rate":0.03}')
        completed = self.response(self.invoke("resume", name, "--runs-dir", self.runs, "--decision", "modify",
                                             "--parameters-file", parameters), command="resume")["data"]
        self.assertEqual(completed["status"], "partial")
        replayed = self.response(self.invoke("replay", name, "--runs-dir", self.runs), command="replay")["data"]
        self.assertEqual(replayed["status"], "partial")
        self.assertTrue(replayed["comparison"]["equal"])
        compared = self.response(self.invoke("compare", name, replayed["run_id"], "--runs-dir", self.runs), command="compare")["data"]
        self.assertTrue(compared["equal"])
        limits.write_text('{"total_tools":0}')
        failed = self.response(self.invoke(*arguments))["data"]
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["stop_reasons"])
        limits.write_text('{"total_tools":4}')
        partial = self.response(self.invoke(*arguments))["data"]
        self.assertEqual(partial["status"], "waiting")
        partial = self.response(self.invoke("resume", partial["run_id"], "--runs-dir", self.runs, "--decision", "confirm"))["data"]
        self.assertEqual(partial["status"], "partial")
        limits.write_text('{"total_tools":20}')
        waiting = self.response(self.invoke(*arguments))["data"]
        cancelled = self.response(self.invoke("resume", waiting["run_id"], "--runs-dir", self.runs, "--decision", "cancel"))["data"]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.response(self.invoke("cancel", waiting["run_id"], "--runs-dir", self.runs))["data"]["cancellation"], "cancelled")

    def test_knowledge_query_parameters_reach_existing_service(self):
        from yantra.knowledge.store import KnowledgeStore
        store = KnowledgeStore(self.library)
        company, _ = store.identity({"company": "synthetic"}, "fixture")
        def save(kind, content, topics):
            item = {"company_id": company, "kind": kind, "content": content, "topics": topics}
            proposal = store.propose(item, "fixture", "fixture", store.version(company))
            return store.commit(proposal["id"], allow_pending=True)
        source = save("source", {"text": "synthetic source full text", "title": "one"}, ["alpha"])
        save("source", {"text": "other synthetic text", "title": "two"}, ["beta"])
        prefix = ("knowledge", "--knowledge-dir", self.library)
        data = self.response(self.invoke(*prefix, "query", "--topic", "alpha", "--topic", "gamma", "--limit", "1",
                                         "--context-bytes", "10000", "--summary-only"))["data"]
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["entry_id"], source["entry_id"])
        self.assertNotIn("text", data["results"][0]["item"]["content"])
        with store.connect() as db:
            recorded = json.loads(db.execute("SELECT payload FROM operations WHERE kind='search' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(recorded["query"]["topics"], ["alpha", "gamma"])
        self.assertEqual(recorded["query"]["limit"], 1)
        self.assertEqual(recorded["query"]["context_bytes"], 10000)
        self.assertTrue(recorded["query"]["summary_only"])
        empty = self.response(self.invoke(*prefix, "query", "--context-bytes", "1"))["data"]
        self.assertEqual(empty["results"], [])
        for flag, value in (("--limit", "0"), ("--limit", "101"), ("--context-bytes", "100001")):
            error = self.response(self.invoke(*prefix, "query", flag, value), 2)["error"]
            self.assertEqual(error["code"], "validation_error")
        self.response(self.invoke(*prefix, "view", source["entry_id"]))
        from yantra.materials import RawMaterialAdapter
        adapter = RawMaterialAdapter.create(self.root / "materials", "synthetic", [])
        raw = b"synthetic source text"
        metadata = adapter.add(raw, ".txt", "synthetic-cli-fixture")
        store.archive_source(metadata, raw, "fixture")
        self.response(self.invoke(*prefix, "source", metadata["id"]))
        impact = self.response(self.invoke(*prefix, "delete-source", metadata["id"], "--impact"))["data"]
        self.assertIn("confirmation", impact)
        self.response(self.invoke(*prefix, "delete-source", metadata["id"]))
        fact = save("fact", {"value": 12}, [])
        changes = self.root / "changes.json"
        changes.write_text('{"value":13}')
        edited = self.response(self.invoke(*prefix, "edit-fact", fact["entry_id"], "--changes-file", changes, "--reason", "synthetic correction"))["data"]
        self.assertEqual(edited["item"]["content"]["value"], 13)
        budget = self.root / "budget.json"
        budget.write_text('{"max_cost_cny":1,"run_seconds":60,"max_models":1}')
        rebuilt = self.response(self.invoke(*prefix, "rebuild", "--budget-file", budget))["data"]
        self.assertEqual(rebuilt["vector_rows_built"], 0)

    def test_local_catalog_mutations_and_checks(self):
        profile = self.root / "model.json"
        profile.write_text(json.dumps({"kind": "ocr", "provider": "command", "command": [sys.executable]}))
        self.response(self.invoke("models", "add", "synthetic", "--file", profile))
        self.response(self.invoke("models", "select", "ocr", "synthetic"))
        self.assertTrue(self.response(self.invoke("models", "check", "synthetic"))["data"]["ready"])
        tool = self.root / "tool.json"
        tool.write_text(json.dumps({"description": "synthetic tool", "command": [sys.executable]}))
        self.response(self.invoke("extensions", "add", "tools", "synthetic", "--file", tool))
        for action in ("disable", "enable", "check"):
            self.response(self.invoke("extensions", action, "tools", "synthetic"))
        profile.write_text(json.dumps({"kind": "llm", "provider": "openai-compatible", "base_url": "https://user:SECRET_SENTINEL@example.invalid"}))
        result = self.invoke("models", "add", "invalid", "--file", profile)
        self.response(result, 2)
        self.assertNotIn("SECRET_SENTINEL", result.stdout + result.stderr)

    def test_eof_interactive_text_and_json_execution_failure(self):
        tty = "from unittest.mock import patch\nimport sys\nfrom yantra.cli import main\nwith patch.object(sys.stdin, 'isatty', return_value=True):\n    raise SystemExit(main())\n"
        for command in ("configure", "models", "extensions"):
            result = self.invoke(command, output="text", bootstrap=tty)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("输入已结束", result.stderr)
        for command, input_text in (("models", "back\n"), ("extensions", "back\n"), ("configure", "exit\n")):
            result = self.invoke(command, output="text", bootstrap=tty, input_text=input_text)
            self.assertEqual(result.returncode, 0, result.stderr)
        bootstrap = "import yantra.cli as cli\nasync def abort(args):\n    print('synthetic progress')\n    raise EOFError()\ncli.execute = abort\nraise SystemExit(cli.main())\n"
        result = self.invoke("models", "list", bootstrap=bootstrap)
        self.assertEqual(self.response(result, 1)["error"]["code"], "input_aborted")
        self.assertIn("synthetic progress", result.stderr)
        bootstrap = bootstrap.replace("EOFError()", "RuntimeError('SECRET_SENTINEL')")
        result = self.invoke("models", "list", bootstrap=bootstrap)
        self.assertEqual(self.response(result, 1)["error"]["code"], "execution_error")
        self.assertNotIn("SECRET_SENTINEL", result.stdout + result.stderr)

    def test_preferences_application_race_reports_durable_result(self):
        name = self.state("waiting", knowledge={"root": str(self.library), "company_id": "synthetic"})
        prefix = ("preferences", "--knowledge-dir", self.library)
        missing = self.response(self.invoke(*prefix, "forget", "missing", "--apply-to", name, "--runs-dir", self.runs), 2)
        self.assertEqual(missing["error"]["code"], "preference_not_found")
        bootstrap = "from unittest.mock import patch\nfrom yantra.cli import main\nwith patch('yantra.knowledge.cli.atomic_bytes', side_effect=OSError('SECRET_SENTINEL')):\n    raise SystemExit(main())\n"
        process = self.invoke(*prefix, "remember", "synthetic", "--meaning", "synthetic default", "--parameters", '{"horizon":"18个月"}',
                              "--apply-to", name, "--runs-dir", self.runs, bootstrap=bootstrap)
        error = self.response(process, 1)["error"]
        self.assertEqual(error["code"], "preference_application_failed")
        saved = error["details"]["completed"]
        self.assertTrue(saved["saved"])
        self.assertEqual(error["details"]["application"]["status"], "failed")
        requirements = self.response(self.invoke(*prefix, "list"))["data"]["requirements"]
        self.assertEqual(requirements[0]["id"], saved["id"])
        self.assertNotIn("SECRET_SENTINEL", process.stdout + process.stderr)
        process = self.invoke(*prefix, "forget", saved["id"], "--apply-to", name, "--runs-dir", self.runs, bootstrap=bootstrap)
        error = self.response(process, 1)["error"]
        self.assertEqual(error["details"]["completed"]["id"], saved["id"])
        self.assertEqual(self.response(self.invoke(*prefix, "list"))["data"]["requirements"], [])
        applied = self.response(self.invoke(*prefix, "remember", "synthetic", "--meaning", "default", "--parameters", '{"horizon":"24个月"}',
                                           "--apply-to", name, "--runs-dir", self.runs, "--modify-this-task"))["data"]
        self.assertEqual(applied["application"], "pending_at_scheduling_boundary")
        request = json.loads((self.runs / name / "preferences.request.json").read_text())
        self.assertEqual(request, {"modify_this_task": True, "keys": ["horizon"]})

    def test_preferences_preflight_terminal_library_and_scope(self):
        name = self.state("completed", knowledge={"root": str(self.library), "company_id": "synthetic"})
        arguments = ("preferences", "--knowledge-dir", self.library, "remember", "synthetic", "--meaning", "default",
                     "--parameters", '{"horizon":"18个月"}', "--apply-to", name, "--runs-dir", self.runs)
        for changes in ({"status": "completed"}, {"status": "waiting", "knowledge": {"root": str(self.root / "other"), "company_id": "synthetic"}},
                        {"status": "waiting", "knowledge": {"root": str(self.library), "company_id": "other"}}):
            self.state(**changes)
            extra = ("--scope", "synthetic") if changes.get("knowledge", {}).get("company_id") == "other" else ()
            self.assertEqual(self.response(self.invoke(*arguments, *extra), 2)["error"]["code"], "preference_target_invalid")
            self.assertEqual(self.response(self.invoke("preferences", "--knowledge-dir", self.library, "list"))["data"]["requirements"], [])

    def test_pricing_file_reaches_raw_engine_without_live_execution(self):
        price = self.root / "pricing.json"
        specification = {"input_per_million": 1, "output_per_million": 2, "source": "synthetic-price", "as_of": "2026-10-13"}
        price.write_text(json.dumps(specification))
        bootstrap = """from types import SimpleNamespace
from unittest.mock import patch
from yantra.cli import main
state = {'run_id':'run-fixture', 'mode':'test', 'status':'partial', 'budget':{}, 'limits':{}}
def create(*args, **kwargs):
    state['pending_user'] = {'received_pricing':kwargs['pricing']}
    return SimpleNamespace(state=state)
async def run(engine):
    print('synthetic progress on stderr')
    return engine.state
with patch('yantra.research.RawEngine.create_raw', side_effect=create), patch('yantra.adk_app.run_engine', side_effect=run):
    raise SystemExit(main())
"""
        result = self.invoke("start", "--company", "synthetic", "--mode", "real-model", "--material", "synthetic.txt",
                             "--pricing-file", price, bootstrap=bootstrap)
        self.assertEqual(self.response(result)["data"]["pending_user"]["received_pricing"], specification)
        self.assertIn("synthetic progress", result.stderr)


if __name__ == "__main__":
    unittest.main()
