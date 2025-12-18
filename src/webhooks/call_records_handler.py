"""
Microsoft Graph Webhook Handler.

Processes org-wide meeting notifications from Microsoft Graph:
- /communications/callRecords (all meetings, need to check for transcripts)
- communications/onlineMeetings/getAllTranscripts (only when transcripts ready)
"""

import logging
import re
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from ..core.database import DatabaseManager, Meeting, JobQueue, ProcessedCallRecord
from ..graph.client import GraphAPIClient
from ..preferences.user_preferences import PreferenceManager

logger = logging.getLogger(__name__)


class CallRecordsWebhookHandler:
    """
    Handles webhook notifications from Microsoft Graph.

    Supports both callRecords and callTranscript subscriptions.
    Processes meetings, checks for opted-in participants, and enqueues jobs.
    """

    def __init__(self, db: DatabaseManager, graph_client: GraphAPIClient):
        """
        Initialize handler.

        Args:
            db: DatabaseManager instance
            graph_client: GraphAPIClient instance
        """
        self.db = db
        self.graph_client = graph_client
        self.pref_manager = PreferenceManager(db)

    async def handle_notification(self, notification: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle webhook notification (callRecords or callTranscript).

        Args:
            notification: Webhook notification payload from Microsoft Graph
                         Can be either a single notification or wrapped in a "value" array

        Returns:
            Response dict with processing status
        """
        try:
            # Microsoft Graph wraps notifications in a "value" array
            # Extract individual notifications and process each one
            if "value" in notification and isinstance(notification["value"], list):
                logger.info(f"Processing {len(notification['value'])} notifications from batch")
                results = []
                for individual_notification in notification["value"]:
                    result = await self._process_individual_notification(individual_notification)
                    results.append(result)
                return {"status": "batch_processed", "count": len(results), "results": results}
            else:
                # Single notification (not wrapped)
                return await self._process_individual_notification(notification)

        except Exception as e:
            logger.error(f"Error handling webhook notification: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _process_individual_notification(self, notification: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single notification.

        Args:
            notification: Individual notification object

        Returns:
            Processing status dict
        """
        try:
            # Extract notification details
            change_type = notification.get("changeType")
            resource = notification.get("resource")
            resource_data = notification.get("resourceData", {})

            logger.info(f"Processing notification: changeType={change_type}, resource={resource}")

            # Handle subscription validation
            if "subscriptionId" in notification and not resource:
                return {"status": "validation_acknowledged"}

            # Determine notification type
            if resource and "transcript" in resource.lower():
                # callTranscript notification - transcript is READY!
                logger.info("Processing callTranscript notification (transcript ready)")
                return await self._process_transcript_notification(notification)
            elif change_type == "created" and resource:
                # callRecords notification - need to check if transcript exists
                logger.info("Processing callRecords notification")
                call_record_id = resource_data.get("id") or resource.split("/")[-1]
                result = await self._process_call_record(call_record_id)
                return result

            return {"status": "ignored", "reason": f"Unhandled notification type"}

        except Exception as e:
            logger.error(f"Error processing individual notification: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _process_transcript_notification(self, notification: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process callTranscript notification (transcript is ready).

        This is the PREFERRED notification type - only fires when transcripts are ready!
        No need to poll or check for transcript availability.

        Args:
            notification: Notification payload with transcript info

        Returns:
            Processing status dict
        """
        try:
            resource = notification.get("resource")
            resource_data = notification.get("resourceData", {})

            # Extract meeting ID and transcript ID from resource path
            # Format can be either:
            #   communications/onlineMeetings/{meetingId}/transcripts/{transcriptId}
            #   users('{userId}')/onlineMeetings('{encodedMeetingId}')/transcripts('{encodedTranscriptId}')
            # More robust regex to handle encoded IDs with special characters
            match = re.search(r"onlineMeetings(?:/|\(['\"]?)([^/'\"()]+)(?:['\"]?\))?/transcripts(?:/|\(['\"]?)([^/'\"()]+)", resource)
            if not match:
                logger.warning(f"Could not parse meeting/transcript IDs from resource: {resource}")
                return {"status": "error", "reason": "Invalid resource format"}

            meeting_id = match.group(1)
            transcript_id = match.group(2)

            logger.info(f"Transcript ready: meeting={meeting_id}, transcript={transcript_id}")

            with self.db.get_session() as session:
                # Check if meeting already exists
                existing_meeting = session.query(Meeting).filter_by(
                    meeting_id=meeting_id
                ).first()

                if existing_meeting:
                    logger.info(f"Meeting {existing_meeting.id} already exists, updating status")
                    existing_meeting.status = "transcript_ready"
                    db_meeting_id = existing_meeting.id

                    # Check if we've already processed this transcript
                    existing_job = session.query(JobQueue).filter_by(
                        meeting_id=db_meeting_id,
                        job_type="fetch_transcript",
                        status="completed"
                    ).first()

                    if existing_job:
                        logger.info(f"Transcript already processed for meeting {db_meeting_id}")
                        return {"status": "duplicate", "meeting_id": db_meeting_id}

                else:
                    # Need to fetch meeting details from Graph API
                    try:
                        meeting_info = self.graph_client.get(f"/communications/onlineMeetings/{meeting_id}")
                    except Exception as e:
                        logger.warning(f"Could not fetch meeting details: {e}")
                        # Create minimal meeting record
                        meeting_info = {
                            "subject": "Teams Meeting",
                            "startDateTime": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                            "endDateTime": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                        }

                    # Create meeting record
                    meeting = Meeting(
                        meeting_id=meeting_id,
                        subject=meeting_info.get("subject", "Teams Meeting"),
                        organizer_email=meeting_info.get("participants", {}).get("organizer", {}).get("upn"),
                        organizer_name=meeting_info.get("participants", {}).get("organizer", {}).get("identity", {}).get("user", {}).get("displayName"),
                        start_time=self._parse_datetime(meeting_info.get("startDateTime")),
                        end_time=self._parse_datetime(meeting_info.get("endDateTime")),
                        join_url=meeting_info.get("joinWebUrl"),
                        chat_id=meeting_info.get("chatInfo", {}).get("threadId"),
                        status="transcript_ready"
                    )
                    session.add(meeting)
                    session.flush()
                    db_meeting_id = meeting.id

                    logger.info(f"Created meeting {db_meeting_id}: {meeting.subject}")

                # Enqueue fetch_transcript job with transcript_id
                job = JobQueue(
                    job_type="fetch_transcript",
                    meeting_id=db_meeting_id,
                    input_data={
                        "meeting_id": db_meeting_id,
                        "transcript_id": transcript_id  # Pass transcript ID directly!
                    },
                    priority=10  # Higher priority - transcript is ready now!
                )
                session.add(job)
                session.commit()

                logger.info(f"✅ Enqueued fetch_transcript job for meeting {db_meeting_id}")

                return {
                    "status": "processed",
                    "meeting_id": db_meeting_id,
                    "transcript_id": transcript_id,
                    "job_created": True
                }

        except Exception as e:
            logger.error(f"Error processing transcript notification: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _process_call_record(self, call_record_id: str, source: str = "webhook") -> Dict[str, Any]:
        """
        Process a single callRecord.

        Args:
            call_record_id: ID of the callRecord to process
            source: Source of this callRecord ('webhook', 'backfill', 'safety_net')

        Returns:
            Processing status dict
        """
        with self.db.get_session() as session:
            # Check if already processed (deduplication)
            existing = session.query(ProcessedCallRecord).filter_by(
                call_record_id=call_record_id
            ).first()

            if existing:
                logger.debug(f"CallRecord {call_record_id} already processed")
                return {"status": "duplicate", "call_record_id": call_record_id}

            try:
                # Fetch full callRecord from Graph API (including sessions)
                call_record = self.graph_client.get(
                    f"/communications/callRecords/{call_record_id}",
                    params={"$expand": "sessions"}
                )

                # If sessions weren't expanded, fetch them separately
                if "sessions" not in call_record or not call_record["sessions"]:
                    sessions_response = self.graph_client.get(
                        f"/communications/callRecords/{call_record_id}/sessions"
                    )
                    call_record["sessions"] = sessions_response.get("value", [])

                # Extract meeting info
                online_meeting_id = call_record.get("joinWebUrl")
                if not online_meeting_id:
                    logger.warning(f"No joinWebUrl in callRecord {call_record_id}")
                    return {"status": "skipped", "reason": "No joinWebUrl"}

                # Get participants first (more efficient to check this before API calls)
                participants = self._extract_participants(call_record)

                # Check if any participants have opted-in
                opted_in_participants = [
                    p for p in participants
                    if self.pref_manager.get_user_preference(p["email"])
                ]

                if not opted_in_participants:
                    logger.info(f"No opted-in participants for meeting {online_meeting_id}")
                    session.add(ProcessedCallRecord(
                        call_record_id=call_record_id,
                        source=source
                    ))
                    session.commit()
                    return {"status": "skipped", "reason": "No opted-in participants"}

                logger.info(f"Meeting has {len(opted_in_participants)} opted-in participants")

                # Check if meeting already exists in database
                existing_meeting = session.query(Meeting).filter_by(
                    meeting_id=online_meeting_id
                ).first()

                if existing_meeting:
                    logger.info(f"Meeting {existing_meeting.id} already exists")
                    meeting_id = existing_meeting.id
                else:
                    # Create meeting record
                    meeting = Meeting(
                        meeting_id=online_meeting_id,
                        subject=call_record.get("subject", "Unknown Meeting"),
                        organizer_email=call_record.get("organizer", {}).get("email"),
                        organizer_name=call_record.get("organizer", {}).get("displayName"),
                        start_time=self._parse_datetime(call_record.get("startDateTime")),
                        end_time=self._parse_datetime(call_record.get("endDateTime")),
                        participant_count=len(participants),
                        join_url=online_meeting_id,
                        chat_id=call_record.get("chatId"),
                        status="discovered"
                    )
                    session.add(meeting)
                    session.flush()
                    meeting_id = meeting.id

                    logger.info(f"Created meeting {meeting_id}: {meeting.subject}")

                    # Add participants
                    from ..core.database import MeetingParticipant
                    for p in participants:
                        participant = MeetingParticipant(
                            meeting_id=meeting_id,
                            email=p["email"],
                            display_name=p["name"],
                            role=p.get("role", "attendee")
                        )
                        session.add(participant)

                # Mark callRecord as processed
                session.add(ProcessedCallRecord(
                    call_record_id=call_record_id,
                    source=source
                ))

                # Enqueue fetch_transcript job
                job = JobQueue(
                    job_type="fetch_transcript",
                    meeting_id=meeting_id,
                    input_data={"meeting_id": meeting_id},
                    priority=5
                )
                session.add(job)
                session.commit()

                logger.info(f"✅ Enqueued fetch_transcript job for meeting {meeting_id}")

                return {
                    "status": "processed",
                    "call_record_id": call_record_id,
                    "meeting_id": meeting_id,
                    "opted_in_count": len(opted_in_participants),
                    "job_created": True
                }

            except Exception as e:
                logger.error(f"Error processing callRecord {call_record_id}: {e}", exc_info=True)
                session.rollback()
                return {"status": "error", "error": str(e)}

    def _extract_participants(self, call_record: Dict[str, Any]) -> list:
        """Extract participant list from callRecord.

        Note: Call record sessions only include user ID and displayName, NOT email.
        We must look up each user in Graph API to get their email address.
        """
        participants = []
        seen_user_ids = set()

        for session_data in call_record.get("sessions", []):
            for endpoint in ["caller", "callee"]:
                identity = session_data.get(endpoint, {}).get("identity", {})
                user = identity.get("user")

                if user and user.get("id"):
                    user_id = user["id"]

                    # Skip if already processed this user
                    if user_id in seen_user_ids:
                        continue
                    seen_user_ids.add(user_id)

                    # Look up user email from Graph API (not included in call record)
                    email = user.get("userPrincipalName")  # Usually not present
                    if not email:
                        try:
                            user_details = self.graph_client.get(f"/users/{user_id}")
                            email = user_details.get("userPrincipalName") or user_details.get("mail")
                            logger.debug(f"Looked up user {user_id}: {email}")
                        except Exception as e:
                            logger.warning(f"Could not look up user {user_id}: {e}")
                            continue

                    if email:
                        participants.append({
                            "email": email.lower(),  # Normalize to lowercase
                            "name": user.get("displayName"),
                            "role": "attendee",
                            "user_id": user_id
                        })

        logger.info(f"Extracted {len(participants)} participants from call record")
        return participants

    def _parse_datetime(self, dt_string: str) -> datetime:
        """Parse ISO datetime string."""
        if not dt_string:
            return None
        try:
            return datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
        except:
            return None

    async def backfill_recent_meetings(self, lookback_hours: int = 48) -> Dict[str, Any]:
        """
        Enhanced backfill with retry logic and progress tracking.

        Smart gap detection: Fills gap from last webhook to present instead of
        using fixed lookback period. Returns detailed statistics for monitoring.

        Args:
            lookback_hours: Maximum hours to look back (fallback if no previous webhook found)

        Returns:
            Dict with statistics:
            - call_records_found, meetings_created, transcripts_found,
            - transcripts_pending, skipped_no_optin, jobs_created, errors
        """
        stats = {
            "call_records_found": 0,
            "meetings_created": 0,
            "transcripts_found": 0,
            "transcripts_pending": 0,
            "skipped_no_optin": 0,
            "jobs_created": 0,
            "errors": 0
        }

        try:
            # Smart gap detection (from last webhook timestamp)
            with self.db.get_session() as session:
                last_webhook = session.query(ProcessedCallRecord).filter_by(
                    source='webhook'
                ).order_by(ProcessedCallRecord.processed_at.desc()).first()

                if last_webhook:
                    # Backfill from last webhook with 5-minute safety margin
                    # Make processed_at timezone-aware if it's naive
                    processed_at = last_webhook.processed_at
                    if processed_at.tzinfo is None:
                        processed_at = processed_at.replace(tzinfo=timezone.utc)

                    cutoff = processed_at - timedelta(minutes=5)
                    time_gap = datetime.now(timezone.utc) - processed_at
                    hours_gap = time_gap.total_seconds() / 3600

                    logger.info(f"Last webhook: {last_webhook.processed_at}")
                    logger.info(f"Backfilling {hours_gap:.1f} hour gap...")
                else:
                    # No webhooks - use default lookback
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
                    logger.info(f"No webhooks found, backfilling last {lookback_hours} hours...")

            cutoff_str = cutoff.isoformat().replace('+00:00', 'Z')

            # Query callRecords API (PROVEN WORKING)
            logger.info(f"Querying callRecords since {cutoff_str}...")
            response = self.graph_client.get(
                "/communications/callRecords",
                params={
                    "$filter": f"startDateTime ge {cutoff_str}"
                }
            )

            call_records = response.get("value", [])
            stats["call_records_found"] = len(call_records)
            logger.info(f"Found {len(call_records)} callRecords to process")

            # Process each callRecord
            for record in call_records:
                try:
                    call_record_id = record["id"]

                    # Deduplication check
                    with self.db.get_session() as session:
                        existing = session.query(ProcessedCallRecord).filter_by(
                            call_record_id=call_record_id
                        ).first()

                        if existing:
                            logger.debug(f"CallRecord {call_record_id} already processed")
                            continue

                    # Fetch sessions for this call record (not included in list response)
                    sessions_response = self.graph_client.get(
                        f"/communications/callRecords/{call_record_id}/sessions"
                    )
                    record["sessions"] = sessions_response.get("value", [])

                    # Extract participants from sessions
                    participants = self._extract_participants(record)
                    logger.debug(f"CallRecord {call_record_id[:16]}... has {len(participants)} participants: {[p.get('email') for p in participants]}")

                    # Check for opted-in participants
                    opted_in_participants = [
                        p for p in participants
                        if self.pref_manager.get_user_preference(p["email"])
                    ]

                    if not opted_in_participants:
                        logger.info(f"No opted-in participants for callRecord {call_record_id}")
                        stats["skipped_no_optin"] += 1

                        # Still mark as processed to avoid re-checking
                        with self.db.get_session() as session:
                            session.add(ProcessedCallRecord(
                                call_record_id=call_record_id,
                                source="backfill"
                            ))
                            session.commit()
                        continue

                    # Process the callRecord (creates meeting and tries to fetch transcript)
                    result = await self._process_call_record(call_record_id, source="backfill")

                    if result["status"] == "processed":
                        stats["meetings_created"] += 1
                        stats["jobs_created"] += 1
                        # Note: Can't easily track if transcript was found vs pending
                        # without refactoring _process_call_record
                    elif result["status"] == "error":
                        stats["errors"] += 1

                except Exception as e:
                    logger.error(f"Error processing callRecord {record.get('id')}: {e}", exc_info=True)
                    stats["errors"] += 1
                    continue

            logger.info(
                f"✅ Backfill complete: {stats['call_records_found']} records, "
                f"{stats['meetings_created']} meetings, "
                f"{stats['skipped_no_optin']} skipped (no opt-in), "
                f"{stats['errors']} errors"
            )

            return stats

        except Exception as e:
            logger.error(f"Backfill failed: {e}", exc_info=True)
            raise
