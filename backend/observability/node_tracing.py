"""Wraps a LangGraph node function (`(state: dict) -> dict`) with a
tracing span, without touching the node's own body.

Applied entirely from `agent/graph.py` (the wiring layer, whose job is
already composing nodes) rather than inside `agent/nodes.py` itself —
this keeps every Phase 7/9/10 node function's business logic
completely untouched (a true zero-diff on `nodes.py`) while still
capturing structured attributes specific to what each node actually
does, via a small `extract` callback supplied at registration time.

`extract` must be a pure, side-effect-free function of the node's
return dict to a small dict of SAFE, already-known-shaped fields
(counts, decisions, booleans, short strings already present in the
project's own state model) — never raw content (diffs, stdout/stderr,
full file contents, the question text). Every node registered in
`agent/graph.py` passes its own `extract`; see that file for exactly
which fields each node exposes.
"""

import functools
from typing import Any, Callable, Dict, Optional, TypeVar

from observability.tracer import Tracer

# Generic over the node's OWN state type (AgentState, a TypedDict) rather
# than a fixed `Dict[str, Any]` — this is what lets the wrapped function
# keep exactly the same static type LangGraph's `add_node` expects
# (`Callable[[AgentState], dict]`), so wrapping a node never turns into
# a type error at the `workflow.add_node(...)` call site in agent/graph.py.
StateT = TypeVar("StateT")
ExtractFn = Callable[[dict], Dict[str, Any]]


def traced_node(
    tracer: Tracer, name: str, kind: str, extract: Optional[ExtractFn] = None
) -> Callable[[Callable[[StateT], dict]], Callable[[StateT], dict]]:
    def decorator(fn: Callable[[StateT], dict]) -> Callable[[StateT], dict]:
        @functools.wraps(fn)
        def wrapper(state: StateT) -> dict:
            with tracer.span(None, name, kind) as span:
                result = fn(state)
                if extract is not None:
                    try:
                        span.attributes.update(extract(result))
                    except Exception:
                        pass  # extraction is best-effort observability, never lets a bad extractor break the node
                return result

        return wrapper

    return decorator
