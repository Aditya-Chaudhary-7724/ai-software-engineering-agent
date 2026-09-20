"""Stateful LangGraph agent (Phase 7).

Wires Phases 1-6 into one workflow: task classification -> planning ->
repository search -> graph search -> analysis -> decision -> answer
(fully real, grounded via Phase 6) / test (honest stub, Phase 10
planned) / human-approval-gated modify (real approval mechanism via
LangGraph interrupt; Phase 9's actual modification is honest stub).

Explicit state (agent/state.py), conditional routing and bounded
retries (agent/graph.py), no hidden chain-of-thought — only safe
execution-summary log entries (agent/nodes.py).
"""

from agent.models import AgentRunResult
from agent.service import AgentService

__all__ = ["AgentRunResult", "AgentService"]
