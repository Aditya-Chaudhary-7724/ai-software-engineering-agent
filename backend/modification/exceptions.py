"""Exceptions raised by the code modification subsystem."""


class ModificationError(Exception):
    """Base class for all modification-related errors."""


class StaleChangeError(ModificationError):
    """Raised when apply_change detects the target file changed since the
    proposal was generated — refuse rather than silently clobber it.
    """
