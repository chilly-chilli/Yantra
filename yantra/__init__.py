"""Yantra: evidence checked investment research on Google ADK."""

__version__ = "0.5.0"

def __getattr__(name):
    # Keep local diagnosis/version usable before optional execution imports work.
    if name == "agent":
        from importlib import import_module
        return import_module(".agent", __name__)
    raise AttributeError(name)
