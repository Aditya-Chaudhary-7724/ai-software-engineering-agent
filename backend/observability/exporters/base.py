"""Optional external trace exporter abstraction.

Exactly one method, deliberately: every exporter this project might
ever add (LangSmith, Langfuse, a future in-house dashboard) needs
nothing more than "hand me a finished trace." `Tracer.finish_trace`
already wraps every exporter call in its own try/except (see
tracer.py), so an exporter failing never breaks the agent — but a
well-behaved exporter implementation should still avoid raising where
practical, as a second line of defense.
"""

from abc import ABC, abstractmethod

from observability.models import Trace


class TraceExporter(ABC):
    @abstractmethod
    def export(self, trace: Trace) -> None:
        """Send a finished (or in-progress) trace to an external system."""
