"""
Microsoft Graph API - Meeting Transcripts

Fetches meeting transcripts from Teams meetings using Microsoft Graph API.
Uses the getAllTranscripts API which works across all meetings for an organizer.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from ..graph.client import GraphAPIClient
from ..core.exceptions import TranscriptNotFoundError, GraphAPIError


logger = logging.getLogger(__name__)


class TranscriptFetcher:
    """
    Fetches meeting transcripts using Microsoft Graph API.

    New approach (works with OnlineMeetingTranscript.Read.All + Chat.Read.All):
    - List all transcripts: /users/{organizerId}/onlineMeetings/getAllTranscripts(meetingOrganizerUserId='{organizerId}')
    - Download content: /users/{organizerId}/onlineMeetings/{meetingId}/transcripts/{transcriptId}/content

    This approach:
    1. Gets ALL transcripts for a meeting organizer
    2. Can filter by meeting ID or time range
    3. Works even if you're just a participant (not organizer)

    Usage:
        client = GraphAPIClient(config, use_beta=True)  # Beta API required
        fetcher = TranscriptFetcher(client)

        # Get transcript for a specific organizer
        transcript = fetcher.get_transcript_for_organizer(
            organizer_user_id='...',
            meeting_id='...'
        )
    """

    def __init__(self, client: GraphAPIClient):
        """
        Initialize transcript fetcher.

        Args:
            client: GraphAPIClient instance (should use beta API)
        """
        self.client = client

    def get_all_transcripts_for_organizer(
        self,
        organizer_user_id: str,
        since_hours: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get ALL transcripts for meetings organized by a specific user.

        Args:
            organizer_user_id: User ID of the meeting organizer
            since_hours: Only return transcripts from last N hours (optional)

        Returns:
            List of transcript metadata dictionaries

        Raises:
            GraphAPIError: If request fails
        """
        try:
            endpoint = f"/users/{organizer_user_id}/onlineMeetings/getAllTranscripts(meetingOrganizerUserId='{organizer_user_id}')"

            logger.debug(f"Getting all transcripts for organizer {organizer_user_id}")

            result = self.client.get(endpoint)
            transcripts = result.get('value', [])

            # Filter by time if requested
            if since_hours and transcripts:
                from datetime import timezone
                cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
                transcripts = [
                    t for t in transcripts
                    if datetime.fromisoformat(t.get('createdDateTime', '').replace('Z', '+00:00')) > cutoff
                ]

            logger.info(f"Found {len(transcripts)} transcripts for organizer {organizer_user_id}")
            return transcripts

        except Exception as e:
            logger.error(f"Failed to get transcripts for organizer {organizer_user_id}: {e}")
            raise GraphAPIError(f"Failed to get organizer transcripts: {e}")

    def get_transcript_for_meeting(
        self,
        organizer_user_id: str,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the transcript for a specific meeting.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID (from getAllTranscripts, NOT calendar event ID)

        Returns:
            Transcript metadata dict if found, None otherwise
        """
        try:
            # Get all transcripts for this organizer
            all_transcripts = self.get_all_transcripts_for_organizer(organizer_user_id)

            # Find transcript(s) matching this meeting ID
            matching = [t for t in all_transcripts if t.get('meetingId') == meeting_id]

            if not matching:
                logger.info(f"No transcript found for meeting {meeting_id}")
                return None

            # Return the most recent transcript if multiple exist
            matching.sort(key=lambda t: t.get('createdDateTime', ''), reverse=True)
            transcript = matching[0]

            logger.info(f"Found transcript {transcript.get('id')} for meeting {meeting_id}")
            return transcript

        except Exception as e:
            logger.error(f"Error getting transcript for meeting {meeting_id}: {e}")
            return None

    def download_transcript_content(
        self,
        organizer_user_id: str,
        meeting_id: str,
        transcript_id: str
    ) -> str:
        """
        Download the actual VTT content of a transcript.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID
            transcript_id: The transcript ID

        Returns:
            VTT content as string

        Raises:
            TranscriptNotFoundError: If transcript not found
            GraphAPIError: If download fails
        """
        try:
            endpoint = f"/users/{organizer_user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
            params = {'$format': 'text/vtt'}

            logger.debug(f"Downloading transcript {transcript_id} for meeting {meeting_id}")

            # The content endpoint returns the raw VTT text (not JSON)
            content = self.client.get_text(endpoint, params=params)

            logger.info(f"Downloaded transcript {transcript_id}: {len(content)} bytes")
            return content

        except GraphAPIError as e:
            if '404' in str(e):
                raise TranscriptNotFoundError(f"Transcript {transcript_id} not found")
            logger.error(f"Failed to download transcript {transcript_id}: {e}")
            raise

    def get_transcript_with_metadata(
        self,
        organizer_user_id: str,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get transcript metadata AND content for a meeting.

        This is the main method to use - it returns everything you need.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID

        Returns:
            Dict with keys:
                - id: Transcript ID
                - meetingId: Meeting ID
                - createdDateTime: When transcript was created
                - content: VTT content string
                - contentUrl: URL to download content
            Returns None if no transcript found
        """
        try:
            # First, get the transcript metadata
            transcript = self.get_transcript_for_meeting(organizer_user_id, meeting_id)
            if not transcript:
                return None

            # Download the content
            content = self.download_transcript_content(
                organizer_user_id,
                transcript.get('meetingId'),
                transcript.get('id')
            )

            # Add content to metadata
            result = {
                'id': transcript.get('id'),
                'meetingId': transcript.get('meetingId'),
                'createdDateTime': transcript.get('createdDateTime'),
                'contentUrl': transcript.get('transcriptContentUrl'),
                'content': content
            }

            logger.info(
                f"Successfully fetched transcript for meeting {meeting_id}: "
                f"{len(content)} bytes"
            )

            return result

        except Exception as e:
            logger.error(f"Error fetching transcript with metadata: {e}")
            raise

    def find_transcript_by_time(
        self,
        organizer_user_id: str,
        meeting_start_time: datetime,
        tolerance_minutes: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Find a transcript by matching the meeting start time.

        Since calendar meeting IDs don't match transcript meeting IDs,
        we match by comparing the transcript createdDateTime with the
        meeting start time.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_start_time: When the meeting started (datetime object)
            tolerance_minutes: Allow this many minutes difference (default 30)

        Returns:
            Transcript metadata dict if found, None otherwise
        """
        try:
            # Get recent transcripts (last 72 hours)
            all_transcripts = self.get_all_transcripts_for_organizer(
                organizer_user_id,
                since_hours=72
            )

            # Find transcript created around the meeting start time
            from datetime import timezone
            for transcript in all_transcripts:
                created_str = transcript.get('createdDateTime', '')
                if not created_str:
                    continue

                created_dt = datetime.fromisoformat(created_str.replace('Z', '+00:00'))

                # Make meeting_start_time timezone-aware if it isn't
                # Database stores naive datetimes in UTC
                if meeting_start_time.tzinfo is None:
                    from datetime import timezone
                    meeting_start_time = meeting_start_time.replace(tzinfo=timezone.utc)

                # Check if times are within tolerance
                time_diff = abs((created_dt - meeting_start_time).total_seconds() / 60)

                if time_diff <= tolerance_minutes:
                    logger.info(
                        f"Found transcript {transcript.get('id')} created at {created_str} "
                        f"matching meeting at {meeting_start_time} (diff: {time_diff:.1f} min)"
                    )
                    return transcript

            logger.info(f"No transcript found for meeting at {meeting_start_time}")
            return None

        except Exception as e:
            logger.error(f"Error finding transcript by time: {e}")
            return None

    def find_transcript_by_thread_id(
        self,
        organizer_user_id: str,
        thread_id: str,
        since_hours: int = 72
    ) -> Optional[Dict[str, Any]]:
        """
        Find a transcript by matching the thread ID in the meeting ID.

        Meeting IDs contain the thread ID, e.g.:
        MSo...***19:meeting_XXX@thread.v2

        Args:
            organizer_user_id: User ID of the meeting organizer
            thread_id: Thread ID from calendar joinUrl (e.g., "19:meeting_XXX@thread.v2")
            since_hours: Only search transcripts from last N hours

        Returns:
            Transcript metadata dict if found, None otherwise
        """
        try:
            # Get recent transcripts
            all_transcripts = self.get_all_transcripts_for_organizer(
                organizer_user_id,
                since_hours=since_hours
            )

            # Find transcript with matching thread ID in meeting ID
            for transcript in all_transcripts:
                meeting_id = transcript.get('meetingId', '')
                if thread_id in meeting_id:
                    logger.info(
                        f"Found transcript {transcript.get('id')} matching thread {thread_id}"
                    )
                    return transcript

            logger.info(f"No transcript found matching thread {thread_id}")
            return None

        except Exception as e:
            logger.error(f"Error finding transcript by thread ID: {e}")
            return None

    def get_transcript_sharepoint_url(
        self,
        organizer_user_id: str,
        meeting_id: str
    ) -> Optional[str]:
        """
        Get SharePoint URL for transcript (respects permissions).

        This URL points to the transcript stored in SharePoint/OneDrive
        and automatically respects Teams meeting permissions.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID

        Returns:
            SharePoint URL string if found, None otherwise
        """
        try:
            transcript = self.get_transcript_for_meeting(organizer_user_id, meeting_id)
            if not transcript:
                return None

            url = transcript.get('transcriptContentUrl')
            if url:
                logger.info(f"Retrieved transcript SharePoint URL for meeting {meeting_id}")
            else:
                logger.warning(f"No transcriptContentUrl found for meeting {meeting_id}")

            return url

        except Exception as e:
            logger.error(f"Error getting transcript SharePoint URL: {e}")
            return None

    def get_all_recordings_for_organizer(
        self,
        organizer_user_id: str,
        since_hours: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get ALL recordings for meetings organized by a specific user.

        Args:
            organizer_user_id: User ID of the meeting organizer
            since_hours: Only return recordings from last N hours (optional)

        Returns:
            List of recording metadata dictionaries

        Raises:
            GraphAPIError: If request fails
        """
        try:
            endpoint = f"/users/{organizer_user_id}/onlineMeetings/getAllRecordings(meetingOrganizerUserId='{organizer_user_id}')"

            logger.debug(f"Getting all recordings for organizer {organizer_user_id}")

            result = self.client.get(endpoint)
            recordings = result.get('value', [])

            # Filter by time if requested
            if since_hours and recordings:
                from datetime import timezone
                cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
                recordings = [
                    r for r in recordings
                    if datetime.fromisoformat(r.get('createdDateTime', '').replace('Z', '+00:00')) > cutoff
                ]

            logger.info(f"Found {len(recordings)} recordings for organizer {organizer_user_id}")
            return recordings

        except Exception as e:
            logger.error(f"Failed to get recordings for organizer {organizer_user_id}: {e}")
            raise GraphAPIError(f"Failed to get organizer recordings: {e}")

    def get_recording_for_meeting(
        self,
        organizer_user_id: str,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the recording for a specific meeting.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID

        Returns:
            Recording metadata dict if found, None otherwise
        """
        try:
            # Get all recordings for this organizer (last 72 hours)
            all_recordings = self.get_all_recordings_for_organizer(
                organizer_user_id,
                since_hours=72
            )

            # Find recording(s) matching this meeting ID
            matching = [r for r in all_recordings if r.get('meetingId') == meeting_id]

            if not matching:
                logger.info(f"No recording found for meeting {meeting_id}")
                return None

            # Return the most recent recording if multiple exist
            matching.sort(key=lambda r: r.get('createdDateTime', ''), reverse=True)
            recording = matching[0]

            logger.info(f"Found recording {recording.get('id')} for meeting {meeting_id}")
            return recording

        except Exception as e:
            logger.error(f"Error getting recording for meeting {meeting_id}: {e}")
            return None

    def get_recording_sharepoint_url(
        self,
        organizer_user_id: str,
        meeting_id: str
    ) -> Optional[str]:
        """
        Get SharePoint URL for recording (respects permissions).

        This URL points to the recording stored in SharePoint/OneDrive
        and automatically respects Teams meeting permissions.

        Args:
            organizer_user_id: User ID of the meeting organizer
            meeting_id: The onlineMeeting ID

        Returns:
            SharePoint URL string if found, None otherwise
        """
        try:
            recording = self.get_recording_for_meeting(organizer_user_id, meeting_id)
            if not recording:
                return None

            url = recording.get('recordingContentUrl')
            if url:
                logger.info(f"Retrieved recording SharePoint URL for meeting {meeting_id}")
            else:
                logger.warning(f"No recordingContentUrl found for meeting {meeting_id}")

            return url

        except Exception as e:
            logger.error(f"Error getting recording SharePoint URL: {e}")
            return None

    def check_transcript_readiness_from_chat(
        self,
        chat_id: str
    ) -> Dict[str, Any]:
        """
        Check chat events to determine transcript readiness.

        This method queries the meeting chat for system events:

        1. callTranscriptEventMessageDetail - Posted when transcript IS AVAILABLE
           (not when transcription starts, but when it's ready to download)

        2. callRecordingEventMessageDetail (success) - Posted when recording STARTS
           (indicates transcription was enabled, but transcript isn't ready yet)

        This allows us to:
        - Skip retries immediately if no recording event (transcription never enabled)
        - Use VERY aggressive polling if transcript event seen (it's ready now!)
        - Use moderate polling if only recording event seen (transcript coming soon)

        Args:
            chat_id: The meeting chat thread ID (e.g., "19:meeting_xxx@thread.v2")

        Returns:
            Dict with keys:
                - transcript_available: bool - True if transcript IS READY (from transcript event)
                - recording_started: bool - True if recording was started (transcription enabled)
                - transcript_posted_time: str or None - When transcript became available
                - error: str or None - Error message if query failed
        """
        result = {
            "transcript_available": False,
            "recording_started": False,
            "transcript_posted_time": None,
            "error": None
        }

        # Validate input
        if not chat_id:
            result["error"] = "No chat_id provided"
            logger.warning("check_transcript_readiness_from_chat called with empty chat_id")
            return result

        try:
            # Query recent chat messages
            messages = self.client.get(
                f"/chats/{chat_id}/messages",
                params={
                    "$top": 30,
                    "$orderby": "createdDateTime desc"
                }
            )

            # Handle case where API returns None or unexpected response
            if not messages or not isinstance(messages, dict):
                result["error"] = "No response from chat API"
                return result

            for msg in messages.get("value", []):
                # Handle case where eventDetail is explicitly None (not just missing)
                event = msg.get("eventDetail") or {}
                event_type = event.get("@odata.type", "")

                # callTranscriptEventMessageDetail = Transcript IS AVAILABLE NOW
                # This is the key signal - posted when transcript is ready to download
                if "callTranscriptEventMessageDetail" in event_type:
                    result["transcript_available"] = True
                    result["transcript_posted_time"] = msg.get("createdDateTime")
                    logger.debug(f"Found transcript available event at {result['transcript_posted_time']}")

                # callRecordingEventMessageDetail (success) = Recording STARTED
                # This means transcription was enabled, but transcript isn't ready yet
                if "callRecordingEventMessageDetail" in event_type:
                    if event.get("callRecordingStatus") == "success":
                        result["recording_started"] = True
                        logger.debug("Found recording started event - transcription was enabled")

            logger.info(
                f"Chat events check for {chat_id}: "
                f"transcript_available={result['transcript_available']}, "
                f"recording_started={result['recording_started']}"
            )

            return result

        except Exception as e:
            logger.error(f"Error checking chat events for {chat_id}: {e}")
            result["error"] = str(e)
            return result
