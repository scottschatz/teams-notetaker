"""
Microsoft Graph API - Meeting Discovery

Discovers Teams meetings organization-wide using Microsoft Graph API.
Handles user calendar queries and online meeting metadata retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import quote

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

        start_time = datetime.now() - timedelta(hours=hours_back)
        start_time_iso = start_time.isoformat() + "Z"

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
            end_time = datetime.now().isoformat() + "Z"

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
            "join_url": online_meeting.get("joinUrl", ""),
            "has_transcript": None,  # Unknown until we check
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

        Args:
            email: User's email address

        Returns:
            User ID (GUID) or None if not found
        """
        try:
            endpoint = f"/users/{email}"
            params = {'$select': 'id,displayName'}
            user = self.client.get(endpoint, params=params)
            return user.get('id')
        except Exception as e:
            logger.error(f"Failed to get user ID for {email}: {e}")
            return None

    def _parse_datetime(self, dt_string: Optional[str]) -> Optional[datetime]:
        """
        Parse ISO datetime string to datetime object.

        Args:
            dt_string: ISO format datetime string

        Returns:
            datetime object or None
        """
        if not dt_string:
            return None

        try:
            # Handle both with and without timezone
            if dt_string.endswith("Z"):
                return datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(dt_string)
        except Exception as e:
            logger.warning(f"Failed to parse datetime '{dt_string}': {e}")
            return None
