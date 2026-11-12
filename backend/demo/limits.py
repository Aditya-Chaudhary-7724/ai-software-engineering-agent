"""Rate limits and the daily LLM token budget for the public demo.

Protects the free Groq quota (not money: the deployment is $0) from
anonymous demo visitors. Applies ONLY to the demo principal; the operator
is never limited here.

Backed by PostgreSQL, not process memory, so limits hold across gunicorn
workers and restarts (an in-memory limiter would multiply by the number of
processes and reset on every deploy). Each check runs in ONE transaction
under a transaction-scoped advisory lock: count the window, then admit
(record) or refuse. Two concurrent requests can therefore never both slip
past a limit.

Visitor identity: the frontend server passes the client's IP; it is reduced
to a network (IPv4 address, IPv6 /64, since one IPv6 user typically owns a
whole /64) and stored only as an HMAC keyed with a server secret, never as
the raw address.

Budget: a fixed reserve is taken BEFORE the LLM call (so concurrent calls
cannot overspend), then settled to the token count the provider reported,
kept in full if it reported none, and refunded if the call failed.
Days are UTC; the budget resets at 00:00 UTC.
"""

import hashlib
import hmac
import ipaddress
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import psycopg

ACTION_ASK = "ask"
ACTION_TASK = "task"
_LOCK_KEY = "demo_limits"
_EVENT_RETENTION = "2 days"

DEFAULTS = {
    "DEMO_ASK_PER_IP_PER_HOUR": 5,
    "DEMO_ASK_GLOBAL_PER_MINUTE": 1,
    "DEMO_DAILY_TOKEN_BUDGET": 140_000,
    "DEMO_ASK_TOKEN_RESERVE": 5_000,
    "DEMO_TASKS_PER_DAY": 5,
    "DEMO_TASKS_PER_IP_PER_DAY": 2,
    "DEMO_TASK_TOKEN_RESERVE": 8_000,
    "DEMO_TASK_MAX_TOKENS": 12_000,
}
_UTC_DAY_START = "(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"


@dataclass(frozen=True)
class DemoLimits:
    ask_per_ip_per_hour: int
    ask_global_per_minute: int
    daily_token_budget: int
    ask_token_reserve: int
    tasks_per_day: int = DEFAULTS["DEMO_TASKS_PER_DAY"]
    tasks_per_ip_per_day: int = DEFAULTS["DEMO_TASKS_PER_IP_PER_DAY"]
    task_token_reserve: int = DEFAULTS["DEMO_TASK_TOKEN_RESERVE"]
    task_max_tokens: int = DEFAULTS["DEMO_TASK_MAX_TOKENS"]

    @classmethod
    def from_env(cls) -> "DemoLimits":
        def value(name: str) -> int:
            raw = os.environ.get(name, "").strip()
            return int(raw) if raw.isdigit() and int(raw) >= 1 else DEFAULTS[name]

        return cls(
            ask_per_ip_per_hour=value("DEMO_ASK_PER_IP_PER_HOUR"),
            ask_global_per_minute=value("DEMO_ASK_GLOBAL_PER_MINUTE"),
            daily_token_budget=value("DEMO_DAILY_TOKEN_BUDGET"),
            ask_token_reserve=value("DEMO_ASK_TOKEN_RESERVE"),
            tasks_per_day=value("DEMO_TASKS_PER_DAY"),
            tasks_per_ip_per_day=value("DEMO_TASKS_PER_IP_PER_DAY"),
            task_token_reserve=value("DEMO_TASK_TOKEN_RESERVE"),
            task_max_tokens=value("DEMO_TASK_MAX_TOKENS"),
        )


class DemoLimitError(Exception):
    """A demo request refused by a limit. `code` is the API error code."""

    code = "demo_limit"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(self.code)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class DemoRateLimitedError(DemoLimitError):
    code = "demo_rate_limited"


class DemoDailyLimitReachedError(DemoLimitError):
    code = "demo_daily_limit_reached"


@dataclass(frozen=True)
class Reservation:
    day: date
    tokens: int


def _one(conn: psycopg.Connection, sql: str, params: Any = None) -> tuple:
    """A query that always returns exactly one row (aggregates, now())."""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return tuple(row)


def _seconds_until_utc_midnight(conn: psycopg.Connection) -> int:
    (seconds,) = _one(
        conn,
        "SELECT EXTRACT(EPOCH FROM (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '1 day' "
        "- (now() AT TIME ZONE 'UTC')))",
    )
    return int(seconds) + 1


def client_key(raw_ip: str, secret: str) -> str:
    """HMAC of the client's network. Raises ValueError for anything that is
    not an IP address."""
    address = ipaddress.ip_address(raw_ip.strip())
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is None:
        network = str(ipaddress.IPv6Network(f"{address}/64", strict=False).network_address) + "/64"
    else:
        network = str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address)
    return hmac.new(secret.encode(), network.encode(), hashlib.sha256).hexdigest()


class DemoLimiter:
    def __init__(self, database_url: str, limits: Optional[DemoLimits] = None) -> None:
        self._database_url = database_url
        self._limits = limits or DemoLimits.from_env()

    def reserve_ask(self, client_hash: str) -> Reservation:
        """Admits one demo Ask (recording it) and reserves its tokens, or
        raises DemoRateLimitedError / DemoDailyLimitReachedError."""
        limits = self._limits
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_LOCK_KEY,))
                conn.execute(f"DELETE FROM demo_rate_events WHERE created_at < now() - interval '{_EVENT_RETENTION}'")
                self._check_window(conn, "1 hour", limits.ask_per_ip_per_hour, client_hash)
                self._check_window(conn, "1 minute", limits.ask_global_per_minute, None)
                return self._admit(conn, ACTION_ASK, client_hash, limits.ask_token_reserve)

    def reserve_task(self, conn: psycopg.Connection, client_hash: str) -> Reservation:
        """Admits one demo agent task and reserves its tokens INSIDE the
        caller's open transaction (not committed here), so the caller can
        insert the task row in the same transaction: either all of it is
        recorded, or none of it (e.g. when the task row is refused)."""
        limits = self._limits
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_LOCK_KEY,))
        self._check_since_utc_midnight(conn, ACTION_TASK, limits.tasks_per_ip_per_day, client_hash)
        self._check_since_utc_midnight(conn, ACTION_TASK, limits.tasks_per_day, None)
        return self._admit(conn, ACTION_TASK, client_hash, limits.task_token_reserve)

    def settle_task(self, task_id: str, used_tokens: int) -> bool:
        """Settles a demo task's reservation to its real total, EXACTLY ONCE
        (whatever the outcome: proposed, failed, ceiling). Returns False if
        there was nothing to settle (not a demo task, or already settled)."""
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_LOCK_KEY,))
                row = conn.execute(
                    "SELECT demo_usage_day, demo_reserved_tokens FROM agent_tasks "
                    "WHERE id = %s AND proposal_only AND NOT demo_settled FOR UPDATE",
                    (task_id,),
                ).fetchone()
                if row is None or row[0] is None:
                    return False
                day, reserved = row[0], int(row[1] or 0)
                used = max(0, int(used_tokens))
                conn.execute(
                    "UPDATE demo_token_usage SET tokens = GREATEST(0, tokens - %s + %s) WHERE day = %s",
                    (reserved, used, day),
                )
                conn.execute(
                    "UPDATE agent_tasks SET demo_used_tokens = %s, demo_settled = TRUE WHERE id = %s", (used, task_id)
                )
        return True

    def _admit(self, conn: psycopg.Connection, action: str, client_hash: str, reserve: int) -> Reservation:
        """Budget check, then record the event and the reservation."""
        day, used = _one(
            conn,
            "SELECT (now() AT TIME ZONE 'UTC')::date, "
            "COALESCE((SELECT tokens FROM demo_token_usage WHERE day = (now() AT TIME ZONE 'UTC')::date), 0)",
        )
        if used + reserve > self._limits.daily_token_budget:
            raise DemoDailyLimitReachedError(_seconds_until_utc_midnight(conn))
        conn.execute("INSERT INTO demo_rate_events (action, client_hash) VALUES (%s, %s)", (action, client_hash))
        conn.execute(
            "INSERT INTO demo_token_usage (day, tokens, requests) VALUES (%s, %s, 1) "
            "ON CONFLICT (day) DO UPDATE SET tokens = demo_token_usage.tokens + EXCLUDED.tokens, "
            "requests = demo_token_usage.requests + 1",
            (day, reserve),
        )
        return Reservation(day=day, tokens=reserve)

    @staticmethod
    def _check_since_utc_midnight(conn: psycopg.Connection, action: str, limit: int, client_hash: Optional[str]) -> None:
        where, params = "action = %s AND created_at >= " + _UTC_DAY_START, [action]
        if client_hash is not None:
            where += " AND client_hash = %s"
            params.append(client_hash)
        (count,) = _one(conn, f"SELECT count(*) FROM demo_rate_events WHERE {where}", params)
        if count >= limit:
            raise DemoDailyLimitReachedError(_seconds_until_utc_midnight(conn))

    def settle(self, reservation: Reservation, actual_tokens: Optional[int]) -> None:
        """Replaces the reserve with the provider-reported usage. With no
        report, the reserve stays charged (conservative)."""
        if actual_tokens is None:
            return
        self._adjust(reservation.day, int(actual_tokens) - reservation.tokens, 0)

    def release(self, reservation: Reservation) -> None:
        """Refunds a reservation whose call failed. The rate event stays
        (it was a real attempt)."""
        self._adjust(reservation.day, -reservation.tokens, -1)

    def _adjust(self, day: date, tokens_delta: int, requests_delta: int) -> None:
        with psycopg.connect(self._database_url) as conn:
            conn.execute(
                "UPDATE demo_token_usage SET tokens = GREATEST(0, tokens + %s), requests = GREATEST(0, requests + %s) "
                "WHERE day = %s",
                (tokens_delta, requests_delta, day),
            )

    @staticmethod
    def _check_window(conn: psycopg.Connection, window: str, limit: int, client_hash: Optional[str]) -> None:
        where = "action = %s AND created_at > now() - %s::interval"
        params: list = [ACTION_ASK, window]
        if client_hash is not None:
            where += " AND client_hash = %s"
            params.append(client_hash)
        count, oldest_wait = _one(
            conn,
            f"SELECT count(*), EXTRACT(EPOCH FROM (min(created_at) + %s::interval - now())) "
            f"FROM demo_rate_events WHERE {where}",
            [window, *params],
        )
        if count >= limit:
            raise DemoRateLimitedError(int(oldest_wait or 1) + 1)
