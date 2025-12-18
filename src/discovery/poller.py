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
from ..core.database import DatabaseManager, Meeting, MeetingParticipant, ProcessingRun, PilotUser
from ..core.config import AppConfig
from ..jobs.queue import JobQueueManager
from ..discovery.filters import MeetingFilter
from ..core.exceptions import GraphAPIError
from ..chat import ChatMonitor, ChatCommandParser


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
        self.filter = MeetingFilter(self.db, config, self.graph_client)

        # Initialize chat monitoring components (Sprint 4)
        self.chat_parser = ChatCommandParser()
        self.chat_monitor = ChatMonitor(self.graph_client, self.db, self.chat_parser)

        logger.info(
            f"MeetingPoller initialized (pilot_mode: {config.app.pilot_mode_enabled}, "
            f"lookback: {config.app.lookback_hours}h, chat_monitoring: enabled)"
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
            "errors": 0,
            "commands_found": 0,
            "commands_queued": 0
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

                        # Only save to database if permanently rejected (not just "waiting")
                        # Meetings that haven't ended yet will be discovered again on next poll
                        is_temporary_skip = any(phrase in reason.lower() for phrase in [
                            "wait", "not yet", "hasn't ended", "buffer", "more min"
                        ])

                        if not dry_run and not is_temporary_skip:
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

            # Monitor chats for commands (Sprint 4)
            if not dry_run:
                chat_stats = self._monitor_chats()
                stats["commands_found"] = chat_stats.get("commands_found", 0)
                stats["commands_queued"] = chat_stats.get("commands_queued", 0)

            # Save processing run audit
            if not dry_run:
                self._save_processing_run(start_time, stats)

            duration = (datetime.now() - start_time).total_seconds()

            logger.info(
                f"Discovery cycle complete ({duration:.1f}s): "
                f"{stats['discovered']} discovered, {stats['new']} new, "
                f"{stats['queued']} queued, {stats['skipped']} skipped, "
                f"{stats['commands_found']} commands found, "
                f"{stats['commands_queued']} commands queued, "
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

        In pilot mode: Queries calendars of pilot users only
        In production mode: Queries all pilot users (can expand to all users later)

        Returns:
            List of meeting dictionaries
        """
        # Get pilot users to query
        with self.db.get_session() as session:
            pilot_users = session.query(PilotUser).filter_by(is_active=True).all()
            user_emails = [user.email for user in pilot_users]

        if not user_emails:
            logger.warning("No active pilot users found - nothing to discover")
            return []

        logger.info(
            f"Discovering meetings for {len(user_emails)} pilot users "
            f"(lookback: {self.config.app.lookback_hours}h)"
        )

        # Query meetings for pilot users
        try:
            meetings = self.discovery.discover_meetings(
                hours_back=self.config.app.lookback_hours,
                user_emails=user_emails
            )
            return meetings
        except Exception as e:
            logger.error(f"Error discovering meetings: {e}", exc_info=True)
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
                organizer_user_id=meeting_data.get("organizer_user_id", ""),
                start_time=meeting_data.get("start_time"),
                end_time=meeting_data.get("end_time"),
                duration_minutes=meeting_data.get("duration_minutes", 0),
                participant_count=meeting_data.get("participant_count", 0),
                join_url=meeting_data.get("join_url", ""),
                chat_id=meeting_data.get("chat_id", ""),
                recording_url=meeting_data.get("recording_url", ""),
                status=status
            )
            session.add(meeting)
            session.flush()

            meeting_id = meeting.id

            # Save participants (skip those without valid email)
            participants_added = 0
            for participant_data in meeting_data.get("participants", []):
                email = participant_data.get("email", "")
                if not email:
                    logger.debug(f"Skipping participant without email: {participant_data.get('display_name', 'Unknown')}")
                    continue

                # Check if participant is in pilot users
                is_pilot = self.db.is_pilot_user(email)

                participant = MeetingParticipant(
                    meeting_id=meeting_id,
                    email=email,
                    display_name=participant_data.get("display_name", ""),
                    role=participant_data.get("role", "attendee"),
                    is_pilot_user=is_pilot
                )
                session.add(participant)
                participants_added += 1

            session.commit()

            logger.debug(f"Saved meeting {meeting_id} with {participants_added} participants (of {len(meeting_data.get('participants', []))} total)")

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

    def _monitor_chats(self) -> Dict[str, int]:
        """
        Monitor meeting chats for bot commands (Sprint 4).

        Checks recent meetings with chat_id for new commands,
        parses them, and creates jobs to process them.

        Returns:
            Dictionary with stats:
                - commands_found: Number of commands detected
                - commands_queued: Number of command jobs created
        """
        stats = {"commands_found": 0, "commands_queued": 0}

        try:
            logger.debug("Starting chat monitoring cycle")

            # Get meetings with chat_id from last 7 days
            lookback_days = 7
            cutoff_date = datetime.now() - timedelta(days=lookback_days)

            with self.db.get_session() as session:
                # Query meetings with chat_id and recent start time
                recent_meetings = session.query(Meeting).filter(
                    Meeting.chat_id.isnot(None),
                    Meeting.chat_id != "",
                    Meeting.start_time >= cutoff_date
                ).all()

                logger.info(
                    f"Monitoring {len(recent_meetings)} meetings with chats "
                    f"(last {lookback_days} days)"
                )

                # Check each meeting's chat for commands
                for meeting in recent_meetings:
                    try:
                        # Determine when to check from
                        # Use last_chat_check if available, otherwise use discovered_at
                        since = meeting.last_chat_check or meeting.discovered_at

                        # Don't check chats older than 7 days
                        if since and since < cutoff_date:
                            since = cutoff_date

                        # Check for commands in this chat
                        commands = self.chat_monitor.check_for_commands(
                            chat_id=meeting.chat_id,
                            since=since,
                            limit=50
                        )

                        stats["commands_found"] += len(commands)

                        # Create jobs for each command
                        for command in commands:
                            self._queue_chat_command(command, meeting)
                            stats["commands_queued"] += 1

                        # Update last check time
                        meeting.last_chat_check = datetime.now()

                    except Exception as e:
                        logger.error(
                            f"Error monitoring chat for meeting {meeting.id}: {e}",
                            exc_info=True
                        )
                        continue

                # Commit all last_chat_check updates
                session.commit()

            if stats["commands_found"] > 0:
                logger.info(
                    f"✓ Chat monitoring found {stats['commands_found']} commands, "
                    f"queued {stats['commands_queued']} jobs"
                )
            else:
                logger.debug("Chat monitoring found no new commands")

            return stats

        except Exception as e:
            logger.error(f"Chat monitoring cycle failed: {e}", exc_info=True)
            return stats

    def _queue_chat_command(self, command, meeting: Meeting):
        """
        Create job to process chat command.

        Args:
            command: Command object from parser
            meeting: Meeting associated with command
        """
        try:
            # Create job for command processing
            job = self.queue.create_job(
                job_type="process_chat_command",
                input_data={
                    "command_type": command.command_type.value,
                    "meeting_id": meeting.id,
                    "message_id": command.message_id,
                    "chat_id": command.chat_id,
                    "user_email": command.user_email,
                    "user_name": command.user_name,
                    "parameters": command.parameters,
                    "raw_message": command.raw_message
                },
                priority=8  # Higher priority than normal processing
            )

            logger.info(
                f"Queued chat command job: {command.command_type.value} "
                f"from {command.user_email} (job_id: {job.id})"
            )

        except Exception as e:
            logger.error(f"Error queueing chat command: {e}", exc_info=True)
