from datetime import datetime, timezone

from gitflic_package.limiter import RateLimiter, backoff_seconds, parse_retry_after


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock.value += delay


def test_rate_limiter_waits_between_requests() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = RateLimiter(10.0, clock=clock, sleeper=sleeper)

    limiter.wait()
    limiter.wait()

    assert sleeper.delays == [10.0]


def test_retry_after_accepts_seconds_and_http_date() -> None:
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert parse_retry_after("7", now) == 7.0
    assert parse_retry_after("Mon, 01 Jan 2024 12:00:09 GMT", now) == 9.0


def test_invalid_retry_after_uses_bounded_backoff() -> None:
    assert parse_retry_after("not-a-delay") is None
    assert parse_retry_after("nan") is None
    assert parse_retry_after("inf") is None
    assert backoff_seconds(1, "not-a-delay") == 1.0
    assert backoff_seconds(10**9, "not-a-delay") == 60.0
    assert backoff_seconds(1, "4") == 4.0
    assert backoff_seconds(1, "3600") == 3600.0
