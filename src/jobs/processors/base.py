"""
Base processor class for job processing.

All job processors (TranscriptProcessor, SummaryProcessor, DistributionProcessor)
inherit from BaseProcessor and implement the process() method.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import logging

from src.core.database import DatabaseManager, Meeting, JobQueue
from src.core.config import ConfigManager
from src.core.exceptions import JobProcessingError


class BaseProcessor(ABC):
    """
    Abstract base class for job processors.

    Job processors handle specific types of jobs:
    - TranscriptProcessor: Fetches and parses meeting transcripts
    - SummaryProcessor: Generates AI summaries from transcripts
    - DistributionProcessor: Sends emails and posts to Teams chat

    Each processor must implement the process() method.
    """

    def __init__(self, db: DatabaseManager, config: ConfigManager):
        """
        Initialize processor.

        Args:
            db: Database manager instance
            config: Configuration manager instance
        """
        self.db = db
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def process(self, job: JobQueue) -> Dict[str, Any]:
        """
        Process a job.

        This method must be implemented by subclasses.

        Args:
            job: Job to process

        Returns:
            Dictionary with processing results to store in job.output_data

        Raises:
            JobProcessingError: If job processing fails
        """
        pass

    def _get_meeting(self, meeting_id: int) -> Meeting:
        """
        Get meeting by ID.

        Args:
            meeting_id: Meeting database ID

        Returns:
            Meeting object

        Raises:
            JobProcessingError: If meeting not found
        """
        meeting = self.db.get_meeting_by_id(meeting_id)

        if not meeting:
            raise JobProcessingError(f"Meeting {meeting_id} not found")

        return meeting

    def _update_meeting_status(self, meeting_id: int, status: str, **kwargs):
        """
        Update meeting status and metadata.

        Args:
            meeting_id: Meeting database ID
            status: New status
            **kwargs: Additional fields to update
        """
        session = self.db.get_session()
        try:
            meeting = session.query(Meeting).get(meeting_id)
            if meeting:
                meeting.status = status
                for key, value in kwargs.items():
                    if hasattr(meeting, key):
                        setattr(meeting, key, value)
                session.commit()
                self.logger.info(f"Updated meeting {meeting_id}: status={status}")
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to update meeting {meeting_id}: {e}")
            raise
        finally:
            session.close()

    def _log_progress(self, job: JobQueue, message: str, level: str = "info"):
        """
        Log processing progress.

        Args:
            job: Current job
            message: Log message
            level: Log level ('debug', 'info', 'warning', 'error')
        """
        log_message = f"[Job {job.id}] {message}"

        if level == "debug":
            self.logger.debug(log_message)
        elif level == "info":
            self.logger.info(log_message)
        elif level == "warning":
            self.logger.warning(log_message)
        elif level == "error":
            self.logger.error(log_message)

    def _validate_job_input(self, job: JobQueue, required_fields: list) -> bool:
        """
        Validate that job has required input data.

        Args:
            job: Job to validate
            required_fields: List of required field names in job.input_data

        Returns:
            True if valid, False otherwise

        Raises:
            JobProcessingError: If validation fails
        """
        if not job.input_data:
            raise JobProcessingError(f"Job {job.id} has no input data")

        missing_fields = [field for field in required_fields if field not in job.input_data]

        if missing_fields:
            raise JobProcessingError(f"Job {job.id} missing required fields: {missing_fields}")

        return True

    def _create_output_data(
        self, success: bool, message: str, data: Optional[Dict[str, Any]] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Create standardized output data dictionary.

        Args:
            success: Whether processing succeeded
            message: Result message
            data: Additional data to include
            **kwargs: Additional key-value pairs

        Returns:
            Output data dictionary
        """
        output = {
            "success": success,
            "message": message,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "processor": self.__class__.__name__,
        }

        if data:
            output["data"] = data

        # Add any additional kwargs
        output.update(kwargs)

        return output

    async def execute_with_timeout(self, job: JobQueue, timeout_seconds: int) -> Dict[str, Any]:
        """
        Execute job processing with timeout.

        Wraps the process() method with timeout handling.

        Args:
            job: Job to process
            timeout_seconds: Timeout in seconds

        Returns:
            Processing result

        Raises:
            asyncio.TimeoutError: If processing exceeds timeout
            JobProcessingError: If processing fails
        """
        import asyncio

        try:
            # Execute with timeout
            result = await asyncio.wait_for(self.process(job), timeout=timeout_seconds)

            self._log_progress(job, f"Completed in {timeout_seconds}s")
            return result

        except asyncio.TimeoutError:
            self._log_progress(job, f"Timeout after {timeout_seconds}s", level="error")
            raise JobProcessingError(f"Job processing timeout after {timeout_seconds}s")

    def handle_error(self, job: JobQueue, error: Exception) -> Dict[str, Any]:
        """
        Handle processing error and create error output.

        Args:
            job: Job that failed
            error: Exception that occurred

        Returns:
            Error output data
        """
        import traceback

        error_message = str(error)
        error_stack = traceback.format_exc()

        self._log_progress(job, f"Error: {error_message}", level="error")

        return self._create_output_data(
            success=False, message=error_message, error_type=type(error).__name__, error_stack=error_stack
        )


class ProcessorRegistry:
    """
    Registry for job processors.

    Maps job types to processor classes.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._processors = {}

    def register(self, job_type: str, processor_class):
        """
        Register a processor for a job type.

        Args:
            job_type: Job type name (e.g., 'fetch_transcript')
            processor_class: Processor class (must inherit from BaseProcessor)
        """
        if not issubclass(processor_class, BaseProcessor):
            raise ValueError(f"{processor_class} must inherit from BaseProcessor")

        self._processors[job_type] = processor_class
        logging.info(f"Registered processor for job type: {job_type}")

    def get_processor(self, job_type: str, db: DatabaseManager, config: ConfigManager) -> Optional[BaseProcessor]:
        """
        Get processor instance for job type.

        Args:
            job_type: Job type name
            db: Database manager
            config: Configuration manager

        Returns:
            Processor instance or None if not registered
        """
        processor_class = self._processors.get(job_type)

        if not processor_class:
            return None

        return processor_class(db, config)

    def list_registered_types(self) -> list:
        """Get list of registered job types."""
        return list(self._processors.keys())


# Global processor registry
_registry = ProcessorRegistry()


def get_processor_registry() -> ProcessorRegistry:
    """Get global processor registry."""
    return _registry


def register_processor(job_type: str):
    """
    Decorator to register a processor class.

    Usage:
        @register_processor('fetch_transcript')
        class TranscriptProcessor(BaseProcessor):
            async def process(self, job):
                ...
    """

    def decorator(processor_class):
        _registry.register(job_type, processor_class)
        return processor_class

    return decorator
