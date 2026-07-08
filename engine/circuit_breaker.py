"""Circuit breaker for RTSP pipeline resilience.

Implements the circuit breaker pattern to manage RTSP reconnection logic
with exponential backoff. The circuit breaker decides whether to attempt
reconnection and tracks state — it does NOT perform the reconnection itself.

States:
- CLOSED: Normal operation, pipeline is running fine.
- HALF_OPEN: Attempting reconnection after cooldown.
- OPEN: Stopped reconnection attempts, waiting for cooldown to expire.
- FAILED: Non-recoverable failure, pipeline will not attempt reconnection.

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5
"""

import threading
import time as time_module
from enum import Enum
from typing import Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    """Possible states for the circuit breaker."""

    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"
    FAILED = "failed"


class CircuitBreaker:
    """Circuit breaker for managing RTSP reconnection with exponential backoff.

    This class tracks connection health and decides whether reconnection
    should be attempted. It does NOT perform the actual reconnection — the
    caller (PipelineOrchestrator) reads the state and acts accordingly.

    Usage:
        cb = CircuitBreaker(machine_id="M-01")

        # On successful frame capture:
        cb.record_success()

        # On connection loss:
        cb.record_failure()

        # Before attempting reconnect:
        if cb.should_attempt():
            success = try_reconnect()
            if success:
                cb.record_success()
            else:
                cb.record_failure()
    """

    # Defaults per spec requirements
    INITIAL_BACKOFF_SECONDS = 5.0
    MAX_BACKOFF_SECONDS = 60.0
    FAILURES_BEFORE_OPEN = 5
    OPEN_COOLDOWN_SECONDS = 300.0  # 5 minutes
    MAX_OPEN_CYCLES_BEFORE_FAILED = 3

    def __init__(
        self,
        machine_id: str,
        clock: Callable[[], float] = None,
        initial_backoff: float = None,
        max_backoff: float = None,
        failures_before_open: int = None,
        open_cooldown: float = None,
        max_open_cycles: int = None,
    ):
        """Initialize the circuit breaker.

        Args:
            machine_id: Identifier for the machine this breaker monitors.
            clock: Injectable time function returning seconds since epoch.
                   Defaults to time.time() for production use.
            initial_backoff: Initial backoff delay in seconds (default 5).
            max_backoff: Maximum backoff delay in seconds (default 60).
            failures_before_open: Consecutive failures before entering OPEN
                                  state (default 5).
            open_cooldown: Duration in seconds to wait in OPEN state before
                           attempting again (default 300 = 5 minutes).
            max_open_cycles: Number of consecutive open-state cycles without
                             success before marking as FAILED (default 3).
        """
        self._machine_id = machine_id
        self._clock = clock or time_module.time
        self._lock = threading.Lock()

        # Configuration
        self._initial_backoff = (
            initial_backoff if initial_backoff is not None else self.INITIAL_BACKOFF_SECONDS
        )
        self._max_backoff = (
            max_backoff if max_backoff is not None else self.MAX_BACKOFF_SECONDS
        )
        self._failures_before_open = (
            failures_before_open if failures_before_open is not None else self.FAILURES_BEFORE_OPEN
        )
        self._open_cooldown = (
            open_cooldown if open_cooldown is not None else self.OPEN_COOLDOWN_SECONDS
        )
        self._max_open_cycles = (
            max_open_cycles if max_open_cycles is not None else self.MAX_OPEN_CYCLES_BEFORE_FAILED
        )

        # State
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._open_cycle_count: int = 0
        self._last_failure_time: Optional[float] = None
        self._open_entered_time: Optional[float] = None
        self._failure_reason: Optional[str] = None

    @property
    def machine_id(self) -> str:
        """The machine ID this circuit breaker is monitoring."""
        return self._machine_id

    @property
    def state(self) -> CircuitState:
        """Current circuit breaker state (thread-safe read)."""
        with self._lock:
            return self._state

    @property
    def machine_status(self) -> str:
        """Human-readable machine status for API/WebSocket reporting.

        Returns:
            "running" when CLOSED (normal operation),
            "disconnected" when HALF_OPEN or OPEN (reconnecting),
            "failed" when FAILED (non-recoverable).
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return "running"
            elif self._state == CircuitState.FAILED:
                return "failed"
            else:
                return "disconnected"

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failures recorded."""
        with self._lock:
            return self._consecutive_failures

    @property
    def open_cycle_count(self) -> int:
        """Number of completed open-state cycles without success."""
        with self._lock:
            return self._open_cycle_count

    @property
    def failure_reason(self) -> Optional[str]:
        """Reason for terminal failure, if in FAILED state."""
        with self._lock:
            return self._failure_reason

    def get_state(self) -> CircuitState:
        """Get the current circuit breaker state.

        This method also handles automatic state transitions:
        - OPEN → HALF_OPEN when cooldown has elapsed.

        Returns:
            Current CircuitState after evaluating time-based transitions.
        """
        with self._lock:
            self._evaluate_state_transition()
            return self._state

    def get_backoff_delay(self) -> float:
        """Compute the current backoff delay based on consecutive failures.

        Uses exponential backoff: initial_backoff * 2^(failures - 1),
        capped at max_backoff.

        Returns:
            Delay in seconds before next reconnection attempt should occur.
            Returns 0.0 if in CLOSED state (no backoff needed).
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return 0.0
            if self._state == CircuitState.OPEN:
                return self._open_cooldown
            # HALF_OPEN: exponential backoff based on failures
            if self._consecutive_failures == 0:
                return self._initial_backoff
            exponent = min(self._consecutive_failures - 1, 10)  # Prevent overflow
            delay = self._initial_backoff * (2 ** exponent)
            return min(delay, self._max_backoff)

    def should_attempt(self) -> bool:
        """Determine whether a reconnection attempt should be made now.

        Considers current state and whether enough time has elapsed since
        the last failure (respecting backoff delay).

        Returns:
            True if a reconnection attempt is appropriate, False otherwise.
        """
        with self._lock:
            self._evaluate_state_transition()

            if self._state == CircuitState.CLOSED:
                # Normal operation — no reconnection needed
                return True

            if self._state == CircuitState.FAILED:
                # Non-recoverable — never attempt
                return False

            if self._state == CircuitState.OPEN:
                # Still in cooldown — don't attempt
                return False

            if self._state == CircuitState.HALF_OPEN:
                # Check if enough backoff time has elapsed
                if self._last_failure_time is None:
                    return True
                elapsed = self._clock() - self._last_failure_time
                delay = self._compute_backoff_delay()
                return elapsed >= delay

        return False

    def record_success(self) -> None:
        """Record a successful operation (connection established or frame read).

        Resets the circuit breaker to CLOSED state and clears all failure
        counters, including the open cycle count.
        """
        with self._lock:
            previous_state = self._state
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._open_cycle_count = 0
            self._last_failure_time = None
            self._open_entered_time = None
            self._failure_reason = None

            if previous_state != CircuitState.CLOSED:
                logger.info(
                    "Circuit breaker reset to CLOSED for machine %s "
                    "(was %s)",
                    self._machine_id,
                    previous_state.value,
                )

    def record_failure(self, recoverable: bool = True, reason: str = None) -> None:
        """Record a failed operation.

        Args:
            recoverable: Whether this failure is potentially recoverable.
                         Set to False for invalid config, corrupt model, etc.
            reason: Optional description of the failure cause.
        """
        with self._lock:
            if self._state == CircuitState.FAILED:
                # Already in terminal state, nothing to do
                return

            if not recoverable:
                # Non-recoverable error: go directly to FAILED
                self._state = CircuitState.FAILED
                self._failure_reason = reason or "non-recoverable error"
                logger.error(
                    "Circuit breaker FAILED for machine %s: %s",
                    self._machine_id,
                    self._failure_reason,
                )
                return

            self._consecutive_failures += 1
            self._last_failure_time = self._clock()

            if self._state == CircuitState.CLOSED:
                # First failure moves to HALF_OPEN (reconnecting)
                self._state = CircuitState.HALF_OPEN
                logger.warning(
                    "Circuit breaker HALF_OPEN for machine %s "
                    "(failure %d/%d)",
                    self._machine_id,
                    self._consecutive_failures,
                    self._failures_before_open,
                )

            elif self._state == CircuitState.HALF_OPEN:
                if self._consecutive_failures >= self._failures_before_open:
                    # Threshold reached — enter OPEN state
                    self._state = CircuitState.OPEN
                    self._open_entered_time = self._clock()
                    self._open_cycle_count += 1
                    logger.warning(
                        "Circuit breaker OPEN for machine %s "
                        "(cycle %d/%d, cooldown %ds)",
                        self._machine_id,
                        self._open_cycle_count,
                        self._max_open_cycles,
                        self._open_cooldown,
                    )

                    # Check if max open cycles exceeded
                    if self._open_cycle_count >= self._max_open_cycles:
                        self._state = CircuitState.FAILED
                        self._failure_reason = (
                            f"exceeded {self._max_open_cycles} consecutive "
                            f"open-state cycles without successful reconnect"
                        )
                        logger.error(
                            "Circuit breaker FAILED for machine %s: %s",
                            self._machine_id,
                            self._failure_reason,
                        )
                else:
                    logger.warning(
                        "Circuit breaker reconnect failed for machine %s "
                        "(failure %d/%d)",
                        self._machine_id,
                        self._consecutive_failures,
                        self._failures_before_open,
                    )

    def reset(self) -> None:
        """Manually reset the circuit breaker to initial CLOSED state.

        Useful when an operator manually intervenes or configuration
        is fixed after a FAILED state.
        """
        with self._lock:
            previous_state = self._state
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._open_cycle_count = 0
            self._last_failure_time = None
            self._open_entered_time = None
            self._failure_reason = None

            logger.info(
                "Circuit breaker manually reset for machine %s (was %s)",
                self._machine_id,
                previous_state.value,
            )

    def _evaluate_state_transition(self) -> None:
        """Check for time-based state transitions (must hold lock).

        Transitions OPEN → HALF_OPEN when cooldown has elapsed,
        resetting the consecutive failure counter for a fresh attempt cycle.
        """
        if self._state == CircuitState.OPEN and self._open_entered_time is not None:
            elapsed = self._clock() - self._open_entered_time
            if elapsed >= self._open_cooldown:
                # Cooldown expired — allow a new attempt cycle
                self._state = CircuitState.HALF_OPEN
                self._consecutive_failures = 0
                self._last_failure_time = None
                self._open_entered_time = None
                logger.info(
                    "Circuit breaker cooldown expired for machine %s, "
                    "transitioning to HALF_OPEN (cycle %d)",
                    self._machine_id,
                    self._open_cycle_count,
                )

    def _compute_backoff_delay(self) -> float:
        """Compute backoff delay (must hold lock).

        Returns:
            Exponential backoff delay in seconds.
        """
        if self._consecutive_failures == 0:
            return self._initial_backoff
        exponent = min(self._consecutive_failures - 1, 10)
        delay = self._initial_backoff * (2 ** exponent)
        return min(delay, self._max_backoff)
