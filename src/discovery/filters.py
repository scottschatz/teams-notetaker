"""
Meeting Filters

Filters for determining which meetings should be processed.
Supports pilot mode, exclusions, and duration filtering.
"""

import logging
from typing import Dict, Any, Tuple, List

from ..core.database import DatabaseManager, Exclusion
from ..core.config import AppConfig


logger = logging.getLogger(__name__)


class MeetingFilter:
    """
    Filters meetings based on pilot mode, exclusions, and other criteria.

    Filtering rules (applied in order):
    1. Duration: Skip meetings shorter than minimum duration
    2. Exclusions: Skip blacklisted users/domains/organizers
    3. Pilot mode: If enabled, require at least one pilot user as participant

    Usage:
        filter = MeetingFilter(db, config)
        should_process, reason = filter.should_process_meeting(meeting_data)
    """

    def __init__(self, db: DatabaseManager, config: AppConfig):
        """
        Initialize meeting filter.

        Args:
            db: DatabaseManager instance
            config: AppConfig instance
        """
        self.db = db
        self.config = config

    def should_process_meeting(self, meeting_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determine if a meeting should be processed.

        Args:
            meeting_data: Meeting data dictionary with:
                - duration_minutes: Meeting duration
                - organizer_email: Organizer email
                - participants: List of participant dicts

        Returns:
            Tuple of (should_process: bool, reason: str)
        """
        # Check duration
        duration = meeting_data.get("duration_minutes", 0)
        if duration < self.config.minimum_meeting_duration_minutes:
            return False, f"Duration too short ({duration} min < {self.config.minimum_meeting_duration_minutes} min)"

        # Check exclusions
        is_excluded, reason = self._is_excluded(meeting_data)
        if is_excluded:
            return False, reason

        # Check pilot mode
        if self.config.pilot_mode_enabled:
            has_pilot_user, reason = self._has_pilot_user(meeting_data)
            if not has_pilot_user:
                return False, reason

        return True, "Passed all filters"

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
