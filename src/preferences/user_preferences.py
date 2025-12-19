"""
User Preference Management

Manages user preferences for meeting summary distribution.
Supports opt-in/opt-out for email summaries via database storage.

Uses Azure AD user_id (GUID) as the primary identity key for stable matching
across email aliases and address changes.
"""

import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

from ..core.database import DatabaseManager, UserPreference, MeetingPreference, Meeting, EmailAlias
from ..core.config import get_config
from ..graph.client import GraphAPIClient
from ..core.exceptions import GraphAPIError


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

    def _normalize_email(self, email: str) -> str:
        """
        Normalize email for comparison.

        Handles Microsoft email aliases by removing dots from local part.
        Example: Scott.Schatz@domain.com -> scottschatz@domain.com

        Args:
            email: Email address to normalize

        Returns:
            Normalized email (lowercase, dots removed from local part)
        """
        if not email:
            return ""
        email = email.lower().strip()
        if "@" in email:
            local, domain = email.split("@", 1)
            # Remove dots from local part (handles aliases like Scott.Schatz vs sschatz)
            local = local.replace(".", "")
            return f"{local}@{domain}"
        return email

    def _get_primary_email(self, email: str) -> str:
        """
        Look up the primary email for an alias using the email_aliases cache.

        This handles cases where users have different aliases (e.g., sschatz vs scottschatz)
        that the simple dot-removal normalization can't handle.

        Args:
            email: Email address (possibly an alias)

        Returns:
            Primary email if found in cache, otherwise original email
        """
        if not email:
            return ""
        email = email.lower().strip()

        try:
            with self.db.get_session() as session:
                alias_record = session.query(EmailAlias).filter_by(alias_email=email).first()
                if alias_record and alias_record.primary_email:
                    return alias_record.primary_email.lower()
        except Exception as e:
            logger.debug(f"Error looking up primary email for {email}: {e}")

        return email

    def _get_user_id(self, email: str) -> Optional[str]:
        """
        Look up the Azure AD user ID for an email.

        The user ID is stable and never changes, even if the user's email changes.

        Args:
            email: Email address

        Returns:
            Azure AD user ID (GUID) if found, otherwise None
        """
        if not email:
            return None
        email = email.lower().strip()

        try:
            with self.db.get_session() as session:
                alias_record = session.query(EmailAlias).filter_by(alias_email=email).first()
                if alias_record and alias_record.user_id:
                    return alias_record.user_id
        except Exception as e:
            logger.debug(f"Error looking up user ID for {email}: {e}")

        return None

    def _get_all_emails_for_user(self, email: str) -> List[str]:
        """
        Get all known email aliases for a user.

        Uses the user_id to find all aliases that belong to the same user.

        Args:
            email: Any email address for the user

        Returns:
            List of all known email addresses for this user
        """
        if not email:
            return []
        email = email.lower().strip()

        try:
            with self.db.get_session() as session:
                # First get the user_id for this email
                alias_record = session.query(EmailAlias).filter_by(alias_email=email).first()
                if not alias_record or not alias_record.user_id:
                    return [email]

                # Get all aliases with same user_id
                all_aliases = session.query(EmailAlias).filter_by(
                    user_id=alias_record.user_id
                ).all()

                return [a.alias_email for a in all_aliases]
        except Exception as e:
            logger.debug(f"Error getting all emails for user {email}: {e}")

        return [email]

    def _resolve_user_id_from_graph(self, email: str) -> Tuple[Optional[str], str, str]:
        """
        Resolve email to user_id (GUID) via Graph API.

        Fetches user info from Azure AD and caches the alias mapping.

        Args:
            email: Email address to resolve

        Returns:
            Tuple of (user_id, primary_email, display_name)
            user_id may be None if Graph API lookup fails

        Side Effects:
            Creates/updates EmailAlias record with resolved info
        """
        email = email.lower().strip()

        try:
            config = get_config()
            graph_client = GraphAPIClient(config.graph_api)

            user_info = graph_client.get(
                f"/users/{email}",
                params={"$select": "id,mail,userPrincipalName,displayName,jobTitle"}
            )

            user_id = user_info.get("id")
            primary_email = user_info.get("mail") or user_info.get("userPrincipalName", "")
            primary_email = primary_email.lower().strip() if primary_email else email
            display_name = user_info.get("displayName") or ""
            job_title = user_info.get("jobTitle") or ""

            # Cache the result in EmailAlias
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.get_session() as session:
                alias_record = EmailAlias(
                    alias_email=email,
                    primary_email=primary_email,
                    user_id=user_id,
                    display_name=display_name,
                    job_title=job_title,
                    resolved_at=now,
                    last_used_at=now
                )
                session.merge(alias_record)

                # Also cache primary email if different
                if primary_email and primary_email != email:
                    primary_record = EmailAlias(
                        alias_email=primary_email,
                        primary_email=primary_email,
                        user_id=user_id,
                        display_name=display_name,
                        job_title=job_title,
                        resolved_at=now,
                        last_used_at=now
                    )
                    session.merge(primary_record)

                session.commit()

            logger.info(f"Resolved {email} -> user_id: {user_id}, primary: {primary_email}")
            return user_id, primary_email, display_name

        except GraphAPIError as e:
            logger.warning(f"Graph API lookup failed for {email}: {e}")
            return None, email, ""
        except Exception as e:
            logger.error(f"Unexpected error resolving user_id for {email}: {e}")
            return None, email, ""

    def _get_or_resolve_user_id(self, email: str) -> Tuple[Optional[str], str, str]:
        """
        Get user_id from cache (EmailAlias) or resolve via Graph API.

        Args:
            email: Email address

        Returns:
            Tuple of (user_id, primary_email, display_name)
        """
        email = email.lower().strip()

        # Try cache first
        user_id = self._get_user_id(email)
        if user_id:
            # Get additional info from cache
            with self.db.get_session() as session:
                alias_record = session.query(EmailAlias).filter_by(alias_email=email).first()
                if alias_record:
                    return user_id, alias_record.primary_email or email, alias_record.display_name or ""

        # Not in cache, resolve via Graph API
        return self._resolve_user_id_from_graph(email)

    def get_user_preference(self, email: str) -> bool:
        """
        Get user's email preference.

        Args:
            email: User email address

        Returns:
            True if user is subscribed, False otherwise

        Default:
            Returns False if no preference set (must explicitly subscribe)

        Note:
            Uses indexed SQL queries for O(1) lookups:
            1. First resolves email to user_id and all known aliases
            2. Uses single SQL query with IN clause on indexed columns
            3. Falls back to normalized email matching if needed
        """
        try:
            if not email:
                # No email = can't have preference, default to False (not subscribed)
                return False

            email_lower = email.lower().strip()
            normalized_input = self._normalize_email(email)

            with self.db.get_session() as session:
                # Step 1: Build list of all emails to check (using alias table)
                emails_to_check = {email_lower, normalized_input}

                # Look up this email in alias table
                alias_record = session.query(EmailAlias).filter_by(alias_email=email_lower).first()

                user_id = None
                if alias_record:
                    # Add primary email to check list
                    if alias_record.primary_email:
                        emails_to_check.add(alias_record.primary_email.lower())
                        emails_to_check.add(self._normalize_email(alias_record.primary_email))

                    # Get user_id for matching
                    user_id = alias_record.user_id

                    # If we have user_id, get ALL aliases for this user
                    if user_id:
                        all_aliases = session.query(EmailAlias.alias_email).filter_by(
                            user_id=user_id
                        ).all()
                        for (alias_email,) in all_aliases:
                            emails_to_check.add(alias_email.lower())

                # Step 2: Check if any subscriber has user_id match (most reliable)
                if user_id:
                    # Find all subscriber emails that have same user_id in alias table
                    subscribed_with_same_user_id = session.query(UserPreference).join(
                        EmailAlias,
                        UserPreference.user_email == EmailAlias.alias_email
                    ).filter(
                        EmailAlias.user_id == user_id,
                        UserPreference.receive_emails == True
                    ).first()

                    if subscribed_with_same_user_id:
                        logger.debug(
                            f"Subscription match by user_id: {email} -> "
                            f"{subscribed_with_same_user_id.user_email} (id: {user_id})"
                        )
                        return True

                # Step 3: Direct lookup on all known email variants (indexed query)
                from sqlalchemy import func
                direct_match = session.query(UserPreference).filter(
                    UserPreference.receive_emails == True,
                    func.lower(UserPreference.user_email).in_(emails_to_check)
                ).first()

                if direct_match:
                    logger.debug(f"Subscription match by email: {email} -> {direct_match.user_email}")
                    return True

                # Step 4: Check if any subscriber's primary email matches our emails
                # (reverse lookup - subscriber used alias, we're checking primary)
                subscriber_aliases = session.query(EmailAlias.alias_email).filter(
                    EmailAlias.primary_email.in_(emails_to_check)
                ).all()

                if subscriber_aliases:
                    subscriber_alias_emails = [a[0].lower() for a in subscriber_aliases]
                    reverse_match = session.query(UserPreference).filter(
                        UserPreference.receive_emails == True,
                        func.lower(UserPreference.user_email).in_(subscriber_alias_emails)
                    ).first()

                    if reverse_match:
                        logger.debug(f"Subscription match by reverse alias: {email} -> {reverse_match.user_email}")
                        return True

                # No match found
                logger.debug(f"No subscription found for {email} (user_id: {user_id})")
                return False

        except Exception as e:
            logger.error(f"Error getting user preference for {email}: {e}")
            # On error, default to NOT sending (fail-closed for non-subscribers)
            return False

    def set_user_preference(
        self,
        email: str,
        receive_emails: bool,
        updated_by: str = "user"
    ) -> bool:
        """
        Set user's email preference.

        Resolves email to user_id (GUID) and stores preference by GUID.
        This ensures stable identity matching across email aliases.

        Args:
            email: User email address
            receive_emails: True to receive emails, False to opt out
            updated_by: Who updated the preference ('user', 'organizer', 'admin')

        Returns:
            True if successfully saved, False if GUID resolution failed

        Creates new preference record if one doesn't exist.
        """
        try:
            email = email.lower().strip()

            # Resolve email to user_id (GUID)
            user_id, primary_email, display_name = self._get_or_resolve_user_id(email)

            if not user_id:
                logger.error(f"Cannot set preference for {email}: failed to resolve user_id")
                return False

            with self.db.get_session() as session:
                # Look up by user_id (GUID) - the primary key
                pref = session.query(UserPreference).filter_by(user_id=user_id).first()

                if pref:
                    # Update existing preference
                    pref.user_email = primary_email  # Update to current primary email
                    pref.display_name = display_name or pref.display_name
                    pref.receive_emails = receive_emails
                    pref.email_preference = 'all' if receive_emails else 'disabled'
                    pref.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    pref.updated_by = updated_by

                    logger.info(
                        f"Updated preference for {email} (user_id: {user_id}): "
                        f"receive_emails={receive_emails} (by {updated_by})"
                    )
                else:
                    # Create new preference
                    pref = UserPreference(
                        user_id=user_id,
                        user_email=primary_email,
                        display_name=display_name,
                        receive_emails=receive_emails,
                        email_preference='all' if receive_emails else 'disabled',
                        updated_by=updated_by
                    )
                    session.add(pref)

                    logger.info(
                        f"Created preference for {email} (user_id: {user_id}): "
                        f"receive_emails={receive_emails} (by {updated_by})"
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

        Resolves email to user_id and deletes by GUID.

        Args:
            email: User email address

        Returns:
            True if deleted successfully

        After deletion, user will receive default behavior (not subscribed).
        """
        try:
            email = email.lower().strip()

            # Try to find user_id from cache
            user_id = self._get_user_id(email)

            with self.db.get_session() as session:
                pref = None

                # First try to find by user_id (preferred)
                if user_id:
                    pref = session.query(UserPreference).filter_by(user_id=user_id).first()

                # Fallback: try by email (for legacy records)
                if not pref:
                    from sqlalchemy import func
                    pref = session.query(UserPreference).filter(
                        func.lower(UserPreference.user_email) == email
                    ).first()

                if pref:
                    session.delete(pref)
                    session.commit()
                    logger.info(f"Deleted preference for {email} (user_id: {user_id or 'N/A'})")
                    return True
                else:
                    logger.debug(f"No preference found for {email} to delete")
                    return False

        except Exception as e:
            logger.error(f"Error deleting preference for {email}: {e}", exc_info=True)
            return False

    # ========================================================================
    # MEETING-SPECIFIC PREFERENCES (NEW - Opt-in/opt-out system)
    # ========================================================================

    def get_meeting_preference(self, email: str, meeting_id: int) -> Optional[bool]:
        """
        Get user's preference for a specific meeting.

        Looks up by user_id (GUID) for stable matching.

        Args:
            email: User email address
            meeting_id: Meeting database ID

        Returns:
            True if user wants emails for this meeting
            False if user opted out of this meeting
            None if no per-meeting preference set (use global preference)

        Per-meeting preferences override global preferences.
        """
        try:
            email = email.lower().strip()

            # Get user_id from cache
            user_id = self._get_user_id(email)

            with self.db.get_session() as session:
                pref = None

                # First try by user_id (preferred)
                if user_id:
                    pref = session.query(MeetingPreference).filter_by(
                        user_id=user_id,
                        meeting_id=meeting_id
                    ).first()

                # Fallback: try by email (for legacy records)
                if not pref:
                    from sqlalchemy import func
                    pref = session.query(MeetingPreference).filter(
                        func.lower(MeetingPreference.user_email) == email,
                        MeetingPreference.meeting_id == meeting_id
                    ).first()

                if pref:
                    logger.debug(
                        f"Per-meeting preference for {email} in meeting {meeting_id}: "
                        f"receive_emails={pref.receive_emails}"
                    )
                    return pref.receive_emails

                logger.debug(f"No per-meeting preference for {email} in meeting {meeting_id}")
                return None  # No per-meeting preference set

        except Exception as e:
            logger.error(f"Error getting meeting preference for {email} in meeting {meeting_id}: {e}")
            return None

    def set_meeting_preference(
        self,
        email: str,
        meeting_id: int,
        receive_emails: bool,
        updated_by: str = "user"
    ) -> bool:
        """
        Set user's preference for a specific meeting.

        Resolves email to user_id (GUID) for stable identity matching.

        Args:
            email: User email address
            meeting_id: Meeting database ID
            receive_emails: True to receive emails, False to opt out
            updated_by: Who updated the preference ('user', 'organizer', 'system')

        Returns:
            True if successfully saved, False if GUID resolution failed

        Creates new preference record if one doesn't exist, updates if it does.
        Per-meeting preferences override global preferences.
        """
        try:
            email = email.lower().strip()

            # Resolve email to user_id (GUID)
            user_id, primary_email, _ = self._get_or_resolve_user_id(email)

            if not user_id:
                logger.error(f"Cannot set meeting preference for {email}: failed to resolve user_id")
                return False

            with self.db.get_session() as session:
                # Look up by user_id and meeting_id
                pref = session.query(MeetingPreference).filter_by(
                    user_id=user_id,
                    meeting_id=meeting_id
                ).first()

                if pref:
                    # Update existing preference
                    pref.user_email = primary_email  # Update to current email
                    pref.receive_emails = receive_emails
                    pref.updated_by = updated_by
                    pref.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

                    logger.info(
                        f"Updated per-meeting preference for {email} (user_id: {user_id}) "
                        f"in meeting {meeting_id}: receive_emails={receive_emails} (by {updated_by})"
                    )
                else:
                    # Create new preference
                    pref = MeetingPreference(
                        meeting_id=meeting_id,
                        user_id=user_id,
                        user_email=primary_email,
                        receive_emails=receive_emails,
                        updated_by=updated_by
                    )
                    session.add(pref)

                    logger.info(
                        f"Created per-meeting preference for {email} in meeting {meeting_id}: "
                        f"receive_emails={receive_emails} (by {updated_by})"
                    )

                session.commit()
                return True

        except Exception as e:
            logger.error(
                f"Error setting meeting preference for {email} in meeting {meeting_id}: {e}",
                exc_info=True
            )
            return False

    def should_send_email(self, email: str, meeting_id: int) -> bool:
        """
        Determine if user should receive email for this meeting using priority logic.

        Priority order (highest to lowest):
        1. Meeting-level distribution control (organizer can disable for entire meeting)
        2. Per-meeting user preference (user opts out of specific meeting)
        3. Global user preference (user opts out of all meetings)
        4. Default (opt-in - send emails)

        Args:
            email: User email address
            meeting_id: Meeting database ID

        Returns:
            True if user should receive email, False otherwise

        This is the main method to use when determining whether to send an email.
        It checks all preference levels in the correct priority order.
        """
        try:
            email = email.lower().strip()

            # 1. Check if organizer disabled distribution for this meeting
            with self.db.get_session() as session:
                meeting = session.query(Meeting).filter_by(id=meeting_id).first()

                if not meeting:
                    logger.warning(f"Meeting {meeting_id} not found, defaulting to opt-in")
                    return True

                if not meeting.distribution_enabled:
                    logger.info(
                        f"Distribution disabled for meeting {meeting_id} by organizer "
                        f"({meeting.distribution_disabled_by}), skipping {email}"
                    )
                    return False

            # 2. Check per-meeting preference (highest user priority)
            meeting_pref = self.get_meeting_preference(email, meeting_id)
            if meeting_pref is not None:
                logger.debug(
                    f"Using per-meeting preference for {email} in meeting {meeting_id}: "
                    f"{meeting_pref}"
                )
                return meeting_pref

            # 3. Check global preference
            global_pref = self.get_user_preference(email)
            logger.debug(
                f"Using global preference for {email}: {global_pref}"
            )
            return global_pref

            # Note: get_user_preference() returns True by default if no preference set
            # So we don't need an explicit "4. Default: opt-in" case

        except Exception as e:
            logger.error(
                f"Error checking if should send email to {email} for meeting {meeting_id}: {e}",
                exc_info=True
            )
            # On error, default to sending emails (fail-open)
            return True
