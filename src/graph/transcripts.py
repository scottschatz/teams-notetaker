"""
Microsoft Graph API - Meeting Transcripts

Fetches meeting transcripts from Teams meetings using Microsoft Graph API.
Handles transcript availability checking and VTT content download.
"""

import logging
from typing import Optional, Dict, Any, List
import requests

from ..graph.client import GraphAPIClient
from ..core.exceptions import TranscriptNotFoundError, GraphAPIError


logger = logging.getLogger(__name__)


class TranscriptFetcher:
    """
    Fetches meeting transcripts using Microsoft Graph API.

    Graph API endpoints for transcripts:
    - List transcripts: /users/{userId}/onlineMeetings/{meetingId}/transcripts
    - Get transcript: /users/{userId}/onlineMeetings/{meetingId}/transcripts/{transcriptId}
    - Download content: /users/{userId}/onlineMeetings/{meetingId}/transcripts/{transcriptId}/content

    Usage:
        client = GraphAPIClient(config)
        fetcher = TranscriptFetcher(client)

        # Check if transcript exists
        if fetcher.has_transcript(meeting_id, user_id):
            # Download VTT content
            vtt_content = fetcher.get_transcript_content(meeting_id, user_id)
    """

    def __init__(self, client: GraphAPIClient):
        """
        Initialize transcript fetcher.

        Args:
            client: GraphAPIClient instance
        """
        self.client = client

    def has_transcript(self, meeting_id: str, user_id: str) -> bool:
        """
        Check if a meeting has a transcript available.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID (organizer or participant)

        Returns:
            True if transcript exists
        """
        try:
            transcripts = self.list_transcripts(meeting_id, user_id)
            return len(transcripts) > 0
        except Exception as e:
            logger.debug(f"No transcript found for meeting {meeting_id}: {e}")
            return False

    def list_transcripts(self, meeting_id: str, user_id: str) -> List[Dict[str, Any]]:
        """
        List all transcripts for a meeting.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID (organizer or participant)

        Returns:
            List of transcript metadata dictionaries

        Raises:
            GraphAPIError: If request fails
        """
        try:
            endpoint = f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"

            logger.debug(f"Listing transcripts for meeting {meeting_id}")

            response = self.client.get(endpoint)
            transcripts = response.get("value", [])

            logger.info(f"Found {len(transcripts)} transcript(s) for meeting {meeting_id}")

            return transcripts

        except Exception as e:
            logger.error(f"Failed to list transcripts for meeting {meeting_id}: {e}")
            raise GraphAPIError(f"Failed to list transcripts: {e}")

    def get_transcript_metadata(
        self,
        meeting_id: str,
        user_id: str,
        transcript_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get transcript metadata.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID
            transcript_id: Transcript ID (if None, gets the first/latest transcript)

        Returns:
            Transcript metadata dictionary with:
                - id: Transcript ID
                - meetingId: Meeting ID
                - createdDateTime: When transcript was created
                - contentUrl: URL to download content

        Raises:
            TranscriptNotFoundError: If no transcript found
        """
        try:
            if transcript_id is None:
                # Get first available transcript
                transcripts = self.list_transcripts(meeting_id, user_id)
                if not transcripts:
                    raise TranscriptNotFoundError(f"No transcript found for meeting {meeting_id}")

                # Use the most recent transcript (they're usually ordered by creation time)
                transcript = transcripts[-1]
                transcript_id = transcript["id"]
            else:
                # Get specific transcript
                endpoint = f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}"
                transcript = self.client.get(endpoint)

            logger.debug(f"Retrieved transcript metadata: {transcript_id}")

            return transcript

        except TranscriptNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get transcript metadata: {e}")
            raise GraphAPIError(f"Failed to fetch transcript metadata: {e}")

    def get_transcript_content(
        self,
        meeting_id: str,
        user_id: str,
        transcript_id: Optional[str] = None
    ) -> str:
        """
        Download transcript content (VTT format).

        Args:
            meeting_id: Online meeting ID
            user_id: User ID
            transcript_id: Transcript ID (if None, gets the first/latest transcript)

        Returns:
            VTT content as string

        Raises:
            TranscriptNotFoundError: If no transcript found
            GraphAPIError: If download fails
        """
        try:
            # Get transcript metadata to get content URL
            metadata = self.get_transcript_metadata(meeting_id, user_id, transcript_id)

            if transcript_id is None:
                transcript_id = metadata["id"]

            logger.info(f"Downloading transcript content for {meeting_id}/{transcript_id}")

            # Download content using Graph API
            endpoint = f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"

            # The content endpoint returns VTT directly (not JSON)
            # Use _request to get raw response
            response = self.client._request("GET", endpoint)

            # Check content type
            content_type = response.headers.get("Content-Type", "")
            if "text/vtt" not in content_type and "text/plain" not in content_type:
                logger.warning(f"Unexpected content type: {content_type}")

            vtt_content = response.text

            logger.info(
                f"✓ Downloaded transcript: {len(vtt_content)} chars "
                f"({len(vtt_content.splitlines())} lines)"
            )

            return vtt_content

        except TranscriptNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to download transcript content: {e}")
            raise GraphAPIError(f"Transcript download failed: {e}")

    def get_transcript_with_metadata(
        self,
        meeting_id: str,
        user_id: str,
        transcript_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get both transcript content and metadata in one call.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID
            transcript_id: Transcript ID (optional)

        Returns:
            Dictionary with:
                - metadata: Transcript metadata
                - content: VTT content string
                - content_url: URL where content was downloaded from

        Raises:
            TranscriptNotFoundError: If no transcript found
        """
        try:
            metadata = self.get_transcript_metadata(meeting_id, user_id, transcript_id)
            content = self.get_transcript_content(meeting_id, user_id, metadata["id"])

            return {
                "metadata": metadata,
                "content": content,
                "content_url": metadata.get("contentUrl", ""),
                "transcript_id": metadata["id"],
                "created_at": metadata.get("createdDateTime", "")
            }

        except Exception as e:
            logger.error(f"Failed to get transcript with metadata: {e}")
            raise

    def wait_for_transcript(
        self,
        meeting_id: str,
        user_id: str,
        max_attempts: int = 10,
        wait_seconds: int = 30
    ) -> Optional[str]:
        """
        Wait for transcript to become available (for recently ended meetings).

        Teams takes a few minutes to generate transcripts after meeting ends.

        Args:
            meeting_id: Online meeting ID
            user_id: User ID
            max_attempts: Maximum polling attempts
            wait_seconds: Seconds to wait between attempts

        Returns:
            Transcript ID if found, None if timeout

        Raises:
            GraphAPIError: If polling fails
        """
        import time

        logger.info(f"Waiting for transcript to become available (max {max_attempts} attempts)")

        for attempt in range(1, max_attempts + 1):
            try:
                transcripts = self.list_transcripts(meeting_id, user_id)
                if transcripts:
                    transcript_id = transcripts[-1]["id"]
                    logger.info(f"✓ Transcript available after {attempt} attempt(s)")
                    return transcript_id
            except Exception as e:
                logger.debug(f"Attempt {attempt}/{max_attempts}: {e}")

            if attempt < max_attempts:
                logger.debug(f"Transcript not ready, waiting {wait_seconds}s...")
                time.sleep(wait_seconds)

        logger.warning(f"Transcript not available after {max_attempts} attempts")
        return None
