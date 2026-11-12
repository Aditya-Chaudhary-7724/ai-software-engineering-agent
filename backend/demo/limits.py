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
_LOCK_KEY = "demo_limits"
_EVENT_RETENTION = "2 days"

DEFAULTS = {
    "DEMO_ASK_PER_IP_PER_HOUR": 5,
    "DEMO_ASK_GLOBAL_PER_MINUTE": 1,
    "DEMO_DAILY_TOKEN_BUDGET": 140_000,
    "DEMO_ASK_TOKEN_RESERVE": 5_000,
}


@dataclass(frozen=True)
class DemoLimits:
    ask_per_ip_per_hour: int
    ask_global_per_minute: int
    daily_token_budget: int
    ask_token_reserve: int

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
                day, used = _one(
                    conn,
                    "SELECT (now() AT TIME ZONE 'UTC')::date, "
                    "COALESCE((SELECT tokens FROM demo_token_usage WHERE day = (now() AT TIME ZONE 'UTC')::date), 0)",
                )
                if used + limits.ask_token_reserve > limits.daily_token_budget:
                    (seconds,) = _one(
                        conn,
                        "SELECT EXTRACT(EPOCH FROM (date_trunc('day', now() AT TIME ZONE 'UTC') + interval '1 day' "
                        "- (now() AT TIME ZONE 'UTC')))",
                    )
                    raise DemoDailyLimitReachedError(int(seconds) + 1)
                conn.execute("INSERT INTO demo_rate_events (action, client_hash) VALUES (%s, %s)", (ACTION_ASK, client_hash))
                conn.execute(
                    "INSERT INTO demo_token_usage (day, tokens, requests) VALUES (%s, %s, 1) "
                    "ON CONFLICT (day) DO UPDATE SET tokens = demo_token_usage.tokens + EXCLUDED.tokens, "
                    "requests = demo_token_usage.requests + 1",
                    (day, limits.ask_token_reserve),
                )
        return Reservation(day=day, tokens=limits.ask_token_reserve)

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
