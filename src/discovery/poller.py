"""
Meeting Discovery Poller

Polls Microsoft Graph API for new Teams meetings and enqueues them for processing.
Runs every N minutes (configured) and respects pilot mode filtering.
"""

import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ..graph.client import GraphAPIClient
from ..graph.meetings import MeetingDiscovery
from ..core.database import DatabaseManager, Meeting, MeetingParticipant, ProcessingRun
from ..core.config import AppConfig
from ..jobs.queue import JobQueueManager
from ..discovery.filters import MeetingFilter
from ..core.exceptions import GraphAPIError


logger = logging.getLogger(__name__)


class MeetingPoller:
    """
    Discovers and enqueues Teams meetings for processing.

    Features:
    - Periodic polling (every N minutes)
    - Pilot mode filtering (only process meetings with pilot users)
    - Exclusion filtering (skip blacklisted users/domains)
    - Deduplication (skip already-processed meetings)
    - Meeting metadata storage
    - Audit logging (processing_runs table)

    Usage:
        config = get_config()
        poller = MeetingPoller(config)

        # Run once
        poller.run_discovery()

        # Run continuously
        poller.run_loop(interval_minutes=5)
    """

    def __init__(
        self,
        config: AppConfig,
        db: Optional[DatabaseManager] = None,
        graph_client: Optional[GraphAPIClient] = None
    ):
        """
        Initialize meeting poller.

        Args:
            config: AppConfig instance
            db: DatabaseManager instance (created if None)
            graph_client: GraphAPIClient instance (created if None)
        """
        self.config = config
        self.db = db or DatabaseManager(config.database.connection_string)
        self.graph_client = graph_client or GraphAPIClient(config.graph_api)

        # Initialize components
        self.discovery = MeetingDiscovery(self.graph_client)
        self.queue = JobQueueManager(self.db)
        self.filter = MeetingFilter(self.db, config)

        logger.info(
            f"MeetingPoller initialized (pilot_mode: {config.app.pilot_mode_enabled}, "
            f"lookback: {config.app.lookback_hours}h)"
        )

    def run_discovery(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run single discovery cycle.

        Args:
            dry_run: If True, discover but don't enqueue jobs

        Returns:
            Dictionary with statistics:
                - discovered: Number of meetings found
                - new: Number of new meetings
                - queued: Number of meetings queued for processing
                - skipped: Number of meetings skipped (filters, already processed)
                - errors: Number of errors encountered
        """
        start_time = datetime.now()

        logger.info(f"Starting discovery cycle (dry_run: {dry_run})")

        stats = {
            "discovered": 0,
            "new": 0,
            "queued": 0,
            "skipped": 0,
            "errors": 0
        }

        try:
            # Discover meetings from Graph API
            meetings = self._discover_meetings()
            stats["discovered"] = len(meetings)

            logger.info(f"Discovered {len(meetings)} meetings from Graph API")

            # Process each meeting
            for meeting_data in meetings:
                try:
                    # Check if meeting already exists
                    if self._meeting_exists(meeting_data["meeting_id"]):
                        logger.debug(f"Meeting {meeting_data['meeting_id']} already exists, skipping")
                        stats["skipped"] += 1
                        continue

                    # Apply filters
                    should_process, reason = self.filter.should_process_meeting(meeting_data)

                    if not should_process:
                        logger.info(f"Skipping meeting '{meeting_data['subject']}': {reason}")
                        stats["skipped"] += 1

                        # Still save to database but mark as skipped
                        if not dry_run:
                            self._save_meeting(meeting_data, status="skipped")

                        continue

                    # Save meeting to database
                    if not dry_run:
                        meeting_id = self._save_meeting(meeting_data, status="discovered")

                        # Enqueue for processing
                        self.queue.enqueue_meeting_jobs(meeting_id, priority=5)

                        logger.info(f"✓ Queued meeting '{meeting_data['subject']}' (id: {meeting_id})")
                        stats["queued"] += 1
                    else:
                        logger.info(f"[DRY RUN] Would queue meeting '{meeting_data['subject']}'")
                        stats["queued"] += 1

                    stats["new"] += 1

                except Exception as e:
                    logger.error(f"Error processing meeting: {e}", exc_info=True)
                    stats["errors"] += 1

            # Save processing run audit
            if not dry_run:
                self._save_processing_run(start_time, stats)

            duration = (datetime.now() - start_time).total_seconds()

            logger.info(
                f"Discovery cycle complete ({duration:.1f}s): "
                f"{stats['discovered']} discovered, {stats['new']} new, "
                f"{stats['queued']} queued, {stats['skipped']} skipped, "
                f"{stats['errors']} errors"
            )

            return stats

        except Exception as e:
            logger.error(f"Discovery cycle failed: {e}", exc_info=True)
            stats["errors"] += 1
            return stats

    def run_loop(self, interval_minutes: Optional[int] = None):
        """
        Run discovery in a continuous loop.

        Args:
            interval_minutes: Polling interval (default from config)
        """
        if interval_minutes is None:
            interval_minutes = self.config.app.polling_interval_minutes

        logger.info(f"Starting discovery loop (interval: {interval_minutes} minutes)")

        try:
            while True:
                # Run discovery
                self.run_discovery()

                # Sleep until next poll
                logger.info(f"Sleeping for {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            logger.info("Discovery loop stopped by user")
        except Exception as e:
            logger.error(f"Discovery loop crashed: {e}", exc_info=True)
            raise

    def _discover_meetings(self) -> List[Dict[str, Any]]:
        """
        Discover meetings from Graph API.

        Returns:
            List of meeting dictionaries
        """
        # For now, we need to implement org-wide discovery
        # This is a placeholder - actual implementation depends on Graph API permissions

        # Option 1: Query specific users' calendars (requires user list)
        # Option 2: Use change notifications/webhooks
        # Option 3: Query shared calendars

        # For initial implementation, let's return empty list
        # This will be populated once we have proper Graph API access

        logger.warning(
            "Org-wide meeting discovery not fully implemented yet. "
            "Need to configure user iteration or webhook-based discovery."
        )

        # Placeholder: return empty list
        # In production, this would call:
        # return self.discovery.discover_meetings(hours_back=self.config.app.lookback_hours)

        return []

    def _meeting_exists(self, meeting_id: str) -> bool:
        """
        Check if meeting already exists in database.

        Args:
            meeting_id: Graph API meeting ID

        Returns:
            True if meeting exists
        """
        with self.db.get_session() as session:
            exists = session.query(Meeting).filter_by(meeting_id=meeting_id).first() is not None
            return exists

    def _save_meeting(self, meeting_data: Dict[str, Any], status: str) -> int:
        """
        Save meeting and participants to database.

        Args:
            meeting_data: Meeting data from Graph API
            status: Initial meeting status

        Returns:
            Database meeting ID
        """
        with self.db.get_session() as session:
            # Create meeting record
            meeting = Meeting(
                meeting_id=meeting_data["meeting_id"],
                subject=meeting_data.get("subject", "No Subject"),
                organizer_email=meeting_data.get("organizer_email", ""),
                organizer_name=meeting_data.get("organizer_name", ""),
                start_time=meeting_data.get("start_time"),
                end_time=meeting_data.get("end_time"),
                duration_minutes=meeting_data.get("duration_minutes", 0),
                participant_count=meeting_data.get("participant_count", 0),
                status=status
            )
            session.add(meeting)
            session.flush()

            meeting_id = meeting.id

            # Save participants
            for participant_data in meeting_data.get("participants", []):
                # Check if participant is in pilot users
                is_pilot = self.db.is_pilot_user(participant_data["email"])

                participant = MeetingParticipant(
                    meeting_id=meeting_id,
                    email=participant_data["email"],
                    display_name=participant_data.get("display_name", ""),
                    role=participant_data.get("role", "attendee"),
                    is_pilot_user=is_pilot
                )
                session.add(participant)

            session.commit()

            logger.debug(f"Saved meeting {meeting_id} with {len(meeting_data.get('participants', []))} participants")

            return meeting_id

    def _save_processing_run(self, start_time: datetime, stats: Dict[str, int]):
        """
        Save processing run audit record.

        Args:
            start_time: When discovery started
            stats: Discovery statistics
        """
        with self.db.get_session() as session:
            run = ProcessingRun(
                started_at=start_time,
                completed_at=datetime.now(),
                mode="pilot" if self.config.app.pilot_mode_enabled else "production",
                meetings_discovered=stats["discovered"],
                meetings_queued=stats["queued"],
                meetings_skipped=stats["skipped"],
                jobs_created=stats["queued"] * 3  # 3 jobs per meeting
            )
            session.add(run)
            session.commit()
