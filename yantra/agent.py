"""Agent Team entry discovered by ``adk web``, ``adk run`` and ``adk api_server``."""

from .adk_app import YantraAgent
from .adk_runtime import native_app

root_agent = YantraAgent(name="yantra")
app = native_app(root_agent, name="yantra")
