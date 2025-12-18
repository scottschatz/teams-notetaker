"""
Microsoft Graph API - Meeting Discovery

Discovers Teams meetings organization-wide using Microsoft Graph API.
Handles user calendar queries and online meeting metadata retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote
import re

from ..graph.client import GraphAPIClient
from ..core.exceptions import GraphAPIError, MeetingNotFoundError


logger = logging.getLogger(__name__)


class MeetingDiscovery:
    """
    Discovers Teams meetings using Microsoft Graph API.

    Note: Graph API doesn't have a direct "all org meetings" endpoint.
    Discovery strategies:
    1. Query users' calendars (requires User.Read.All, Calendars.Read)
    2. Query for online meetings (OnlineMeetings.Read.All)
    3. Use change notifications/webhooks for real-time discovery

    This implementation uses online meetings API with filtering.

    Usage:
        client = GraphAPIClient(config)
        discovery = MeetingDiscovery(client)

        # Discover meetings in last 48 hours
        meetings = discovery.discover_meetings(hours_back=48)
    """

    def __init__(self, client: GraphAPIClient):
        """
        Initialize meeting discovery.

        Args:
            client: GraphAPIClient instance
        """
        self.client = client

    def discover_meetings(
        self,
        hours_back: int = 48,
        include_recurring: bool = True,
        user_emails: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover Teams meetings from the last N hours.

        Args:
            hours_back: How many hours to look back (default 48)
            include_recurring: Include recurring meetings (default True)
            user_emails: Optional list of specific user emails to query
                        (if None, attempts org-wide discovery)

        Returns:
            List of meeting dictionaries with standardized format

        Note: This is a placeholder implementation. Actual discovery requires
        either:
        1. Admin consent for org-wide calendars access
        2. Service account with delegated access
        3. Iterating through users or using change notifications
        """
        logger.info(f"Discovering meetings from last {hours_back} hours")

        start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        start_time_iso = start_time.isoformat()

        meetings = []

        if user_emails:
            # Query specific users' calendars
            for email in user_emails:
                try:
                    user_meetings = self._get_user_meetings(email, start_time_iso)
                    meetings.extend(user_meetings)
                except Exception as e:
                    logger.error(f"Failed to get meetings for {email}: {e}")
        else:
            # Attempt org-wide discovery
            # NOTE: This requires specific admin permissions
            logger.warning(
                "Org-wide meeting discovery not yet implemented. "
                "Need to implement user iteration or webhook-based discovery."
            )
            # TODO: Implement one of:
            # - Iterate through all users (if user list is available)
            # - Use Graph change notifications with onlineMeetings resource
            # - Query specific shared calendars

        logger.info(f"Discovered {len(meetings)} meetings")
        return meetings

    def _get_user_meetings(
        self,
        user_email: str,
        start_time: str,
        end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get meetings for a specific user.

        Args:
            user_email: User's email address
            start_time: Start time (ISO format with Z)
            end_time: End time (ISO format with Z, default=now)

        Returns:
            List of meeting dictionaries
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc).isoformat()

        logger.debug(f"Fetching meetings for {user_email} from {start_time} to {end_time}")

        # Use calendarView to get meetings in time range
        # Note: Cannot filter by isOnlineMeeting in API, so we get all events and filter client-side
        endpoint = f"/users/{user_email}/calendarView"
        params = {
            "startDateTime": start_time,
            "endDateTime": end_time,
            "$select": "id,subject,start,end,organizer,attendees,onlineMeeting,isOnlineMeeting",
            "$top": 50
        }

        try:
            events = self.client.get_paged(endpoint, params=params, max_pages=10)

            meetings = []
            for event in events:
                # Filter for online meetings (Teams meetings only)
                if event.get('isOnlineMeeting') and event.get('onlineMeeting'):
                    meeting = self._parse_meeting_event(event)
                    if meeting:
                        meetings.append(meeting)

            logger.debug(f"Found {len(meetings)} online meetings for {user_email}")
            return meetings

        except Exception as e:
            logger.error(f"Failed to fetch meetings for {user_email}: {e}")
            raise GraphAPIError(f"Failed to get user meetings: {e}")

    def get_meeting_details(self, meeting_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific meeting.

        Args:
            meeting_id: Meeting ID (Graph event ID or onlineMeeting ID)

        Returns:
            Meeting details dictionary

        Raises:
            MeetingNotFoundError: If meeting not found
        """
        try:
            logger.debug(f"Fetching details for meeting {meeting_id}")

            # Try as calendar event first
            try:
                # Need to know which user's calendar - try as onlineMeeting ID instead
                endpoint = f"/me/onlineMeetings/{meeting_id}"
                meeting_data = self.client.get(endpoint)
                return self._parse_online_meeting(meeting_data)
            except:
                # Try as event ID (requires user context)
                pass

            raise MeetingNotFoundError(f"Meeting {meeting_id} not found")

        except GraphAPIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get meeting details: {e}")
            raise GraphAPIError(f"Failed to fetch meeting: {e}")

    def get_meeting_participants(self, meeting_id: str) -> List[Dict[str, str]]:
        """
        Get list of participants for a meeting.

        Args:
            meeting_id: Meeting ID

        Returns:
            List of participant dictionaries with email, displayName, role
        """
        try:
            meeting = self.get_meeting_details(meeting_id)
            return meeting.get("participants", [])
        except Exception as e:
            logger.error(f"Failed to get participants for meeting {meeting_id}: {e}")
            return []

    def _parse_meeting_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse Graph API calendar event into standardized meeting format.

        Args:
            event: Raw event data from Graph API

        Returns:
            Standardized meeting dictionary or None if not a valid Teams meeting
        """
        if not event.get("isOnlineMeeting"):
            return None

        online_meeting = event.get("onlineMeeting", {})

        # Parse participants
        participants = []

        # Add organizer
        organizer = event.get("organizer", {}).get("emailAddress", {})
        if organizer.get("address"):
            participants.append({
                "email": organizer["address"],
                "display_name": organizer.get("name", organizer["address"]),
                "role": "organizer"
            })

        # Add attendees
        for attendee in event.get("attendees", []):
            email_addr = attendee.get("emailAddress", {})
            if email_addr.get("address"):
                participants.append({
                    "email": email_addr["address"],
                    "display_name": email_addr.get("name", email_addr["address"]),
                    "role": attendee.get("type", "attendee")
                })

        # Parse times
        start = event.get("start", {})
        end = event.get("end", {})

        start_time = self._parse_datetime(start.get("dateTime"))
        end_time = self._parse_datetime(end.get("dateTime"))

        duration_minutes = 0
        if start_time and end_time:
            duration = end_time - start_time
            duration_minutes = int(duration.total_seconds() / 60)

        # Get organizer user ID (needed for transcript API)
        organizer_user_id = None
        organizer_email = organizer.get("address", "")
        if organizer_email:
            try:
                organizer_user_id = self._get_user_id(organizer_email)
            except Exception as e:
                logger.warning(f"Could not get user ID for organizer {organizer_email}: {e}")

        # Extract chat_id from join_url (v2.0 feature for chat posting)
        join_url = online_meeting.get("joinUrl", "")
        chat_id = self._extract_chat_id_from_url(join_url) if join_url else None

        # Try to get actual meeting stats from call records (v2.1 feature)
        call_record = self.get_call_record(start_time, organizer_user_id) if start_time and organizer_user_id else None

        # Use actual stats if available, otherwise use scheduled
        if call_record:
            actual_start = call_record["start_time"]
            actual_end = call_record["end_time"]
            actual_duration = call_record["duration_minutes"]
            actual_participants = call_record["participants"]
            actual_count = call_record["participant_count"]

            logger.info(f"Using actual stats: {actual_duration} min (scheduled: {duration_minutes}), "
                       f"{actual_count} joined (invited: {len(participants)})")

            return {
                "meeting_id": online_meeting.get("id", event["id"]),
                "event_id": event["id"],
                "subject": event.get("subject", "No Subject"),
                "organizer_email": organizer_email,
                "organizer_name": organizer.get("name", ""),
                "organizer_user_id": organizer_user_id,
                "start_time": actual_start,  # Actual
                "end_time": actual_end,  # Actual
                "duration_minutes": actual_duration,  # Actual
                "participant_count": actual_count,  # Actual
                "participants": actual_participants,  # Actual attendees
                "invited_participants": participants,  # Keep original for reference
                "invited_count": len(participants),  # Keep scheduled count
                "scheduled_duration": duration_minutes,  # Keep scheduled duration
                "join_url": join_url,
                "chat_id": chat_id,
                "has_transcript": None,
                "call_record_id": call_record.get("call_record_id"),
            }
        else:
            logger.debug("No call record found, using scheduled stats")

            return {
                "meeting_id": online_meeting.get("id", event["id"]),
                "event_id": event["id"],
                "subject": event.get("subject", "No Subject"),
                "organizer_email": organizer_email,
                "organizer_name": organizer.get("name", ""),
                "organizer_user_id": organizer_user_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration_minutes": duration_minutes,
                "participant_count": len(participants),
                "participants": participants,
                "join_url": join_url,
                "chat_id": chat_id,
                "has_transcript": None,
            }

    def _parse_online_meeting(self, meeting: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Graph API onlineMeeting object.

        Args:
            meeting: Raw onlineMeeting data

        Returns:
            Standardized meeting dictionary
        """
        start_time = self._parse_datetime(meeting.get("startDateTime"))
        end_time = self._parse_datetime(meeting.get("endDateTime"))

        duration_minutes = 0
        if start_time and end_time:
            duration = end_time - start_time
            duration_minutes = int(duration.total_seconds() / 60)

        return {
            "meeting_id": meeting["id"],
            "subject": meeting.get("subject", "No Subject"),
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": duration_minutes,
            "join_url": meeting.get("joinUrl", ""),
            "participants": [],  # onlineMeeting API doesn't include participants
            "has_transcript": None
        }

    def _get_user_id(self, email: str) -> Optional[str]:
        """
        Get user ID from email address.
        Tries multiple email formats if initial lookup fails.

        Args:
            email: User's email address

        Returns:
            User ID (GUID) or None if not found
        """
        # Try original email (lowercased)
        normalized_email = email.lower()

        # Generate alternate email formats to try
        # Example: "Scott.Schatz@domain.com" -> "sschatz@domain.com"
        alternate_emails = [normalized_email]

        if '.' in normalized_email.split('@')[0]:
            # Try without the dot: "scott.schatz@domain.com" -> "sschatz@domain.com"
            local_part, domain = normalized_email.split('@')
            parts = local_part.split('.')
            if len(parts) == 2:
                # Take first letter of first name + last name
                alternate = f"{parts[0][0]}{parts[1]}@{domain}"
                alternate_emails.append(alternate)

        # Try each email format
        for attempt_email in alternate_emails:
            try:
                endpoint = f"/users/{attempt_email}"
                params = {'$select': 'id,displayName,userPrincipalName'}
                user = self.client.get(endpoint, params=params)
                user_id = user.get('id')

                if user_id:
                    upn = user.get('userPrincipalName', attempt_email)
                    if attempt_email != normalized_email:
                        logger.info(f"Found user ID for {email} using alternate format: {upn}")
                    return user_id
            except Exception as e:
                # Try next format
                if attempt_email == alternate_emails[-1]:
                    # Last attempt failed
                    logger.error(f"Failed to get user ID for {email} (tried {len(alternate_emails)} formats): {e}")
                continue

        return None

    def _parse_datetime(self, dt_string: Optional[str]) -> Optional[datetime]:
        """
        Parse ISO datetime string to UTC-naive datetime object.

        Graph API returns times in UTC (with Z suffix). We store as UTC-naive
        datetimes, and the display layer converts to Eastern timezone.

        Args:
            dt_string: ISO format datetime string

        Returns:
            UTC-naive datetime object or None
        """
        if not dt_string:
            return None

        try:
            # Handle both with and without timezone
            if dt_string.endswith("Z"):
                # Parse UTC time and return as naive (strip timezone info)
                dt = datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
                return dt.replace(tzinfo=None)  # Store as UTC-naive
            elif "+" in dt_string or "-" in dt_string[10:]:
                # Has timezone offset - parse and convert to UTC-naive
                dt = datetime.fromisoformat(dt_string)
                if dt.tzinfo:
                    from datetime import timezone
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            else:
                # No timezone - assume already UTC-naive
                return datetime.fromisoformat(dt_string)
        except Exception as e:
            logger.warning(f"Failed to parse datetime '{dt_string}': {e}")
            return None

    def _extract_chat_id_from_url(self, join_url: str) -> Optional[str]:
        """
        Extract Teams chat ID from meeting join URL.

        Args:
            join_url: Teams meeting join URL

        Returns:
            Chat ID (e.g., "19:meeting_xxx@thread.v2") or None

        Note: Graph API doesn't always return chatId in onlineMeeting object,
        but we can extract it from the joinUrl for v2.0 chat posting feature.
        """
        if not join_url:
            return None

        try:
            # Pattern: meetup-join/{encoded_chat_id}/
            match = re.search(r'meetup-join/([^/]+)', join_url)
            if match:
                encoded_chat_id = match.group(1)
                # URL decode it
                chat_id = unquote(encoded_chat_id)
                logger.debug(f"Extracted chat_id from join URL: {chat_id}")
                return chat_id
        except Exception as e:
            logger.warning(f"Failed to extract chat_id from URL: {e}")

        return None

    def get_call_record(self, meeting_start_time: datetime, organizer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get call record for a meeting using time-based matching.

        Since call records don't have direct links to meetings, we match by:
        - Meeting start time (±5 minute window)
        - Organizer user ID (from sessions)

        Call records provide:
        - Actual start/end times (when people joined/left)
        - List of participants who actually joined (including silent attendees)
        - Individual participant session durations

        Requires: CallRecords.Read.All permission

        Args:
            meeting_start_time: Scheduled meeting start time
            organizer_id: Microsoft Graph user ID of meeting organizer

        Returns:
            Dictionary with call record data:
            {
                'start_time': datetime,
                'end_time': datetime,
                'duration_minutes': int,
                'participants': List[Dict],  # Who actually joined
                'participant_count': int,
                'match_confidence': str  # 'high', 'medium', 'low'
            }
            Returns None if no call record found.
        """
        try:
            if not meeting_start_time or not organizer_id:
                return None

            logger.debug(f"Searching for call record matching meeting at {meeting_start_time}...")

            # Get recent call records
            # Note: Call Records API doesn't support filtering
            # We'll search through recent records
            endpoint = "/communications/callRecords"

            try:
                response = self.client.get(endpoint)
            except GraphAPIError as e:
                if "404" in str(e) or "NotFound" in str(e):
                    logger.debug(f"No call records found (may not have started yet)")
                    return None
                elif "401" in str(e) or "403" in str(e):
                    logger.warning(f"Permission denied accessing call records - ensure CallRecords.Read.All is granted")
                    return None
                else:
                    raise

            records = response.get("value", [])

            if not records:
                logger.debug("No call records found")
                return None

            # Match by time window (±5 minutes)
            TIME_WINDOW_SECONDS = 300  # 5 minutes

            # Ensure meeting_start_time is timezone-aware
            if meeting_start_time.tzinfo is None:
                from datetime import timezone
                meeting_start_time = meeting_start_time.replace(tzinfo=timezone.utc)

            logger.debug(f"Searching {len(records)} call records for match within {TIME_WINDOW_SECONDS/60} min of {meeting_start_time}")

            best_match = None
            best_time_diff = float('inf')
            best_participant_overlap = 0
            match_confidence = 'low'

            for record in records:
                record_start_str = record.get('startDateTime')
                if not record_start_str:
                    continue

                # Parse call record start time
                record_start = datetime.fromisoformat(record_start_str.replace('Z', '+00:00'))

                # Calculate time difference
                time_diff_seconds = abs((record_start - meeting_start_time).total_seconds())

                # Within time window?
                if time_diff_seconds <= TIME_WINDOW_SECONDS:
                    # Fetch sessions to check participant overlap
                    participant_overlap_score = 0
                    try:
                        call_record_id = record.get('id')
                        if call_record_id:
                            sessions_response = self.client.get(f"/communications/callRecords/{call_record_id}/sessions")
                            sessions = sessions_response.get("value", [])

                            # Extract participant IDs from call record
                            call_participant_ids = set()
                            for session in sessions:
                                for participant in [session.get('caller'), session.get('callee')]:
                                    if participant:
                                        user = participant.get('identity', {}).get('user', {})
                                        user_id = user.get('id')
                                        if user_id:
                                            call_participant_ids.add(user_id)

                            # Check overlap with organizer (most reliable check)
                            if organizer_id in call_participant_ids:
                                participant_overlap_score = 100  # Organizer match is strong signal
                            elif len(call_participant_ids) > 0:
                                participant_overlap_score = 50  # Has participants but organizer not found
                    except Exception as e:
                        logger.debug(f"Could not verify participants for call record: {e}")
                        participant_overlap_score = 0

                    # Score this match based on time proximity + participant overlap
                    # Better time = higher score, participant match = bonus
                    match_score = (300 - time_diff_seconds) + participant_overlap_score

                    # Check if this is the best match so far
                    best_score = (300 - best_time_diff) + best_participant_overlap if best_match else 0

                    if match_score > best_score:
                        best_match = record
                        best_time_diff = time_diff_seconds
                        best_participant_overlap = participant_overlap_score

                        # Determine confidence based on time difference AND participant overlap
                        if participant_overlap_score >= 100:
                            # Organizer match + good time = high confidence
                            if time_diff_seconds < 180:
                                match_confidence = 'high'
                            else:
                                match_confidence = 'medium'
                        elif time_diff_seconds < 60:  # Within 1 minute but no participant check
                            match_confidence = 'medium'
                        elif time_diff_seconds < 180:  # Within 3 minutes but no participant check
                            match_confidence = 'low'
                        else:
                            match_confidence = 'low'

            if not best_match:
                logger.debug(f"No call record found within {TIME_WINDOW_SECONDS/60} min window")
                return None

            call_record = best_match

            # Log match details
            organizer_matched = "✓ organizer matched" if best_participant_overlap >= 100 else "organizer not verified"
            logger.info(f"Found call record match (confidence: {match_confidence}, time diff: {int(best_time_diff)}s, {organizer_matched})")

            # Extract actual times
            start_time_str = call_record.get("startDateTime")
            end_time_str = call_record.get("endDateTime")

            if not start_time_str or not end_time_str:
                logger.warning("Call record missing start/end times")
                return None

            # Parse ISO timestamps
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))

            # Calculate actual duration
            duration = end_time - start_time
            duration_minutes = int(duration.total_seconds() / 60)

            # Fetch sessions separately (since $expand not allowed)
            call_record_id = call_record.get("id")
            participants = []
            unique_participant_ids = set()

            if call_record_id:
                try:
                    sessions_endpoint = f"/communications/callRecords/{call_record_id}/sessions"
                    sessions_response = self.client.get(sessions_endpoint)
                    sessions = sessions_response.get("value", [])

                    for session in sessions:
                        caller = session.get("caller", {})
                        callee = session.get("callee", {})

                        for participant in [caller, callee]:
                            if not participant:
                                continue

                            identity = participant.get("identity", {})
                            user_info = identity.get("user", {})

                            if not user_info:
                                continue

                            user_id = user_info.get("id")
                            if user_id and user_id not in unique_participant_ids:
                                unique_participant_ids.add(user_id)

                                # Call record sessions only have user ID and displayName, NOT email
                                # Must look up each user in Graph API to get their email
                                email = user_info.get("userPrincipalName", "")
                                if not email:
                                    try:
                                        user_details = self.client.get(f"/users/{user_id}")
                                        email = user_details.get("mail") or user_details.get("userPrincipalName", "")
                                        logger.debug(f"Looked up user {user_id}: {email}")
                                    except Exception as e:
                                        logger.warning(f"Could not look up email for user {user_id}: {e}")
                                        # Still add participant but without email
                                        email = ""

                                participants.append({
                                    "id": user_id,
                                    "display_name": user_info.get("displayName", "Unknown"),
                                    "email": email,
                                })
                except Exception as e:
                    logger.warning(f"Could not fetch sessions for call record: {e}")
                    # Continue without participant details

            logger.info(f"✓ Call record found: {duration_minutes} min, {len(participants)} participants (confidence: {match_confidence})")

            return {
                "start_time": start_time,
                "end_time": end_time,
                "duration_minutes": duration_minutes,
                "participants": participants,
                "participant_count": len(participants),
                "call_record_id": call_record.get("id"),
                "match_confidence": match_confidence,
            }

        except GraphAPIError as e:
            logger.warning(f"Error fetching call record: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching call record: {e}", exc_info=True)
            return None
