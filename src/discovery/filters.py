"""
Meeting Filters

Filters for determining which meetings should be processed.
Supports pilot mode, exclusions, duration filtering, and completion checks.
"""

import logging
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime, timedelta, timezone

from ..core.database import DatabaseManager, Exclusion
from ..core.config import AppConfig
from ..graph.client import GraphAPIClient
from ..graph.transcripts import TranscriptFetcher


logger = logging.getLogger(__name__)


class MeetingFilter:
    """
    Filters meetings based on pilot mode, exclusions, and other criteria.

    Filtering rules (applied in order):
    1. Completion: Skip meetings that haven't ended or need more buffer time for transcripts
    2. Duration: Skip meetings shorter than minimum duration
    3. Exclusions: Skip blacklisted users/domains/organizers
    4. Pilot mode: If enabled, require at least one pilot user as participant

    Usage:
        filter = MeetingFilter(db, config)
        should_process, reason = filter.should_process_meeting(meeting_data)
    """

    def __init__(self, db: DatabaseManager, config: AppConfig, graph_client: Optional[GraphAPIClient] = None):
        """
        Initialize meeting filter.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
            graph_client: Optional GraphAPIClient instance for transcript availability checks
        """
        self.db = db
        self.config = config
        self.graph_client = graph_client
        self.transcript_fetcher = TranscriptFetcher(graph_client) if graph_client else None

    def should_process_meeting(self, meeting_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determine if a meeting should be processed.

        Args:
            meeting_data: Meeting data dictionary with:
                - end_time: Meeting end time (datetime)
                - duration_minutes: Meeting duration
                - organizer_email: Organizer email
                - participants: List of participant dicts

        Returns:
            Tuple of (should_process: bool, reason: str)
        """
        # Check if meeting has completed (with buffer time for transcript generation)
        is_completed, reason = self._is_meeting_completed(meeting_data)
        if not is_completed:
            return False, reason

        # Check duration
        duration = meeting_data.get("duration_minutes", 0)
        if duration < self.config.app.minimum_meeting_duration_minutes:
            return False, f"Duration too short ({duration} min < {self.config.app.minimum_meeting_duration_minutes} min)"

        # Check exclusions
        is_excluded, reason = self._is_excluded(meeting_data)
        if is_excluded:
            return False, reason

        # Check pilot mode
        if self.config.app.pilot_mode_enabled:
            has_pilot_user, reason = self._has_pilot_user(meeting_data)
            if not has_pilot_user:
                return False, reason

        return True, "Passed all filters"

    def _is_meeting_completed(self, meeting_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if meeting has completed using call record data.

        Uses actual end time from call records (not scheduled time) to determine
        when a meeting truly ended. This handles:
        - Meetings that end early (3 min instead of 60 min scheduled)
        - Meetings that run late (65 min instead of 60 min scheduled)
        - In-progress meetings (call record won't have endDateTime yet)
        - Canceled meetings (no call record exists)

        Falls back to scheduled time + buffer if no call record available.

        Args:
            meeting_data: Meeting data dictionary with:
                - end_time: Actual end time (from call record) or scheduled end time (datetime)
                - call_record_id: ID of call record (if meeting happened)
                - start_time: Meeting start time
                - organizer_email: Meeting organizer's email
                - online_meeting_id: Teams meeting ID (optional)

        Returns:
            Tuple of (is_completed: bool, reason: str)
        """
        end_time = meeting_data.get("end_time")
        call_record_id = meeting_data.get("call_record_id")

        if not end_time:
            return False, "No end time available"

        # Ensure end_time is timezone-aware
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # If we have a call record, the end_time is ACTUAL (not scheduled)
        if call_record_id:
            # Call record exists, so meeting happened and end_time is actual
            # Add small buffer for transcript generation (5 minutes after actual end)
            buffer_minutes = 5
            completion_time = end_time + timedelta(minutes=buffer_minutes)

            if now < completion_time:
                minutes_remaining = int((completion_time - now).total_seconds() / 60)
                return False, (
                    f"Meeting ended but waiting for transcript generation "
                    f"({minutes_remaining} more min, {buffer_minutes} min buffer after actual end)"
                )

            logger.debug(
                f"Meeting completed with call record (actual end: {end_time}, "
                f"buffer passed: {completion_time})"
            )
            return True, "Meeting completed (using actual end time from call record)"

        # No call record yet - check if we're past scheduled end time
        if now < end_time:
            minutes_until_end = int((end_time - now).total_seconds() / 60)
            return False, (
                f"Meeting not yet at scheduled end time (wait {minutes_until_end} more min, "
                f"no call record available yet)"
            )

        # No call record available yet - could mean:
        # 1. Meeting hasn't happened yet
        # 2. Meeting just ended (call record takes a few minutes to appear)
        # 3. Meeting was canceled
        # Fall back to longer buffer time to be safe

        # Fallback: Use time-based check with 15-minute buffer
        # This ensures we don't get stuck if Graph API is unavailable
        buffer_minutes = 15
        completion_time = end_time + timedelta(minutes=buffer_minutes)

        if now < completion_time:
            minutes_remaining = int((completion_time - now).total_seconds() / 60)
            return False, (
                f"Using fallback time check: wait {minutes_remaining} more minutes "
                f"(15 min buffer after scheduled end)"
            )

        logger.debug(f"Fallback: Meeting completed at {end_time}, buffer passed at {completion_time}")
        return True, "Meeting completed (fallback time check)"

    def _is_excluded(self, meeting_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if meeting is excluded.

        Args:
            meeting_data: Meeting data

        Returns:
            Tuple of (is_excluded: bool, reason: str)
        """
        organizer_email = meeting_data.get("organizer_email", "")
        participants = meeting_data.get("participants", [])

        # Get active exclusions
        exclusions = self._get_active_exclusions()

        # Check organizer exclusion
        for exclusion in exclusions:
            if exclusion.type == "organizer":
                if organizer_email.lower() == exclusion.value.lower():
                    return True, f"Organizer excluded: {exclusion.reason}"

        # Check user exclusions
        for exclusion in exclusions:
            if exclusion.type == "user":
                for participant in participants:
                    if participant["email"].lower() == exclusion.value.lower():
                        return True, f"Participant excluded: {exclusion.reason}"

        # Check domain exclusions
        for exclusion in exclusions:
            if exclusion.type == "domain":
                # Check organizer domain
                if "@" in organizer_email:
                    organizer_domain = organizer_email.split("@")[1].lower()
                    if organizer_domain == exclusion.value.lower():
                        return True, f"Organizer domain excluded: {exclusion.reason}"

                # Check participant domains
                for participant in participants:
                    email = participant["email"]
                    if "@" in email:
                        domain = email.split("@")[1].lower()
                        if domain == exclusion.value.lower():
                            return True, f"Participant domain excluded: {exclusion.reason}"

        return False, ""

    def _has_pilot_user(self, meeting_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if meeting has at least one pilot user.

        Args:
            meeting_data: Meeting data

        Returns:
            Tuple of (has_pilot: bool, reason: str)
        """
        participants = meeting_data.get("participants", [])

        if not participants:
            return False, "No participants found"

        # Check each participant
        for participant in participants:
            email = participant.get("email", "")
            if self.db.is_pilot_user(email):
                logger.debug(f"Meeting has pilot user: {email}")
                return True, f"Has pilot user: {email}"

        return False, "No pilot users in meeting"

    def _get_active_exclusions(self) -> List[Exclusion]:
        """
        Get all active exclusions from database.

        Returns:
            List of Exclusion objects
        """
        with self.db.get_session() as session:
            exclusions = session.query(Exclusion).filter_by(is_active=True).all()
            # Detach from session
            return [
                type('Exclusion', (), {
                    'type': e.type,
                    'value': e.value,
                    'reason': e.reason,
                    'is_active': e.is_active
                })()
                for e in exclusions
            ]
