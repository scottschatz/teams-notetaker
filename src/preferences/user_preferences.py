"""
User Preference Management

Manages user preferences for meeting summary distribution.
Supports opt-in/opt-out for email summaries via database storage.
"""

import logging
from typing import Optional, List
from datetime import datetime

from ..core.database import DatabaseManager, UserPreference


logger = logging.getLogger(__name__)


class PreferenceManager:
    """
    Manages user preferences for meeting summaries.

    Stores preferences in database to persist across sessions.
    Supports:
    - Email opt-in/opt-out
    - Bulk preference updates
    - Meeting-specific preferences (future enhancement)

    Usage:
        pref_mgr = PreferenceManager(db)

        # Check if user wants emails
        if pref_mgr.get_user_preference("user@example.com"):
            send_email(...)

        # User opts out
        pref_mgr.set_user_preference("user@example.com", receive_emails=False)

        # Organizer disables emails for all participants
        pref_mgr.bulk_disable_for_meeting(meeting_id=123, disabled_by="organizer@example.com")
    """

    def __init__(self, db: DatabaseManager):
        """
        Initialize preference manager.

        Args:
            db: DatabaseManager instance
        """
        self.db = db

    def get_user_preference(self, email: str) -> bool:
        """
        Get user's email preference.

        Args:
            email: User email address

        Returns:
            True if user wants emails, False if opted out

        Default:
            Returns True if no preference set (opt-in by default)
        """
        try:
            email = email.lower().strip()

            with self.db.get_session() as session:
                pref = session.query(UserPreference).filter_by(user_email=email).first()

                if not pref:
                    # Default: user receives emails
                    logger.debug(f"No preference found for {email}, defaulting to opt-in")
                    return True

                return pref.receive_emails

        except Exception as e:
            logger.error(f"Error getting user preference for {email}: {e}")
            # On error, default to sending emails (fail-open)
            return True

    def set_user_preference(
        self,
        email: str,
        receive_emails: bool,
        updated_by: str = "user"
    ) -> bool:
        """
        Set user's email preference.

        Args:
            email: User email address
            receive_emails: True to receive emails, False to opt out
            updated_by: Who updated the preference ('user', 'organizer', 'admin')

        Returns:
            True if successfully saved

        Creates new preference record if one doesn't exist.
        """
        try:
            email = email.lower().strip()

            with self.db.get_session() as session:
                pref = session.query(UserPreference).filter_by(user_email=email).first()

                if pref:
                    # Update existing preference
                    pref.receive_emails = receive_emails
                    pref.email_preference = 'all' if receive_emails else 'disabled'
                    pref.updated_at = datetime.now()
                    pref.updated_by = updated_by

                    logger.info(
                        f"Updated preference for {email}: receive_emails={receive_emails} "
                        f"(by {updated_by})"
                    )
                else:
                    # Create new preference
                    pref = UserPreference(
                        user_email=email,
                        receive_emails=receive_emails,
                        email_preference='all' if receive_emails else 'disabled',
                        updated_by=updated_by
                    )
                    session.add(pref)

                    logger.info(
                        f"Created preference for {email}: receive_emails={receive_emails} "
                        f"(by {updated_by})"
                    )

                session.commit()
                return True

        except Exception as e:
            logger.error(f"Error setting user preference for {email}: {e}", exc_info=True)
            return False

    def bulk_disable_for_meeting(
        self,
        meeting_id: int,
        participant_emails: List[str],
        disabled_by: str
    ) -> int:
        """
        Bulk disable email summaries for all participants of a meeting.

        Used when organizer requests "no emails" for a specific meeting.

        Args:
            meeting_id: Meeting database ID
            participant_emails: List of participant email addresses
            disabled_by: Email of person who disabled (usually organizer)

        Returns:
            Number of preferences updated

        Note: This sets global preference for each user, not meeting-specific.
        Future enhancement: Add meeting-specific preferences.
        """
        try:
            count = 0

            for email in participant_emails:
                if self.set_user_preference(
                    email=email,
                    receive_emails=False,
                    updated_by=f"organizer:{disabled_by}"
                ):
                    count += 1

            logger.info(
                f"Bulk disabled emails for {count}/{len(participant_emails)} participants "
                f"of meeting {meeting_id} (by {disabled_by})"
            )

            return count

        except Exception as e:
            logger.error(
                f"Error bulk disabling for meeting {meeting_id}: {e}",
                exc_info=True
            )
            return 0

    def is_opted_in(self, email: str, meeting_id: Optional[int] = None) -> bool:
        """
        Check if user is opted in for email summaries.

        Args:
            email: User email address
            meeting_id: Optional meeting ID (for future meeting-specific preferences)

        Returns:
            True if user should receive emails, False otherwise

        Currently only checks global preference.
        Future enhancement: Check meeting-specific preferences.
        """
        # Currently just checks global preference
        # Future: Add meeting-specific preference check
        return self.get_user_preference(email)

    def get_opted_in_emails(self, emails: List[str]) -> List[str]:
        """
        Filter list of emails to only those opted in.

        Args:
            emails: List of email addresses to check

        Returns:
            List of email addresses that should receive summaries

        Useful for bulk filtering before sending emails.
        """
        try:
            opted_in = []

            for email in emails:
                if self.get_user_preference(email):
                    opted_in.append(email)

            logger.debug(
                f"Filtered {len(emails)} emails to {len(opted_in)} opted-in recipients"
            )

            return opted_in

        except Exception as e:
            logger.error(f"Error filtering opted-in emails: {e}")
            # On error, return all emails (fail-open)
            return emails

    def get_preference_stats(self) -> dict:
        """
        Get statistics about user preferences.

        Returns:
            Dictionary with counts:
            - total_users: Total users with preferences set
            - opted_in: Users who receive emails
            - opted_out: Users who opted out

        Useful for analytics and monitoring.
        """
        try:
            with self.db.get_session() as session:
                total = session.query(UserPreference).count()
                opted_in = session.query(UserPreference).filter_by(
                    receive_emails=True
                ).count()
                opted_out = total - opted_in

                return {
                    "total_users": total,
                    "opted_in": opted_in,
                    "opted_out": opted_out,
                    "opt_out_rate": (opted_out / total * 100) if total > 0 else 0
                }

        except Exception as e:
            logger.error(f"Error getting preference stats: {e}")
            return {
                "total_users": 0,
                "opted_in": 0,
                "opted_out": 0,
                "opt_out_rate": 0
            }

    def delete_user_preference(self, email: str) -> bool:
        """
        Delete user preference (resets to default).

        Args:
            email: User email address

        Returns:
            True if deleted successfully

        After deletion, user will receive default behavior (opt-in).
        """
        try:
            email = email.lower().strip()

            with self.db.get_session() as session:
                pref = session.query(UserPreference).filter_by(user_email=email).first()

                if pref:
                    session.delete(pref)
                    session.commit()
                    logger.info(f"Deleted preference for {email}")
                    return True
                else:
                    logger.debug(f"No preference found for {email} to delete")
                    return False

        except Exception as e:
            logger.error(f"Error deleting preference for {email}: {e}", exc_info=True)
            return False
