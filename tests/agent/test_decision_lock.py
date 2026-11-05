"""The in-process, per-thread decision lock in `agent.service` (F6).

The database's conditional UPDATE is the primary single-use gate for a
decision; this lock is the in-process second line. Audit fix: its registry
entry used to be removed AFTER the lock was released. In that window a
second caller could fetch and acquire the old lock, the entry was then
deleted underneath it, and a third caller created a fresh lock and ran
concurrently with the second. No database needed.
"""

import threading
from types import SimpleNamespace

import pytest

import agent.service as service_module
from agent.approval import ApprovalInProgressError
from agent.service import _decision_locks, _exclusive_decision


class _RecordingLock:
    """A real lock that records, at release time, whether it was still the
    registered lock for its thread id."""

    def __init__(self, thread_id, events):
        self._lock = threading.Lock()
        self._thread_id = thread_id
        self._events = events

    def acquire(self, blocking=True):
        return self._lock.acquire(blocking)

    def release(self):
        self._events.append(_decision_locks.get(self._thread_id) is self)
        self._lock.release()


def test_the_lock_is_unregistered_before_it_is_released(monkeypatch):
    events = []
    # Only agent.service's own reference: the real `threading.Lock` stays intact.
    monkeypatch.setattr(service_module, "threading", SimpleNamespace(Lock=lambda: _RecordingLock("thread-a", events)))

    with _exclusive_decision("thread-a"):
        pass

    assert events == [False], "the lock was released while still registered (lets a third decision run concurrently)"
    assert "thread-a" not in _decision_locks


def test_a_concurrent_decision_for_the_same_thread_is_refused():
    with _exclusive_decision("thread-b"):
        with pytest.raises(ApprovalInProgressError):
            with _exclusive_decision("thread-b"):
                pass
    # Released and unregistered: a later decision is possible again.
    with _exclusive_decision("thread-b"):
        pass
    assert "thread-b" not in _decision_locks


def test_decisions_for_different_threads_do_not_block_each_other():
    with _exclusive_decision("thread-c"):
        with _exclusive_decision("thread-d"):
            pass
