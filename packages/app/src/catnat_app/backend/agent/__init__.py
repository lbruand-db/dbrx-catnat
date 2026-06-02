"""LLM agent loop for catnat.

Wires a Databricks Foundation Model API endpoint (Claude via the
OpenAI-compatible chat-completions surface) to the catnat MCP server. The
agent loop ingests a chat history, drives tool calls against MCP, and
streams events back to the FastAPI route.
"""

from .events import AgentEvent, sse
from .loop import run_agent
from .prompts import SYSTEM_PROMPT

__all__ = ["AgentEvent", "SYSTEM_PROMPT", "run_agent", "sse"]
