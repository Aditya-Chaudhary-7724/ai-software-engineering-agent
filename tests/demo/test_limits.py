"""Demo rate limits and daily token budget (backend/demo/limits.py),
against the real test PostgreSQL: windows, the budget's reserve/settle/
release cycle, UTC day rollover, concurrency, and IP privacy."""

import threading

import psycopg
import pytest

from demo.limits import DemoDailyLimitReachedError, DemoLimiter, DemoLimits, DemoRateLimitedError, client_key
from vectorstore.migrations import apply_schema

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres

SECRET = "demo-secret-" + "s" * 30


def _limits(**overrides):
    base = dict(ask_per_ip_per_hour=5, ask_global_per_minute=100, daily_token_budget=1_000_000, ask_token_reserve=5_000)
    base.update(overrides)
    return DemoLimits(**base)


@pytest.fixture
def db():
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        apply_schema(conn)
        conn.execute("TRUNCATE demo_rate_events, demo_token_usage")
    yield TEST_DATABASE_URL
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        conn.execute("TRUNCATE demo_rate_events, demo_token_usage")


def _sql(sql, params=()):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


# -- client identity ------------------------------------------------------------------


def test_ipv4_addresses_are_distinct_and_stable():
    assert client_key("203.0.113.7", SECRET) == client_key(" 203.0.113.7 ", SECRET)
    assert client_key("203.0.113.7", SECRET) != client_key("203.0.113.8", SECRET)


def test_one_ipv6_slash_64_is_one_client():
    same = client_key("2001:db8:1:2::1", SECRET) == client_key("2001:db8:1:2:ffff:ffff:ffff:ffff", SECRET)
    assert same and client_key("2001:db8:1:2::1", SECRET) != client_key("2001:db8:1:3::1", SECRET)


def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_client():
    assert client_key("::ffff:203.0.113.7", SECRET) == client_key("203.0.113.7", SECRET)


@pytest.mark.parametrize("bad", ["", "not-an-ip", "203.0.113.999", "203.0.113.7, 10.0.0.1"])
def test_anything_but_one_ip_is_refused(bad):
    with pytest.raises(ValueError):
        client_key(bad, SECRET)


def test_the_key_never_contains_the_address_and_depends_on_the_secret():
    key = client_key("203.0.113.7", SECRET)
    assert "203.0.113" not in key and key != client_key("203.0.113.7", SECRET + "x")


# -- windows -----------------------------------------------------------------------------


@requires_postgres
def test_per_ip_hourly_limit_with_retry_after(db):
    limiter = DemoLimiter(db, _limits(ask_per_ip_per_hour=3))
    a, b = client_key("203.0.113.1", SECRET), client_key("203.0.113.2", SECRET)
    for _ in range(3):
        limiter.reserve_ask(a)
    with pytest.raises(DemoRateLimitedError) as caught:
        limiter.reserve_ask(a)
    assert 3500 < caught.value.retry_after_seconds <= 3601
    limiter.reserve_ask(b)  # another visitor is unaffected


@requires_postgres
def test_global_per_minute_limit_applies_across_visitors(db):
    limiter = DemoLimiter(db, _limits(ask_global_per_minute=1))
    limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    with pytest.raises(DemoRateLimitedError) as caught:
        limiter.reserve_ask(client_key("203.0.113.2", SECRET))
    assert 0 < caught.value.retry_after_seconds <= 61


@requires_postgres
def test_windows_slide(db):
    limiter = DemoLimiter(db, _limits(ask_per_ip_per_hour=1))
    key = client_key("203.0.113.1", SECRET)
    limiter.reserve_ask(key)
    _sql("UPDATE demo_rate_events SET created_at = now() - interval '61 minutes'")
    limiter.reserve_ask(key)


@requires_postgres
def test_old_events_are_purged(db):
    limiter = DemoLimiter(db, _limits())
    limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    _sql("UPDATE demo_rate_events SET created_at = now() - interval '3 days'")
    limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(1,)]


@requires_postgres
def test_no_raw_ip_is_stored(db):
    DemoLimiter(db, _limits()).reserve_ask(client_key("198.51.100.23", SECRET))
    stored = _sql("SELECT client_hash FROM demo_rate_events")[0][0]
    assert "198.51.100" not in stored


# -- budget --------------------------------------------------------------------------------


@requires_postgres
def test_the_budget_is_reserved_before_the_call_and_refuses_overspend(db):
    limiter = DemoLimiter(db, _limits(daily_token_budget=10_000, ask_token_reserve=5_000))
    limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    limiter.reserve_ask(client_key("203.0.113.2", SECRET))
    with pytest.raises(DemoDailyLimitReachedError) as caught:
        limiter.reserve_ask(client_key("203.0.113.3", SECRET))
    assert 0 < caught.value.retry_after_seconds <= 86_401  # until 00:00 UTC
    assert _sql("SELECT tokens, requests FROM demo_token_usage") == [(10_000, 2)]


@requires_postgres
def test_settle_replaces_the_reserve_with_reported_usage(db):
    limiter = DemoLimiter(db, _limits(daily_token_budget=10_000, ask_token_reserve=5_000))
    reservation = limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    limiter.settle(reservation, 1_200)
    assert _sql("SELECT tokens FROM demo_token_usage") == [(1_200,)]
    limiter.reserve_ask(client_key("203.0.113.2", SECRET))  # room again


@requires_postgres
def test_without_reported_usage_the_reserve_stays_charged(db):
    limiter = DemoLimiter(db, _limits(ask_token_reserve=5_000))
    limiter.settle(limiter.reserve_ask(client_key("203.0.113.1", SECRET)), None)
    assert _sql("SELECT tokens FROM demo_token_usage") == [(5_000,)]


@requires_postgres
def test_release_refunds_a_failed_call_but_keeps_the_attempt(db):
    limiter = DemoLimiter(db, _limits(ask_token_reserve=5_000))
    limiter.release(limiter.reserve_ask(client_key("203.0.113.1", SECRET)))
    assert _sql("SELECT tokens, requests FROM demo_token_usage") == [(0, 0)]
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(1,)]


@requires_postgres
def test_the_budget_resets_on_a_new_utc_day(db):
    limiter = DemoLimiter(db, _limits(daily_token_budget=5_000, ask_token_reserve=5_000))
    limiter.reserve_ask(client_key("203.0.113.1", SECRET))
    _sql("UPDATE demo_token_usage SET day = day - 1")
    limiter.reserve_ask(client_key("203.0.113.2", SECRET))


# -- concurrency ----------------------------------------------------------------------------


@requires_postgres
def test_concurrent_requests_never_exceed_a_limit(db):
    limiter = DemoLimiter(db, _limits(ask_per_ip_per_hour=3))
    key = client_key("203.0.113.1", SECRET)
    outcomes = []
    barrier = threading.Barrier(12)

    def attempt():
        barrier.wait()
        try:
            limiter.reserve_ask(key)
            outcomes.append("ok")
        except DemoRateLimitedError:
            outcomes.append("limited")

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 3 and outcomes.count("limited") == 9
