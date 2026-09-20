"""Exceptions raised by the observability subsystem."""


class ObservabilityError(Exception):
    """Base class for all observability-related errors.

    Deliberately never raised from the hot path of tracing itself
    (`Tracer.span`/`start_trace`/`finish_trace` catch and log instead of
    raising — see tracer.py's own docstring) — this exists for the few
    places that need to fail loudly by design, such as constructing an
    optional external exporter without the credentials it requires.
    """


class MissingCredentialsError(ObservabilityError):
    """Raised when an optional external exporter (e.g. `LangSmithExporter`)
    is constructed without the credentials it needs. Never raised by
    the local, credential-free tracing path (`Tracer` + `JSONFileRecorder`/
    `InMemoryRecorder`), which always works without any external service.
    """
