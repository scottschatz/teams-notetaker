"""
Job Queue Manager

Manages the asynchronous job processing queue using PostgreSQL for atomic job claiming.
Supports job dependencies, priorities, retries, and monitoring.
"""

import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text, and_, or_

from ..core.database import (
    DatabaseManager, JobQueue, Meeting
)
from ..jobs.retry import calculate_next_retry, get_retry_strategy
from ..core.exceptions import JobQueueError


logger = logging.getLogger(__name__)


class JobQueueManager:
    """
    Manages job queue operations with atomic claiming and dependency support.

    Features:
    - Atomic job claiming using FOR UPDATE SKIP LOCKED
    - Job dependencies (job chains)
    - Priority-based scheduling
    - Exponential backoff retry
    - Job timeout detection
    - Queue statistics

    Usage:
        db = DatabaseManager(config.database)
        queue = JobQueueManager(db)

        # Enqueue meeting for processing
        queue.enqueue_meeting_jobs(meeting_id=123, priority=5)

        # Worker claims next job
        job = queue.claim_next_job(worker_id="worker-1")
        if job:
            # Process job...
            queue.mark_completed(job.id, output_data={...})
    """

    def __init__(self, db: DatabaseManager):
        """
        Initialize job queue manager.

        Args:
            db: DatabaseManager instance
        """
        self.db = db

    def enqueue_meeting_jobs(self, meeting_id: int, priority: int = 5) -> List[int]:
        """
        Enqueue 3-job chain for meeting processing.

        Creates job chain:
        1. fetch_transcript (no dependencies)
        2. generate_summary (depends on #1)
        3. distribute (depends on #2)

        Args:
            meeting_id: Meeting ID to process
            priority: Job priority (1-10, higher = more important)

        Returns:
            List of created job IDs [fetch_job_id, summary_job_id, distribute_job_id]

        Raises:
            JobQueueError: If meeting not found or jobs already exist
        """
        with self.db.get_session() as session:
            # Verify meeting exists
            meeting = session.query(Meeting).filter_by(id=meeting_id).first()
            if not meeting:
                raise JobQueueError(f"Meeting {meeting_id} not found")

            # Check if jobs already exist for this meeting
            existing_jobs = session.query(JobQueue).filter(
                JobQueue.meeting_id == meeting_id,
                JobQueue.status.in_(["pending", "running", "retrying"])
            ).count()

            if existing_jobs > 0:
                logger.warning(f"Jobs already exist for meeting {meeting_id}, skipping enqueue")
                return []

            logger.info(f"Enqueueing 3-job chain for meeting {meeting_id} (priority: {priority})")

            # Job 1: Fetch transcript (no dependencies)
            job1 = JobQueue(
                job_type='fetch_transcript',
                meeting_id=meeting_id,
                priority=priority,
                status="pending",
                input_data={"meeting_id": meeting_id},
                max_retries=3
            )
            session.add(job1)
            session.flush()  # Get job1.id

            # Job 2: Generate summary (depends on job1)
            job2 = JobQueue(
                job_type='generate_summary',
                meeting_id=meeting_id,
                priority=priority,
                status="pending",
                input_data={"meeting_id": meeting_id},
                depends_on_job_id=job1.id,
                max_retries=3
            )
            session.add(job2)
            session.flush()  # Get job2.id

            # Job 3: Distribute (depends on job2)
            job3 = JobQueue(
                job_type='distribute',
                meeting_id=meeting_id,
                priority=priority,
                status="pending",
                input_data={"meeting_id": meeting_id},
                depends_on_job_id=job2.id,
                max_retries=5  # More retries for distribution (network issues)
            )
            session.add(job3)
            session.commit()

            # Update meeting status
            meeting.status = "queued"
            session.commit()

            job_ids = [job1.id, job2.id, job3.id]
            logger.info(f"✓ Created 3-job chain for meeting {meeting_id}: {job_ids}")

            return job_ids

    def claim_next_job(self, worker_id: str, timeout_seconds: int = 600) -> Optional[JobQueue]:
        """
        Atomically claim next available job using FOR UPDATE SKIP LOCKED.

        Selection criteria (in order):
        1. Status: pending or retrying
        2. Retry time: next_retry_at is NULL or in the past
        3. Dependencies: parent job (depends_on_job_id) is completed
        4. Priority: higher priority first (DESC)
        5. Age: older jobs first (created_at ASC)

        Args:
            worker_id: Worker identifier (for tracking)
            timeout_seconds: Job timeout in seconds (default 10 minutes)

        Returns:
            JobQueue object if claimed, None if no jobs available
        """
        with self.db.get_session() as session:
            # Raw SQL for atomic claiming with FOR UPDATE SKIP LOCKED
            # This ensures only one worker can claim each job
            query = text("""
                UPDATE job_queue
                SET
                    status = :running_status,
                    worker_id = :worker_id,
                    started_at = :now,
                    heartbeat_at = :now
                WHERE id = (
                    SELECT jq.id
                    FROM job_queue jq
                    LEFT JOIN job_queue parent ON jq.depends_on_job_id = parent.id
                    WHERE
                        -- Job is ready to run
                        jq.status IN (:pending_status, :retrying_status)

                        -- Retry time has passed (or not set)
                        AND (jq.next_retry_at IS NULL OR jq.next_retry_at <= :now)

                        -- Dependencies are met (parent completed or no parent)
                        AND (jq.depends_on_job_id IS NULL OR parent.status = :completed_status)

                    ORDER BY
                        jq.priority DESC,
                        jq.created_at ASC
                    LIMIT 1
                    FOR UPDATE OF jq SKIP LOCKED
                )
                RETURNING *
            """)

            now = datetime.now()

            result = session.execute(
                query,
                {
                    "running_status": "running",
                    "pending_status": "pending",
                    "retrying_status": "retrying",
                    "completed_status": "completed",
                    "worker_id": worker_id,
                    "now": now
                }
            )

            row = result.fetchone()
            if row:
                session.commit()

                # Convert row to JobQueue object
                job = session.query(JobQueue).filter_by(id=row.id).first()

                logger.info(
                    f"✓ Claimed job {job.id} (type: {job.job_type}, "
                    f"meeting: {job.meeting_id}, priority: {job.priority}, "
                    f"retry: {job.retry_count}/{job.max_retries})"
                )

                return job
            else:
                logger.debug("No jobs available to claim")
                return None

    def update_heartbeat(self, job_id: int) -> bool:
        """
        Update job heartbeat timestamp (for timeout detection).

        Args:
            job_id: Job ID

        Returns:
            True if updated
        """
        with self.db.get_session() as session:
            job = session.query(JobQueue).filter_by(id=job_id).first()
            if job and job.status == "running":
                job.heartbeat_at = datetime.now()
                session.commit()
                return True
            return False

    def mark_completed(self, job_id: int, output_data: Dict[str, Any]) -> None:
        """
        Mark job as completed with output data.

        Args:
            job_id: Job ID
            output_data: Job output data (processor results)
        """
        with self.db.get_session() as session:
            job = session.query(JobQueue).filter_by(id=job_id).first()
            if not job:
                raise JobQueueError(f"Job {job_id} not found")

            job.status = "completed"
            job.completed_at = datetime.now()
            job.output_data = output_data
            session.commit()

            logger.info(f"✓ Job {job_id} marked as completed (type: {job.job_type})")

    def mark_failed(
        self,
        job_id: int,
        error_message: str,
        should_retry: bool = True,
        output_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Mark job as failed and optionally schedule retry.

        Args:
            job_id: Job ID
            error_message: Error description
            should_retry: If True and retries remaining, schedule retry
            output_data: Optional output data (partial results, error details)
        """
        with self.db.get_session() as session:
            job = session.query(JobQueue).filter_by(id=job_id).first()
            if not job:
                raise JobQueueError(f"Job {job_id} not found")

            job.error_message = error_message
            if output_data:
                job.output_data = output_data

            # Check if we should retry
            if should_retry and job.retry_count < job.max_retries:
                job.retry_count += 1
                job.status = "retrying"

                # Calculate next retry time using exponential backoff
                strategy = get_retry_strategy(job.job_type)
                job.next_retry_at = calculate_next_retry(
                    retry_count=job.retry_count,
                    base_delay=strategy["base_delay_seconds"],
                    max_delay=strategy["max_delay_seconds"],
                    jitter=True
                )

                logger.warning(
                    f"Job {job_id} failed, scheduling retry {job.retry_count}/{job.max_retries} "
                    f"at {job.next_retry_at.strftime('%Y-%m-%d %H:%M:%S')}: {error_message}"
                )
            else:
                job.status = "failed"
                job.completed_at = datetime.now()

                reason = "max retries exceeded" if job.retry_count >= job.max_retries else "retry disabled"
                logger.error(f"✗ Job {job_id} permanently failed ({reason}): {error_message}")

                # Update meeting status to failed
                if job.meeting_id:
                    meeting = session.query(Meeting).filter_by(id=job.meeting_id).first()
                    if meeting:
                        meeting.status = "failed"
                        meeting.error_message = error_message

            session.commit()

    def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get job queue statistics for monitoring.

        Returns:
            Dictionary with:
                - total_jobs: Total jobs in queue
                - by_status: Count by status
                - by_type: Count by job type
                - oldest_pending: Oldest pending job age in minutes
                - avg_processing_time: Average processing time in seconds
        """
        with self.db.get_session() as session:
            total = session.query(JobQueue).count()

            # Count by status
            by_status = {}
            for status in ['pending', 'running', 'completed', 'failed', 'retrying']:
                count = session.query(JobQueue).filter_by(status=status).count()
                by_status[status] = count

            # Count by type
            by_type = {}
            for job_type in ['fetch_transcript', 'generate_summary', 'distribute']:
                count = session.query(JobQueue).filter_by(job_type=job_type).count()
                by_type[job_type] = count

            # Oldest pending job
            oldest_pending = session.query(JobQueue).filter(
                JobQueue.status.in_(["pending", "retrying"])
            ).order_by(JobQueue.created_at.asc()).first()

            oldest_age_minutes = None
            if oldest_pending:
                age = datetime.now() - oldest_pending.created_at
                oldest_age_minutes = int(age.total_seconds() / 60)

            # Average processing time (completed jobs only)
            completed_jobs = session.query(JobQueue).filter(
                JobQueue.status == "completed",
                JobQueue.started_at.isnot(None),
                JobQueue.completed_at.isnot(None)
            ).all()

            avg_processing_seconds = None
            if completed_jobs:
                total_time = sum(
                    (job.completed_at - job.started_at).total_seconds()
                    for job in completed_jobs
                )
                avg_processing_seconds = total_time / len(completed_jobs)

            return {
                "total_jobs": total,
                "by_status": by_status,
                "by_type": by_type,
                "oldest_pending_minutes": oldest_age_minutes,
                "avg_processing_seconds": avg_processing_seconds,
                "timestamp": datetime.now().isoformat()
            }

    def cleanup_old_jobs(self, days: int = 90) -> int:
        """
        Delete completed and failed jobs older than specified days.

        Args:
            days: Delete jobs older than this many days

        Returns:
            Number of jobs deleted
        """
        with self.db.get_session() as session:
            cutoff = datetime.now() - timedelta(days=days)

            deleted = session.query(JobQueue).filter(
                JobQueue.status.in_(["completed", "failed"]),
                JobQueue.completed_at < cutoff
            ).delete()

            session.commit()

            logger.info(f"Cleaned up {deleted} jobs older than {days} days")
            return deleted

    def cancel_meeting_jobs(self, meeting_id: int) -> int:
        """
        Cancel all pending/retrying jobs for a meeting.

        Args:
            meeting_id: Meeting ID

        Returns:
            Number of jobs cancelled
        """
        with self.db.get_session() as session:
            cancelled = 0

            jobs = session.query(JobQueue).filter(
                JobQueue.meeting_id == meeting_id,
                JobQueue.status.in_(["pending", "retrying"])
            ).all()

            for job in jobs:
                job.status = "failed"
                job.error_message = "Cancelled by user"
                job.completed_at = datetime.now()
                cancelled += 1

            session.commit()

            logger.info(f"Cancelled {cancelled} jobs for meeting {meeting_id}")
            return cancelled

    def get_job_status(self, job_id: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed job status.

        Args:
            job_id: Job ID

        Returns:
            Dictionary with job details or None if not found
        """
        with self.db.get_session() as session:
            job = session.query(JobQueue).filter_by(id=job_id).first()
            if not job:
                return None

            return {
                "id": job.id,
                "type": job.job_type,
                "status": job.status.value,
                "meeting_id": job.meeting_id,
                "priority": job.priority,
                "created_at": job.created_at.isoformat(),
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "retry_count": job.retry_count,
                "max_retries": job.max_retries,
                "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
                "worker_id": job.worker_id,
                "error_message": job.error_message,
                "depends_on_job_id": job.depends_on_job_id
            }
