"""
Retry logic and exponential backoff for job processing.

Implements exponential backoff strategy for failed jobs with configurable
max retries and base delay.
"""

from datetime import datetime, timedelta
import logging
from typing import Optional
import random


def calculate_next_retry(
    retry_count: int, base_delay_seconds: int = 60, max_delay_seconds: int = 3600, jitter: bool = True
) -> datetime:
    """
    Calculate next retry time using exponential backoff.

    Strategy: delay = base_delay * (2 ^ retry_count)

    Examples with base_delay=60:
        - retry 0: 1 minute
        - retry 1: 2 minutes
        - retry 2: 4 minutes
        - retry 3: 8 minutes (capped at max_delay)

    Args:
        retry_count: Number of retries so far (0-indexed)
        base_delay_seconds: Base delay in seconds (default: 60)
        max_delay_seconds: Maximum delay in seconds (default: 3600 = 1 hour)
        jitter: Add random jitter to prevent thundering herd (default: True)

    Returns:
        Datetime for next retry attempt
    """
    # Calculate exponential delay
    delay = base_delay_seconds * (2 ** retry_count)

    # Cap at maximum
    delay = min(delay, max_delay_seconds)

    # Add jitter (random ±25% variation)
    if jitter:
        jitter_amount = delay * 0.25
        delay = delay + random.uniform(-jitter_amount, jitter_amount)
        delay = max(base_delay_seconds, delay)  # Don't go below base delay

    # Calculate next retry time
    # Use local time since PostgreSQL is configured with America/New_York timezone
    next_retry = datetime.now() + timedelta(seconds=delay)

    return next_retry


def should_retry(retry_count: int, max_retries: int = 3, error: Optional[Exception] = None) -> bool:
    """
    Determine if a job should be retried based on retry count and error type.

    Args:
        retry_count: Current retry count
        max_retries: Maximum number of retries allowed (default: 3)
        error: The exception that occurred (optional)

    Returns:
        True if job should be retried, False otherwise
    """
    # Check retry limit
    if retry_count >= max_retries:
        return False

    # If error is provided, check if it's retryable
    if error:
        # List of non-retryable error types
        non_retryable_errors = (
            ValueError,  # Invalid input data
            KeyError,  # Missing required data
            TypeError,  # Type mismatch
        )

        if isinstance(error, non_retryable_errors):
            return False

    return True


def get_retry_strategy(job_type: str) -> dict:
    """
    Get retry strategy configuration for different job types.

    Different job types may have different retry strategies based on
    their likelihood of transient failures.

    Args:
        job_type: Type of job ('fetch_transcript', 'generate_summary', 'distribute')

    Returns:
        Dictionary with retry configuration:
        {
            'max_retries': int,
            'base_delay_seconds': int,
            'max_delay_seconds': int
        }
    """
    strategies = {
        "fetch_transcript": {
            "max_retries": 3,
            "base_delay_seconds": 60,  # 1 minute
            "max_delay_seconds": 600,  # 10 minutes
            "reason": "Transcript may not be immediately available after meeting",
        },
        "generate_summary": {
            "max_retries": 3,
            "base_delay_seconds": 30,  # 30 seconds
            "max_delay_seconds": 300,  # 5 minutes
            "reason": "Claude API may have transient issues or rate limits",
        },
        "distribute": {
            "max_retries": 5,
            "base_delay_seconds": 120,  # 2 minutes
            "max_delay_seconds": 1800,  # 30 minutes
            "reason": "Email/chat distribution is critical and may have transient failures",
        },
    }

    # Default strategy
    default = {"max_retries": 3, "base_delay_seconds": 60, "max_delay_seconds": 600, "reason": "Default strategy"}

    return strategies.get(job_type, default)


class RetryContext:
    """
    Context manager for retry logic with automatic backoff.

    Example:
        retry_ctx = RetryContext(job_type='fetch_transcript', max_retries=3)

        for attempt in retry_ctx:
            try:
                result = do_risky_operation()
                retry_ctx.success()
                break
            except Exception as e:
                retry_ctx.failure(e)
                if not retry_ctx.should_continue():
                    raise
    """

    def __init__(
        self, job_type: str, max_retries: Optional[int] = None, base_delay_seconds: Optional[int] = None, logger=None
    ):
        """
        Initialize retry context.

        Args:
            job_type: Type of job being retried
            max_retries: Override max retries (uses strategy default if None)
            base_delay_seconds: Override base delay (uses strategy default if None)
            logger: Logger instance (creates new if None)
        """
        self.job_type = job_type
        self.logger = logger or logging.getLogger(__name__)

        # Get retry strategy
        strategy = get_retry_strategy(job_type)
        self.max_retries = max_retries or strategy["max_retries"]
        self.base_delay_seconds = base_delay_seconds or strategy["base_delay_seconds"]
        self.max_delay_seconds = strategy.get("max_delay_seconds", 3600)

        # State
        self.attempt = 0
        self.last_error = None
        self.succeeded = False

    def __iter__(self):
        """Make RetryContext iterable."""
        return self

    def __next__(self):
        """Get next retry attempt."""
        if self.attempt >= self.max_retries or self.succeeded:
            raise StopIteration

        self.attempt += 1
        self.logger.debug(f"Retry attempt {self.attempt}/{self.max_retries} for {self.job_type}")
        return self.attempt

    def success(self):
        """Mark operation as successful."""
        self.succeeded = True
        self.logger.info(f"Operation succeeded on attempt {self.attempt}")

    def failure(self, error: Exception):
        """
        Mark operation as failed.

        Args:
            error: The exception that occurred
        """
        self.last_error = error
        self.logger.warning(f"Attempt {self.attempt} failed: {error}")

    def should_continue(self) -> bool:
        """Check if retry should continue."""
        if self.succeeded:
            return False

        return should_retry(self.attempt, self.max_retries, self.last_error)

    def get_next_retry_time(self) -> datetime:
        """Get next retry time with exponential backoff."""
        return calculate_next_retry(self.attempt, self.base_delay_seconds, self.max_delay_seconds)


def format_retry_info(retry_count: int, max_retries: int, next_retry_at: Optional[datetime] = None) -> str:
    """
    Format retry information for logging/display.

    Args:
        retry_count: Current retry count
        max_retries: Maximum retries allowed
        next_retry_at: Next retry time (optional)

    Returns:
        Formatted string like "Retry 2/3 (next at 2025-12-10 14:30:00)"
    """
    info = f"Retry {retry_count}/{max_retries}"

    if next_retry_at:
        info += f" (next at {next_retry_at.strftime('%Y-%m-%d %H:%M:%S')})"

    return info
