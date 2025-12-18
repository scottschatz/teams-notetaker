"""
Job Worker

Asynchronous job worker that processes jobs from the queue using asyncio.
Runs multiple jobs concurrently with timeout enforcement and heartbeat updates.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional
from datetime import datetime, timedelta
import uuid

from ..core.database import DatabaseManager, JobQueue
from ..core.config import get_config
from ..jobs.queue import JobQueueManager
from ..jobs.processors.base import get_processor_registry
from ..core.exceptions import JobProcessingError
from ..graph.client import GraphAPIClient
from ..inbox import InboxMonitor

# Import processors to register them
from ..jobs.processors import transcript, summary, distribution


logger = logging.getLogger(__name__)


class JobWorker:
    """
    Asynchronous job worker using asyncio for concurrent processing.

    Features:
    - Concurrent job processing (5-10 jobs at once)
    - Graceful shutdown on SIGTERM/SIGINT
    - Heartbeat updates every 30 seconds
    - Job timeout enforcement
    - Automatic retry on failure
    - Processor registry integration

    Usage:
        config = get_config()
        worker = JobWorker(config, max_concurrent=5)
        await worker.start()  # Run until stopped

        # Or run in background:
        worker.run()  # Blocks and handles signals
    """

    def __init__(
        self,
        config,
        db: Optional[DatabaseManager] = None,
        max_concurrent: int = 5,
        job_timeout: int = 600
    ):
        """
        Initialize job worker.

        Args:
            config: AppConfig instance
            db: DatabaseManager instance (created if None)
            max_concurrent: Maximum concurrent jobs (default 5)
            job_timeout: Job timeout in seconds (default 600 = 10 minutes)
        """
        self.config = config
        self.db = db or DatabaseManager(config.database.connection_string)
        self.max_concurrent = max_concurrent
        self.job_timeout = job_timeout

        # Worker state
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.running = False
        self.active_jobs = {}  # {job_id: task}

        # Job queue manager
        self.queue = JobQueueManager(self.db)

        # Processor registry
        self.registry = get_processor_registry()

        # Initialize inbox monitor (for email-based preferences)
        self.inbox_monitor = None
        inbox_email = getattr(config.app, 'email_from', None)
        if inbox_email:
            try:
                graph_client = GraphAPIClient(config.graph_api)
                self.inbox_monitor = InboxMonitor(
                    db=self.db,
                    graph_client=graph_client,
                    mailbox_email=inbox_email,
                    lookback_minutes=60
                )
                logger.info(f"Inbox monitor initialized for {inbox_email}")
            except Exception as e:
                logger.warning(f"Could not initialize inbox monitor: {e}")

        logger.info(
            f"JobWorker initialized (id: {self.worker_id}, "
            f"max_concurrent: {max_concurrent}, timeout: {job_timeout}s, "
            f"inbox_monitoring: {'enabled' if self.inbox_monitor else 'disabled'})"
        )

    async def start(self):
        """
        Start processing jobs (runs until stopped).

        This is the main worker loop. It continuously:
        1. Claims jobs from the queue
        2. Processes them concurrently (up to max_concurrent)
        3. Updates heartbeats
        4. Handles errors and retries
        5. Cleans up orphaned jobs periodically
        """
        self.running = True
        cleanup_counter = 0
        inbox_counter = 0

        logger.info(f"Worker {self.worker_id} started")

        try:
            while self.running:
                # Process jobs concurrently
                await self._process_batch()

                # Periodic cleanup of orphaned jobs (every 60 seconds)
                cleanup_counter += 1
                if cleanup_counter >= 60:
                    try:
                        orphaned_count = self.queue.cleanup_orphaned_jobs()
                        if orphaned_count > 0:
                            logger.info(f"Cleaned up {orphaned_count} orphaned jobs")
                    except Exception as e:
                        logger.error(f"Orphaned job cleanup failed: {e}")
                    cleanup_counter = 0

                # Periodic inbox check for email commands (every 5 minutes = 300 seconds)
                inbox_counter += 1
                if inbox_counter >= 300 and self.inbox_monitor:
                    try:
                        stats = await self.inbox_monitor.check_inbox()
                        if stats.get("processed", 0) > 0:
                            logger.info(
                                f"Inbox check: {stats['processed']} processed, "
                                f"{stats['subscribed']} subscribed, "
                                f"{stats['unsubscribed']} unsubscribed"
                            )
                    except Exception as e:
                        logger.error(f"Inbox check failed: {e}")
                    inbox_counter = 0

                # Sleep briefly before next iteration
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, stopping...")
        except Exception as e:
            logger.error(f"Worker crashed: {e}", exc_info=True)
        finally:
            await self.stop()

    async def stop(self):
        """
        Gracefully stop the worker.

        Waits for active jobs to complete (up to 30 seconds).
        """
        logger.info(f"Stopping worker {self.worker_id}...")

        self.running = False

        # Wait for active jobs to complete
        if self.active_jobs:
            logger.info(f"Waiting for {len(self.active_jobs)} active job(s) to complete...")

            # Give jobs up to 30 seconds to finish
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.active_jobs.values(), return_exceptions=True),
                    timeout=30
                )
            except asyncio.TimeoutError:
                logger.warning("Some jobs did not complete within 30 seconds")

        logger.info(f"Worker {self.worker_id} stopped")

    async def _process_batch(self):
        """
        Process a batch of jobs concurrently.

        Claims and processes jobs up to max_concurrent limit.
        """
        # Calculate how many more jobs we can process
        available_slots = self.max_concurrent - len(self.active_jobs)

        if available_slots <= 0:
            # All slots busy, just wait
            await asyncio.sleep(0.1)
            return

        # Claim jobs to fill available slots
        for _ in range(available_slots):
            job = self.queue.claim_next_job(self.worker_id, timeout_seconds=self.job_timeout)

            if job is None:
                # No more jobs available
                break

            # Start processing job in background
            task = asyncio.create_task(self._process_job_with_timeout(job))
            self.active_jobs[job.id] = task

            # Set up cleanup when task completes
            task.add_done_callback(lambda t, jid=job.id: self._cleanup_job(jid))

    async def _process_job_with_timeout(self, job: JobQueue):
        """
        Process a single job with timeout enforcement.

        Args:
            job: JobQueue object to process
        """
        logger.info(
            f"Processing job {job.id} (type: {job.job_type}, "
            f"meeting: {job.meeting_id}, priority: {job.priority})"
        )

        try:
            # Start heartbeat updater
            heartbeat_task = asyncio.create_task(self._update_heartbeat(job.id))

            # Process job with timeout
            try:
                result = await asyncio.wait_for(
                    self._process_job(job),
                    timeout=self.job_timeout
                )

                # Mark as completed
                self.queue.mark_completed(job.id, result)

                logger.info(f"✓ Job {job.id} completed successfully")

            except asyncio.TimeoutError:
                logger.error(f"✗ Job {job.id} timed out after {self.job_timeout}s")

                self.queue.mark_failed(
                    job.id,
                    f"Job timed out after {self.job_timeout} seconds",
                    should_retry=True
                )

            except Exception as e:
                logger.error(f"✗ Job {job.id} failed: {e}", exc_info=True)

                self.queue.mark_failed(
                    job.id,
                    str(e),
                    should_retry=True,
                    output_data={"error": str(e), "error_type": type(e).__name__}
                )

            finally:
                # Stop heartbeat updater
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            logger.error(f"Unexpected error processing job {job.id}: {e}", exc_info=True)

    async def _process_job(self, job: JobQueue) -> dict:
        """
        Process a job using the appropriate processor.

        Args:
            job: JobQueue object

        Returns:
            Job output data dictionary

        Raises:
            JobProcessingError: If processing fails
        """
        # Get processor for job type
        processor = self.registry.get_processor(
            job.job_type,
            self.db,
            self.config
        )

        if not processor:
            raise JobProcessingError(f"No processor found for job type: {job.job_type}")

        # Execute processor
        try:
            output_data = await processor.process(job)
            return output_data

        except Exception as e:
            logger.error(f"Processor failed for job {job.id}: {e}", exc_info=True)
            raise JobProcessingError(f"Processor failed: {e}")

    async def _update_heartbeat(self, job_id: int):
        """
        Periodically update job heartbeat.

        Args:
            job_id: Job ID
        """
        try:
            while True:
                await asyncio.sleep(30)  # Update every 30 seconds

                success = self.queue.update_heartbeat(job_id)

                if success:
                    logger.debug(f"Heartbeat updated for job {job_id}")
                else:
                    logger.warning(f"Failed to update heartbeat for job {job_id}")
                    break

        except asyncio.CancelledError:
            # Task cancelled (job completed/failed)
            pass

    def _cleanup_job(self, job_id: int):
        """
        Cleanup after job completes.

        Args:
            job_id: Job ID
        """
        if job_id in self.active_jobs:
            del self.active_jobs[job_id]

    def run(self):
        """
        Run worker in the main thread (blocks until stopped).

        Sets up signal handlers for graceful shutdown.
        """
        # Set up signal handlers (only works in main thread)
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            asyncio.create_task(self.stop())

        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
        except ValueError:
            # Signal handlers only work in main thread, skip if in background thread
            logger.debug("Running in background thread, skipping signal handlers")

        # Run event loop
        try:
            asyncio.run(self.start())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Worker failed: {e}", exc_info=True)
            sys.exit(1)


def main():
    """
    Main entry point for running worker as a standalone process.
    """
    from ..core.logging_config import setup_logging

    # Set up logging
    setup_logging(log_file="logs/worker.log")

    # Load config
    config = get_config()

    # Create and run worker
    worker = JobWorker(
        config=config,
        max_concurrent=config.app.max_concurrent_jobs,
        job_timeout=config.app.job_timeout_minutes * 60
    )

    logger.info("Starting job worker...")
    worker.run()


if __name__ == "__main__":
    main()
