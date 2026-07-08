"""Unit tests for the CircuitBreaker class.

Tests exponential backoff, state transitions, cooldown, and failure scenarios.
"""

import threading

import pytest

from engine.circuit_breaker import CircuitBreaker, CircuitState


class FakeClock:
    """Injectable fake clock for deterministic testing."""

    def __init__(self, start: float = 0.0):
        self._time = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


@pytest.fixture
def clock():
    return FakeClock(start=1000.0)


@pytest.fixture
def cb(clock):
    """Circuit breaker with default settings and injectable clock."""
    return CircuitBreaker(machine_id="M-01", clock=clock)


class TestInitialState:
    def test_starts_closed(self, cb):
        assert cb.get_state() == CircuitState.CLOSED

    def test_machine_status_running(self, cb):
        assert cb.machine_status == "running"

    def test_no_failures(self, cb):
        assert cb.consecutive_failures == 0

    def test_should_attempt_true_when_closed(self, cb):
        assert cb.should_attempt() is True

    def test_backoff_zero_when_closed(self, cb):
        assert cb.get_backoff_delay() == 0.0


class TestExponentialBackoff:
    """Requirement 15.1: Exponential backoff starting at 5s, doubling to 60s max."""

    def test_first_failure_backoff(self, cb, clock):
        cb.record_failure()
        # After 1 failure: initial backoff = 5s
        assert cb.get_backoff_delay() == 5.0

    def test_second_failure_backoff(self, cb, clock):
        cb.record_failure()
        clock.advance(5)
        cb.record_failure()
        # After 2 failures: 5 * 2^1 = 10s
        assert cb.get_backoff_delay() == 10.0

    def test_third_failure_backoff(self, cb, clock):
        cb.record_failure()
        clock.advance(5)
        cb.record_failure()
        clock.advance(10)
        cb.record_failure()
        # After 3 failures: 5 * 2^2 = 20s
        assert cb.get_backoff_delay() == 20.0

    def test_fourth_failure_backoff(self, cb, clock):
        cb.record_failure()
        clock.advance(5)
        cb.record_failure()
        clock.advance(10)
        cb.record_failure()
        clock.advance(20)
        cb.record_failure()
        # After 4 failures: 5 * 2^3 = 40s
        assert cb.get_backoff_delay() == 40.0

    def test_backoff_capped_at_max(self, cb, clock):
        # Record enough failures to exceed max
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        # After cap: should never exceed 60s
        # State is now OPEN after 5 failures, backoff = cooldown
        assert cb.get_backoff_delay() == 300.0  # open cooldown

    def test_backoff_caps_within_half_open(self, clock):
        """Custom breaker with higher threshold to test backoff cap."""
        cb = CircuitBreaker(
            machine_id="M-01",
            clock=clock,
            failures_before_open=10,  # High threshold to stay in HALF_OPEN
        )
        for i in range(7):
            cb.record_failure()
            clock.advance(60)
        # 5 * 2^6 = 320, capped at 60
        assert cb.get_backoff_delay() == 60.0


class TestShouldAttempt:
    """Test that should_attempt respects backoff timing."""

    def test_not_ready_before_backoff_elapsed(self, cb, clock):
        cb.record_failure()
        clock.advance(3)  # Only 3s elapsed, need 5s
        assert cb.should_attempt() is False

    def test_ready_after_backoff_elapsed(self, cb, clock):
        cb.record_failure()
        clock.advance(5)  # Exactly 5s elapsed
        assert cb.should_attempt() is True

    def test_not_ready_in_open_state(self, cb, clock):
        for _ in range(5):
            cb.record_failure()
            clock.advance(60)
        assert cb.get_state() == CircuitState.OPEN
        assert cb.should_attempt() is False

    def test_never_attempt_in_failed_state(self, cb, clock):
        cb.record_failure(recoverable=False, reason="corrupt model")
        assert cb.should_attempt() is False


class TestOpenState:
    """Requirement 15.2: Open state after 5 failures, 5 min cooldown."""

    def test_enters_open_after_5_failures(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        assert cb.get_state() == CircuitState.OPEN

    def test_stays_open_before_cooldown(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        # Advance less than cooldown (300s)
        clock.advance(200)
        assert cb.get_state() == CircuitState.OPEN

    def test_transitions_half_open_after_cooldown(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        assert cb.get_state() == CircuitState.OPEN
        # Advance past cooldown
        clock.advance(300)
        assert cb.get_state() == CircuitState.HALF_OPEN

    def test_failure_counter_resets_after_cooldown(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        clock.advance(300)
        # After cooldown, counter should reset
        _ = cb.get_state()  # triggers transition
        assert cb.consecutive_failures == 0


class TestMachineStatus:
    """Requirement 15.3: Report 'disconnected' during reconnection."""

    def test_disconnected_in_half_open(self, cb, clock):
        cb.record_failure()
        assert cb.machine_status == "disconnected"

    def test_disconnected_in_open(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        assert cb.machine_status == "disconnected"

    def test_running_when_closed(self, cb):
        assert cb.machine_status == "running"

    def test_failed_status(self, cb):
        cb.record_failure(recoverable=False, reason="bad config")
        assert cb.machine_status == "failed"


class TestSuccessfulReconnect:
    """Requirement 15.4: Resume normal operation on successful reconnect."""

    def test_success_resets_to_closed(self, cb, clock):
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.get_state() == CircuitState.CLOSED

    def test_success_resets_failure_counter(self, cb, clock):
        for i in range(3):
            cb.record_failure()
            clock.advance(10)
        cb.record_success()
        assert cb.consecutive_failures == 0

    def test_success_resets_open_cycle_count(self, cb, clock):
        # Go through one open cycle
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        clock.advance(300)  # Cooldown expires
        # Now in HALF_OPEN again, cycle count = 1
        assert cb.open_cycle_count == 1
        cb.record_success()
        assert cb.open_cycle_count == 0

    def test_machine_status_running_after_success(self, cb, clock):
        cb.record_failure()
        assert cb.machine_status == "disconnected"
        cb.record_success()
        assert cb.machine_status == "running"


class TestNonRecoverableErrors:
    """Requirement 15.5: Mark pipeline 'failed' on non-recoverable errors."""

    def test_invalid_config_fails_immediately(self, cb):
        cb.record_failure(recoverable=False, reason="invalid configuration")
        assert cb.get_state() == CircuitState.FAILED
        assert cb.failure_reason == "invalid configuration"

    def test_corrupt_model_fails_immediately(self, cb):
        cb.record_failure(recoverable=False, reason="corrupted model file")
        assert cb.get_state() == CircuitState.FAILED
        assert cb.failure_reason == "corrupted model file"

    def test_failed_state_is_terminal(self, cb):
        cb.record_failure(recoverable=False, reason="bad")
        # Further failures should not change state
        cb.record_failure()
        assert cb.get_state() == CircuitState.FAILED

    def test_failed_machine_status(self, cb):
        cb.record_failure(recoverable=False, reason="bad config")
        assert cb.machine_status == "failed"


class TestMaxOpenCycles:
    """After 3 consecutive open-state cycles without success, mark FAILED."""

    def test_fails_after_3_open_cycles(self, clock):
        cb = CircuitBreaker(machine_id="M-01", clock=clock)

        for cycle in range(3):
            # Each cycle: 5 failures → OPEN, then cooldown
            for i in range(5):
                cb.record_failure()
                clock.advance(60)
            if cycle < 2:
                # Cooldown expires, transition to HALF_OPEN for next cycle
                clock.advance(300)
                _ = cb.get_state()

        # After 3rd cycle's 5th failure → OPEN → exceeds max cycles → FAILED
        assert cb.get_state() == CircuitState.FAILED
        assert "3 consecutive open-state cycles" in cb.failure_reason

    def test_success_resets_cycle_count_mid_way(self, clock):
        cb = CircuitBreaker(machine_id="M-01", clock=clock)

        # Complete 2 open cycles
        for cycle in range(2):
            for i in range(5):
                cb.record_failure()
                clock.advance(60)
            clock.advance(300)
            _ = cb.get_state()

        # Success resets everything
        cb.record_success()
        assert cb.open_cycle_count == 0
        assert cb.get_state() == CircuitState.CLOSED


class TestReset:
    """Test manual reset functionality."""

    def test_reset_from_half_open(self, cb, clock):
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.get_state() == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    def test_reset_from_open(self, cb, clock):
        for i in range(5):
            cb.record_failure()
            clock.advance(60)
        cb.reset()
        assert cb.get_state() == CircuitState.CLOSED

    def test_reset_from_failed(self, cb):
        cb.record_failure(recoverable=False, reason="bad")
        cb.reset()
        assert cb.get_state() == CircuitState.CLOSED
        assert cb.failure_reason is None


class TestThreadSafety:
    """Verify thread-safe access to circuit breaker state."""

    def test_concurrent_failures(self, clock):
        cb = CircuitBreaker(
            machine_id="M-01",
            clock=clock,
            failures_before_open=100,  # High threshold to avoid OPEN
        )
        threads = []
        for _ in range(20):
            t = threading.Thread(target=cb.record_failure)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 20 failures should be recorded
        assert cb.consecutive_failures == 20

    def test_concurrent_success_and_failure(self, clock):
        cb = CircuitBreaker(machine_id="M-01", clock=clock)
        cb.record_failure()

        def do_success():
            cb.record_success()

        def do_failure():
            cb.record_failure()

        # Mix of successes and failures — should not crash
        threads = []
        for i in range(10):
            if i % 2 == 0:
                threads.append(threading.Thread(target=do_success))
            else:
                threads.append(threading.Thread(target=do_failure))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Final state should be consistent (either CLOSED or HALF_OPEN)
        state = cb.get_state()
        assert state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
