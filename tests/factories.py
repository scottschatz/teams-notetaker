"""
Test data factories for generating realistic Graph API responses.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
import uuid


class GraphAPITestFactory:
    """Factory for creating realistic Graph API test data."""

    @staticmethod
    def create_call_record(
        start_hours_ago: int = 24,
        duration_minutes: int = 60,
        organizer_email: str = "organizer@example.com",
        participants: Optional[List[Dict]] = None,
        has_join_url: bool = True
    ) -> Dict[str, Any]:
        """
        Create a realistic callRecord response.

        Args:
            start_hours_ago: How many hours ago the meeting started (negative = past)
            duration_minutes: Meeting duration in minutes
            organizer_email: Email of meeting organizer
            participants: List of participant dicts, or None for default
            has_join_url: Whether to include joinWebUrl (meeting ID)

        Returns:
            Dictionary matching Graph API callRecords response format
        """
        start_time = datetime.now(timezone.utc) - timedelta(hours=start_hours_ago)
        end_time = start_time + timedelta(minutes=duration_minutes)
        call_id = str(uuid.uuid4())
        meeting_id = f"MSo...***19:meeting_{uuid.uuid4().hex[:16]}@thread.v2"

        # Default participants if none provided
        if participants is None:
            participants = [
                {
                    "email": "participant1@example.com",
                    "name": "Participant One",
                    "role": "attendee"
                }
            ]

        # Build sessions from participants
        sessions = []
        for p in participants:
            sessions.append({
                "caller": {
                    "identity": {
                        "user": {
                            "id": str(uuid.uuid4()),
                            "displayName": p.get("name", "Unknown"),
                            "userPrincipalName": p.get("email", "unknown@example.com")
                        }
                    }
                }
            })

        return {
            "id": call_id,
            "version": 1,
            "startDateTime": start_time.isoformat().replace('+00:00', 'Z'),
            "endDateTime": end_time.isoformat().replace('+00:00', 'Z'),
            "joinWebUrl": meeting_id if has_join_url else None,
            "organizer": {
                "id": str(uuid.uuid4()),
                "displayName": organizer_email.split('@')[0].replace('.', ' ').title(),
                "userPrincipalName": organizer_email,
                "email": organizer_email
            },
            "sessions": sessions
        }

    @staticmethod
    def create_transcript_metadata(
        meeting_id: str,
        transcript_id: Optional[str] = None,
        created_hours_ago: int = 0
    ) -> Dict[str, Any]:
        """
        Create realistic transcript metadata.

        Args:
            meeting_id: Meeting ID this transcript belongs to
            transcript_id: Transcript ID, or None to generate
            created_hours_ago: How long ago transcript was created

        Returns:
            Dictionary matching Graph API transcript metadata format
        """
        if transcript_id is None:
            transcript_id = str(uuid.uuid4())

        created_time = datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)

        return {
            "id": transcript_id,
            "meetingId": meeting_id,
            "createdDateTime": created_time.isoformat().replace('+00:00', 'Z'),
            "transcriptContentUrl": f"https://graph.microsoft.com/.../content",
            "meetingOrganizer": {
                "application": None,
                "device": None,
                "user": {
                    "id": str(uuid.uuid4()),
                    "displayName": "Organizer",
                    "userPrincipalName": "organizer@example.com"
                }
            }
        }

    @staticmethod
    def create_vtt_content(
        speakers: int = 3,
        duration_minutes: int = 30,
        words_per_minute: int = 150
    ) -> str:
        """
        Generate realistic VTT transcript content.

        Args:
            speakers: Number of different speakers
            duration_minutes: Total duration of transcript
            words_per_minute: Average words spoken per minute

        Returns:
            VTT format string
        """
        vtt_lines = ["WEBVTT", ""]

        total_words = duration_minutes * words_per_minute
        current_time = 0
        speaker_names = [f"Speaker {i+1}" for i in range(speakers)]

        for i in range(0, total_words, 10):
            # Rotate through speakers
            speaker = speaker_names[i // 50 % speakers]

            # Format timestamp
            minutes = current_time // 60
            seconds = current_time % 60
            timestamp_start = f"{minutes:02d}:{seconds:02d}.000"

            current_time += 4  # 4 seconds per segment
            minutes = current_time // 60
            seconds = current_time % 60
            timestamp_end = f"{minutes:02d}:{seconds:02d}.000"

            vtt_lines.append(f"{timestamp_start} --> {timestamp_end}")
            vtt_lines.append(f"<v {speaker}>Sample discussion text segment {i}. This represents realistic meeting content.</v>")
            vtt_lines.append("")

        return "\n".join(vtt_lines)


class DatabaseTestFactory:
    """Factory for creating test database models."""

    @staticmethod
    def create_pilot_user(
        email: str = "pilot@example.com",
        display_name: Optional[str] = None,
        is_active: bool = True
    ):
        """Create a PilotUser model instance for testing."""
        from src.core.database import PilotUser

        return PilotUser(
            email=email,
            display_name=display_name or email.split('@')[0].title(),
            is_active=is_active,
            added_at=datetime.now(timezone.utc)
        )

    @staticmethod
    def create_user_preference(
        email: str = "user@example.com",
        user_id: Optional[str] = None,
        receive_emails: bool = True,
        email_preference: str = 'all'
    ):
        """Create a UserPreference model instance for testing."""
        from src.core.database import UserPreference

        # Generate test GUID if not provided
        if user_id is None:
            user_id = str(uuid.uuid4())

        return UserPreference(
            user_id=user_id,
            user_email=email,
            receive_emails=receive_emails,
            email_preference=email_preference,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )

    @staticmethod
    def create_processed_call_record(
        call_record_id: str,
        source: str = "webhook"
    ):
        """Create a ProcessedCallRecord model instance for testing."""
        from src.core.database import ProcessedCallRecord

        return ProcessedCallRecord(
            call_record_id=call_record_id,
            source=source,
            processed_at=datetime.now(timezone.utc)
        )
