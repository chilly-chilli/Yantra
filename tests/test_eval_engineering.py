"""Evaluation formats and authorization guards; no model-wording assertions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from eval_behavior import Approval, approved_cases, generate


class EvaluationEngineeringTests(unittest.TestCase):
    def test_fixed_material_has_explicit_disclosure_before_cutoff(self):
        from tests_eval_support import material_text
        from yantra.materials import RawMaterialAdapter
        from yantra.schemas import ReadArguments
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.txt"
            source.write_text(material_text("financial"))
            adapter = RawMaterialAdapter.create(root / "materials", "EVAL_SYNTHETIC", [source])
            documents = adapter.read_materials(ReadArguments(company="EVAL_SYNTHETIC", cutoff="2026-10-13"))["documents"]
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["date"], "2026-10-01")

    def test_plan_has_five_fictional_cases_and_never_executes(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/eval_behavior.py")], capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["status"], "not_executed")
        self.assertEqual(len(set(plan["cases"])), 5)
        dataset = json.loads((ROOT / "tests/eval/datasets/behavior.json").read_text())
        for case in dataset["eval_cases"]:
            self.assertTrue(case["context"]["fictional"])
            self.assertGreater(len(case["context"]["criteria"]), 1)
            self.assertTrue(case["context"]["turns"])

    def test_live_requires_explicit_finite_total_budgets_and_known_cases(self):
        dataset = json.loads((ROOT / "tests/eval/datasets/behavior.json").read_text())
        valid = {"approved": True, "model_profile": "existing", "max_models": 10,
            "max_cost_cny": 1.0, "run_seconds": 300.0, "cases": ["missing_numbers"]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "approval.json"
            for invalid in ({**valid, "approved": False}, {**valid, "max_cost_cny": 50.0}, {**valid, "run_seconds": 7201.0},
                            {**valid, "max_models": 0}, {**valid, "max_cost_cny": float("nan")},
                            {**valid, "cases": ["unknown"]}, {**valid, "cases": ["missing_numbers"] * 2}):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    approved_cases(path, dataset)
            path.write_text(json.dumps(valid))
            approval, cases = approved_cases(path, dataset)
            self.assertEqual(approval.max_cost_cny, 1)
            self.assertEqual(cases[0]["eval_case_id"], "missing_numbers")

    def test_cli_manifest_and_configs_declare_no_default_model_judge(self):
        manifest = yaml.safe_load((ROOT / "agents-cli-manifest.yaml").read_text())
        self.assertEqual(manifest["framework"], "google-adk")
        self.assertEqual(manifest["create_params"]["deployment_target"], "none")
        for name in ("eval_config.yaml", "behavior_eval_config.yaml"):
            config = yaml.safe_load((ROOT / "tests/eval" / name).read_text())
            selected = set(config["metrics_to_run"])
            defined = {metric["name"] for metric in config["custom_metrics"]}
            self.assertTrue(selected <= defined)
            self.assertNotIn("Gemini", json.dumps(config))

    def test_transport_gate_rejects_errors_empty_responses_and_unrelated_json(self):
        spec = importlib.util.spec_from_file_location("metric", ROOT / "tests/eval/transport_metric.py")
        metric = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(metric)
        for payload in ({}, {"status": "error"}, {"run_id": "run-x", "status": "error"}):
            self.assertEqual(metric.evaluate({"responses": [{"response": {"parts": [{"text": json.dumps(payload)}]}}]})["score"], 0)
        self.assertEqual(metric.evaluate({})["score"], 0)
        self.assertEqual(metric.evaluate({"responses": [{"response": {"parts": [{"text": '{"run_id":"run-x","status":"waiting"}'}]}}]})["score"], 1)

    def test_generator_budget_and_isolation_with_synthetic_backend_only(self):
        from yantra.configuration import ConfigStore
        from yantra.models import ModelCatalog
        from test_v2 import PRICE, V2TestBackend
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = ConfigStore(root / "existing.json")
            ModelCatalog(settings).add("existing", {"kind": "llm", "provider": "openai-compatible", "model_id": "synthetic",
                "base_url": "https://unused.invalid/v1", "api_key_env": "EVAL_SYNTHETIC_KEY", "pricing": PRICE})
            original = settings.path.read_bytes()
            approval = Approval(approved=True, model_profile="existing", max_models=8, max_cost_cny=1.0,
                run_seconds=120.0, cases=["missing_numbers"])
            dataset = json.loads((ROOT / "tests/eval/datasets/behavior.json").read_text())
            args = Namespace(env_file=None, config=settings.path, output=root / "generated")
            # Simulate the live generator wiring, without a provider or network call.
            with patch.dict(os.environ, {"EVAL_SYNTHETIC_KEY": "synthetic-secret"}), patch("yantra.models.create_backend", return_value=V2TestBackend()):
                previous = os.getenv("YANTRA_RUNS_DIR")
                try:
                    generate(args, approval, dataset["eval_cases"][:1])
                finally:
                    if previous is None:os.environ.pop("YANTRA_RUNS_DIR", None)
                    else:os.environ["YANTRA_RUNS_DIR"] = previous
            summary = json.loads((args.output / "generation.json").read_text())
            self.assertEqual(summary["generated_cases"], ["missing_numbers"])
            self.assertLessEqual(summary["model_calls"], approval.max_models)
            self.assertLessEqual(summary["model_cost_cny"], approval.max_cost_cny)
            self.assertEqual(summary["judge"], "not executed")
            state = json.loads(next((args.output / "runs").glob("run-*/state.json")).read_text())
            self.assertEqual(state["capabilities"]["extensions"]["tools"], {})
            self.assertEqual(state["capabilities"]["models"]["embedding"], None)
            self.assertEqual(settings.path.read_bytes(), original)
            self.assertNotIn("synthetic-secret", (args.output / "traces.json").read_text())
