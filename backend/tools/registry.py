"""Tool registration and invocation.

`ToolRegistry.invoke` is the single choke point every tool call goes
through: input validation (Pydantic), then the handler, with every
expected failure (bad input, an authorization boundary, an
intentionally-unavailable capability) turned into a structured
ToolResult rather than an uncaught exception — a tool-calling agent
needs to see "this call failed and why", not a stack trace.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from observability.tracer import Tracer

from tools.exceptions import ToolAuthorizationError, ToolError, ToolInputError, ToolNotAvailableError


@dataclass(frozen=True)
class ToolResult:
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    # Callable[..., BaseModel] rather than Callable[[BaseModel], BaseModel]:
    # each concrete handler takes its own specific input_model subtype (a
    # narrower parameter type), which is exactly what a heterogeneous
    # registry of handlers looks like. Runtime safety comes from invoke()
    # always validating raw input against this Tool's own input_model
    # before calling handler — never from the type checker here.
    handler: Callable[..., BaseModel]


class ToolRegistry:
    def __init__(self, tracer: Optional[Tracer] = None) -> None:
        self._tools: dict[str, Tool] = {}
        # Phase 13: optional — the agent graph (Phase 7/9/10) doesn't
        # currently route through this registry at all (its nodes call
        # retrieval/modification/sandbox services directly; see
        # docs/architecture.md's "Evaluation" section for the same
        # honest scoping note about "tool selection"). This makes
        # ToolRegistry ready to be traced the moment something DOES
        # call it, without changing its behavior when nothing does.
        self._tracer = tracer

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def invoke(self, name: str, raw_input: dict) -> ToolResult:
        if self._tracer is None:
            return self._invoke(name, raw_input)

        argument_keys = sorted(raw_input.keys()) if isinstance(raw_input, dict) else []
        with self._tracer.span(None, f"tool:{name}", "tool", attributes={"tool_name": name, "argument_keys": argument_keys}) as span:
            result = self._invoke(name, raw_input)
            span.attributes["success"] = result.success
            if not result.success:
                span.attributes["tool_error"] = result.error
            return result

    def _invoke(self, name: str, raw_input: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: '{name}'")

        try:
            validated_input = tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            return ToolResult(success=False, error=f"Invalid input for '{name}': {exc}")

        try:
            output = tool.handler(validated_input)
        except (ToolInputError, ToolAuthorizationError, ToolNotAvailableError) as exc:
            return ToolResult(success=False, error=str(exc))
        except ToolError as exc:  # any other declared tool error
            return ToolResult(success=False, error=str(exc))

        return ToolResult(success=True, data=output.model_dump())
