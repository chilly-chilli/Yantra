"""Explicit SYNTHETIC setup for injected-backend regression fixtures."""
from pathlib import Path
from yantra.knowledge.preferences import Preferences
from yantra.knowledge.store import KnowledgeStore
from yantra.investment_logic import CHOICE_KINDS
from yantra.research import RawEngine


def setup_preferences(storage, knowledge_dir=None):
    preferences = Preferences(KnowledgeStore(knowledge_dir or Path(storage)/"knowledge"))
    if not any(p["id"] == "research-setup" for p in preferences.list(history=True)):
        preferences.setup({"horizon": "12个月", "framework": "auto", "confirmation_mode": "autonomous",
                           "authorization": {"status": "granted", "scope": list(CHOICE_KINDS)}})


def create_test_raw(storage, research, inputs, **kwargs):
    setup_preferences(storage, kwargs.get("knowledge_dir"))
    return RawEngine.create_raw(storage, research, inputs, **kwargs)
