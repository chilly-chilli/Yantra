"""Recheck two historical-cutoff fixtures without editing application/tests.

Run from repository root: .venv/bin/python docs/validation/v0.3.3/check_fixture_clock.py
Only knowledge recording timestamps are fixed; research deadlines still use real time.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root / "tests")]
names = [
    "test_knowledge.KnowledgeTests.test_two_sessions_reuse_verified_fact_not_historical_conclusion",
    "test_knowledge_runtime.RuntimeKnowledgeTests.test_two_actual_adk_sessions_reuse_compare_and_replay_fixed_revisions",
]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
with patch("yantra.knowledge.store.now", return_value="2026-10-13T00:00:00+00:00"), \
     patch("yantra.knowledge.runtime.now", return_value="2026-10-13T00:00:00+00:00"):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
