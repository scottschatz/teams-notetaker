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

from ..core.database import DatabaseManager, Meeting, JobQueue, ProcessedCallRecord, MeetingParticipant
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

            # Extract user ID from resource path (if present)
            # Format: users('{userId}')/onlineMeetings('{encodedMeetingId}')/transcripts('{encodedTranscriptId}')
            user_match = re.search(r"users\(['\"]?([^'\"()]+)['\"]?\)", resource)
            organizer_user_id = user_match.group(1) if user_match else None

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

            logger.info(f"Transcript ready: meeting={meeting_id}, transcript={transcript_id}, organizer={organizer_user_id}")

            with self.db.get_session() as session:
                # Check if meeting already exists
                existing_meeting = session.query(Meeting).filter_by(
                    meeting_id=meeting_id
                ).first()

                if existing_meeting:
                    logger.info(f"Meeting {existing_meeting.id} already exists, updating status")
                    existing_meeting.status = "queued"
                    db_meeting_id = existing_meeting.id

                    # Update organizer info if missing and we have it from notification
                    if organizer_user_id and not existing_meeting.organizer_user_id:
                        existing_meeting.organizer_user_id = organizer_user_id
                        # Fetch organizer details
                        try:
                            user_info = self.graph_client.get(f"/users/{organizer_user_id}")
                            existing_meeting.organizer_email = self._get_email_from_user_info(user_info)
                            existing_meeting.organizer_name = user_info.get("displayName")
                            logger.info(f"Updated organizer info for meeting {db_meeting_id}")
                        except Exception as e:
                            logger.warning(f"Could not fetch organizer details: {e}")

                    # Store transcript_id from webhook (helps backfill-created meetings)
                    if transcript_id and not existing_meeting.graph_transcript_id:
                        existing_meeting.graph_transcript_id = transcript_id
                        logger.info(f"Stored graph_transcript_id for meeting {db_meeting_id}")

                    # Check if we've already processed THIS SPECIFIC transcript
                    # (Important for recurring meetings - same meeting_id but different transcript_id)
                    from sqlalchemy import cast
                    from sqlalchemy.dialects.postgresql import JSONB

                    # Check for existing jobs in any active or completed state
                    existing_job = session.query(JobQueue).filter(
                        JobQueue.meeting_id == db_meeting_id,
                        JobQueue.job_type == "fetch_transcript",
                        JobQueue.status.in_(["pending", "running", "retrying", "completed"]),
                        JobQueue.input_data["transcript_id"].astext == transcript_id
                    ).first()

                    if existing_job:
                        logger.info(f"Transcript {transcript_id[:20]}... already has job (status={existing_job.status}) for meeting {db_meeting_id}")
                        return {"status": "duplicate", "meeting_id": db_meeting_id}

                    logger.info(f"New transcript {transcript_id[:20]}... for recurring meeting {db_meeting_id}")

                else:
                    # Fetch organizer details from Graph API if we have the user ID
                    organizer_email = None
                    organizer_name = None
                    if organizer_user_id:
                        try:
                            user_info = self.graph_client.get(f"/users/{organizer_user_id}")
                            organizer_email = self._get_email_from_user_info(user_info)
                            organizer_name = user_info.get("displayName")
                            logger.info(f"Fetched organizer info: {organizer_name} <{organizer_email}>")
                        except Exception as e:
                            logger.warning(f"Could not fetch organizer details: {e}")

                    # Fetch meeting details (subject, times, transcription settings) from onlineMeetings API
                    meeting_subject = "Teams Meeting"
                    # Default to current UTC time (naive) if not available
                    meeting_start_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    meeting_end_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    allow_transcription = None
                    allow_recording = None
                    if organizer_user_id and meeting_id:
                        try:
                            meeting_details = self.graph_client.get(
                                f"/users/{organizer_user_id}/onlineMeetings/{meeting_id}"
                            )
                            meeting_subject = meeting_details.get("subject") or "Teams Meeting"
                            if meeting_details.get("startDateTime"):
                                # Parse UTC time and store as UTC-naive
                                dt = datetime.fromisoformat(
                                    meeting_details["startDateTime"].replace("Z", "+00:00")
                                )
                                meeting_start_time = dt.replace(tzinfo=None)
                            if meeting_details.get("endDateTime"):
                                # Parse UTC time and store as UTC-naive
                                dt = datetime.fromisoformat(
                                    meeting_details["endDateTime"].replace("Z", "+00:00")
                                )
                                meeting_end_time = dt.replace(tzinfo=None)
                            # Extract transcription/recording settings
                            allow_transcription = meeting_details.get("allowTranscription")
                            allow_recording = meeting_details.get("allowRecording")
                            logger.info(f"Fetched meeting details: {meeting_subject} (allowTranscription={allow_transcription})")
                        except Exception as e:
                            logger.warning(f"Could not fetch meeting details: {e}")

                    # Create meeting record with organizer info from notification
                    # Note: meeting_id from transcript notification is the online_meeting_id (MSp...)
                    meeting = Meeting(
                        meeting_id=meeting_id,
                        online_meeting_id=meeting_id,  # MSp... format from transcript notification
                        calendar_event_id=None,  # Not available from transcript notification
                        call_record_id=None,  # Not available from transcript notification
                        discovery_source=source,  # 'webhook' or 'backfill'
                        subject=meeting_subject,
                        organizer_email=organizer_email,
                        organizer_name=organizer_name,
                        organizer_user_id=organizer_user_id,
                        start_time=meeting_start_time,
                        end_time=meeting_end_time,
                        status="queued",
                        participant_count=1,  # At least the organizer
                        graph_transcript_id=transcript_id,  # Store for backfill reliability
                        allow_transcription=allow_transcription,  # Transcription enabled setting
                        allow_recording=allow_recording  # Recording enabled setting
                    )
                    session.add(meeting)
                    session.flush()
                    db_meeting_id = meeting.id

                    # Add organizer as participant so they receive the email
                    if organizer_email:
                        participant = MeetingParticipant(
                            meeting_id=db_meeting_id,
                            email=organizer_email,
                            display_name=organizer_name or organizer_email,
                            role="organizer"
                        )
                        session.add(participant)
                        logger.info(f"Added organizer {organizer_email} as participant")

                    logger.info(f"Created meeting {db_meeting_id}: {meeting.subject}")

                # Enqueue fetch_transcript job with transcript_id
                job = JobQueue(
                    job_type="fetch_transcript",
                    meeting_id=db_meeting_id,
                    input_data={
                        "meeting_id": db_meeting_id,
                        "transcript_id": transcript_id  # Pass transcript ID directly!
                    },
                    priority=10,  # Higher priority - transcript is ready now!
                    max_retries=4  # Matches transcript processor retry_delays [5, 10, 15, 30]
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
                # Fetch full callRecord from Graph API (including sessions with segments for quality metrics)
                call_record = self.graph_client.get(
                    f"/communications/callRecords/{call_record_id}",
                    params={"$expand": "sessions($expand=segments)"}
                )

                # If sessions weren't expanded, fetch them separately (with segments)
                if "sessions" not in call_record or not call_record["sessions"]:
                    sessions_response = self.graph_client.get(
                        f"/communications/callRecords/{call_record_id}/sessions",
                        params={"$expand": "segments"}
                    )
                    call_record["sessions"] = sessions_response.get("value", [])

                # Extract meeting info
                online_meeting_id = call_record.get("joinWebUrl")
                if not online_meeting_id:
                    logger.warning(f"No joinWebUrl in callRecord {call_record_id}")
                    return {"status": "skipped", "reason": "No joinWebUrl"}

                # Extract call type from callRecord (groupCall, peerToPeer, unknown)
                call_type = call_record.get("type", "unknown")

                # Get participants first (more efficient to check this before API calls)
                participants = self._extract_participants(call_record)

                # Check if any participants have opted-in (for auto-processing decision)
                # Do this BEFORE the <3 filter so pilot users can test with small meetings
                opted_in_participants = [
                    p for p in participants
                    if p.get("email") and self.pref_manager.get_user_preference(p["email"])
                ]
                has_opted_in = len(opted_in_participants) > 0

                # Skip 1-on-1 calls (less than 3 participants) - but allow if pilot user is present
                # This lets opted-in users test with small meetings while filtering general 1-on-1s
                if len(participants) < 3 and not has_opted_in:
                    logger.info(f"Skipping 1-on-1 call with {len(participants)} participants (no pilot users)")
                    session.add(ProcessedCallRecord(
                        call_record_id=call_record_id,
                        source=source
                    ))
                    session.commit()
                    return {"status": "skipped", "reason": "1-on-1 call (< 3 participants, no pilot users)"}

                # Always capture transcript, but only auto-process if opted-in participants exist
                if has_opted_in:
                    logger.info(f"Meeting has {len(opted_in_participants)} opted-in participants, will auto-process")
                else:
                    logger.info(f"No opted-in participants for {online_meeting_id}, capturing transcript only")

                # Extract organizer info from call record or first participant
                # callRecords don't have a simple "organizer" field - use organizer from call record
                # or fall back to first participant as the meeting creator
                organizer_info = call_record.get("organizer", {})
                organizer_user = organizer_info.get("user", {})
                organizer_user_id = organizer_user.get("id")
                organizer_email = None
                organizer_name = organizer_user.get("displayName")

                # If no organizer in callRecord, use first participant's user_id
                if not organizer_user_id and participants:
                    first_participant = participants[0]
                    organizer_user_id = first_participant.get("user_id")
                    organizer_email = first_participant.get("email")
                    organizer_name = first_participant.get("name")
                    logger.info(f"Using first participant as organizer: {organizer_email} ({organizer_user_id})")

                # Look up organizer email from Graph API if we have user_id but no email
                if organizer_user_id and not organizer_email:
                    try:
                        user_info = self.graph_client.get(f"/users/{organizer_user_id}")
                        organizer_email = self._get_email_from_user_info(user_info)
                        if not organizer_name:
                            organizer_name = user_info.get("displayName")
                        logger.debug(f"Looked up organizer: {organizer_name} <{organizer_email}>")
                    except Exception as e:
                        logger.warning(f"Could not look up organizer email for {organizer_user_id}: {e}")

                # Look up meeting subject and transcription settings from online meeting details
                meeting_subject = "Unknown Meeting"
                allow_transcription = None
                allow_recording = None
                if organizer_user_id and online_meeting_id:
                    try:
                        # online_meeting_id is the joinWebUrl
                        join_url = online_meeting_id
                        # Use $filter to query by joinWebUrl (required by Graph API)
                        meetings_response = self.graph_client.get(
                            f"/users/{organizer_user_id}/onlineMeetings",
                            params={"$filter": f"joinWebUrl eq '{join_url}'"}
                        )
                        meetings = meetings_response.get("value", [])
                        if meetings:
                            meeting_data = meetings[0]
                            meeting_subject = meeting_data.get("subject") or "Teams Meeting"
                            allow_transcription = meeting_data.get("allowTranscription")
                            allow_recording = meeting_data.get("allowRecording")
                            logger.info(f"Found meeting: {meeting_subject} (allowTranscription={allow_transcription})")
                    except Exception as e:
                        # Many callRecords are ad-hoc calls without scheduled meetings
                        logger.debug(f"Could not look up meeting subject for {organizer_user_id}: {e}")

                # Check if meeting already exists in database
                # Check multiple fields for deduplication (webhook vs calendar discovery)
                # IMPORTANT: Also check date for recurring meetings (they share the same online_meeting_id)
                # Both callRecord.startDateTime and Meeting.start_time are UTC-naive (stored as UTC)
                from sqlalchemy import or_, func

                # Parse the call's start time - this is UTC-naive (value is UTC)
                call_start_time = self._parse_datetime(call_record.get("startDateTime"))

                # For recurring meetings: same online_meeting_id but different UTC dates = different meetings
                if call_start_time:
                    call_start_date = call_start_time.date()  # UTC date
                    existing_meeting = session.query(Meeting).filter(
                        or_(
                            Meeting.meeting_id == online_meeting_id,
                            Meeting.online_meeting_id == online_meeting_id,
                            Meeting.join_url == online_meeting_id
                        ),
                        # Compare UTC dates (both are UTC-naive in database)
                        func.date(Meeting.start_time) == call_start_date
                    ).first()
                else:
                    # Fallback if no start time (shouldn't happen, but be safe)
                    logger.warning(f"No startDateTime in callRecord {call_record_id}, using ID-only dedup")
                    call_start_date = None
                    existing_meeting = session.query(Meeting).filter(
                        or_(
                            Meeting.meeting_id == online_meeting_id,
                            Meeting.online_meeting_id == online_meeting_id,
                            Meeting.join_url == online_meeting_id
                        )
                    ).first()

                if existing_meeting:
                    logger.info(f"Meeting {existing_meeting.id} already exists for date {call_start_date}")
                    meeting_id = existing_meeting.id

                    # Update organizer_user_id if we have it and existing doesn't
                    if organizer_user_id and not existing_meeting.organizer_user_id:
                        existing_meeting.organizer_user_id = organizer_user_id
                        logger.info(f"Updated organizer_user_id for meeting {meeting_id}")
                else:
                    # Extract enterprise intelligence metadata
                    enterprise_metadata = self._extract_enterprise_metadata(call_record)

                    # Check if transcription was disabled - skip if so
                    if allow_transcription is False:
                        logger.info(f"Transcription disabled for meeting, marking as transcription_disabled")
                        meeting = Meeting(
                            meeting_id=online_meeting_id,
                            online_meeting_id=online_meeting_id,
                            call_record_id=call_record_id,
                            discovery_source=source,
                            subject=meeting_subject,
                            organizer_email=organizer_email,
                            organizer_name=organizer_name,
                            organizer_user_id=organizer_user_id,
                            start_time=self._parse_datetime(call_record.get("startDateTime")),
                            end_time=self._parse_datetime(call_record.get("endDateTime")),
                            participant_count=len(participants),
                            join_url=online_meeting_id,
                            chat_id=call_record.get("chatId"),
                            status="transcription_disabled",
                            allow_transcription=False,
                            allow_recording=allow_recording,
                            call_type=call_type,
                            # Enterprise intelligence metadata
                            primary_modality=enterprise_metadata.get("primary_modality"),
                            modalities_used=enterprise_metadata.get("modalities_used"),
                            is_pstn_call=enterprise_metadata.get("is_pstn_call", False),
                            actual_duration_seconds=enterprise_metadata.get("actual_duration_seconds"),
                            external_domains=enterprise_metadata.get("external_domains"),
                            device_types=enterprise_metadata.get("device_types"),
                            avg_packet_loss_rate=enterprise_metadata.get("avg_packet_loss_rate"),
                            avg_jitter_ms=enterprise_metadata.get("avg_jitter_ms"),
                            avg_round_trip_ms=enterprise_metadata.get("avg_round_trip_ms"),
                            network_quality_score=enterprise_metadata.get("network_quality_score"),
                            connection_types=enterprise_metadata.get("connection_types"),
                            had_quality_issues=enterprise_metadata.get("had_quality_issues", False)
                        )
                        session.add(meeting)
                        session.add(ProcessedCallRecord(call_record_id=call_record_id, source=source))
                        session.commit()
                        return {
                            "status": "skipped",
                            "reason": "transcription_disabled",
                            "meeting_id": meeting.id
                        }

                    # Create meeting record from callRecord notification
                    meeting = Meeting(
                        meeting_id=online_meeting_id,
                        online_meeting_id=online_meeting_id,  # MSp... format from callRecord
                        calendar_event_id=None,  # Not available from callRecord
                        call_record_id=call_record_id,  # The callRecord ID
                        discovery_source=source,  # 'webhook' or 'backfill'
                        subject=meeting_subject,
                        organizer_email=organizer_email,
                        organizer_name=organizer_name,
                        organizer_user_id=organizer_user_id,
                        start_time=self._parse_datetime(call_record.get("startDateTime")),
                        end_time=self._parse_datetime(call_record.get("endDateTime")),
                        participant_count=len(participants),
                        join_url=online_meeting_id,
                        chat_id=call_record.get("chatId"),
                        status="discovered",
                        allow_transcription=allow_transcription,
                        allow_recording=allow_recording,
                        call_type=call_type,  # groupCall, peerToPeer, unknown
                        # Enterprise intelligence metadata
                        primary_modality=enterprise_metadata.get("primary_modality"),
                        modalities_used=enterprise_metadata.get("modalities_used"),
                        is_pstn_call=enterprise_metadata.get("is_pstn_call", False),
                        actual_duration_seconds=enterprise_metadata.get("actual_duration_seconds"),
                        external_domains=enterprise_metadata.get("external_domains"),
                        device_types=enterprise_metadata.get("device_types"),
                        avg_packet_loss_rate=enterprise_metadata.get("avg_packet_loss_rate"),
                        avg_jitter_ms=enterprise_metadata.get("avg_jitter_ms"),
                        avg_round_trip_ms=enterprise_metadata.get("avg_round_trip_ms"),
                        network_quality_score=enterprise_metadata.get("network_quality_score"),
                        connection_types=enterprise_metadata.get("connection_types"),
                        had_quality_issues=enterprise_metadata.get("had_quality_issues", False)
                    )
                    session.add(meeting)
                    session.flush()
                    meeting_id = meeting.id

                    logger.info(f"Created meeting {meeting_id}: {meeting.subject}")

                    # Add participants (skip internal participants without email)
                    from ..core.database import MeetingParticipant
                    participants_added = 0
                    for p in participants:
                        display_name = p.get("name") or "Unknown"
                        participant_type = p.get("type", "internal")
                        email = p.get("email", "")

                        # Azure AD properties (fetched for internal users with email)
                        job_title = None
                        department = None
                        office_location = None
                        company_name = None

                        # For PSTN participants, include phone number in display name
                        if participant_type == "pstn" and p.get("phone"):
                            phone = p["phone"]
                            # Format phone with icon: "Name (📞 +1234567890)" or just "📞 +1234567890"
                            if display_name and display_name != "Phone Participant":
                                display_name = f"{display_name} (📞 {phone})"
                            else:
                                display_name = f"📞 {phone}"

                        # For external/guest participants, mark them
                        elif participant_type in ("guest", "external"):
                            if display_name and not display_name.endswith("(External)"):
                                display_name = f"{display_name} (External)"

                        # Skip internal participants without email (can't distribute to them)
                        elif participant_type == "internal" and not email:
                            logger.debug(f"Skipping participant without email: {display_name}")
                            continue

                        # Fetch Azure AD properties for internal users with email
                        if email and participant_type == "internal":
                            try:
                                user_details = self.graph_client.get_user_details(email)
                                if user_details:
                                    job_title = user_details.get("jobTitle")
                                    department = user_details.get("department")
                                    office_location = user_details.get("officeLocation")
                                    company_name = user_details.get("companyName")
                            except Exception as e:
                                logger.debug(f"Could not fetch Azure AD details for {email}: {e}")

                        participant = MeetingParticipant(
                            meeting_id=meeting_id,
                            email=email,
                            display_name=display_name,
                            role=p.get("role", "attendee"),
                            attended=True,
                            participant_type=participant_type,
                            job_title=job_title,
                            department=department,
                            office_location=office_location,
                            company_name=company_name
                        )
                        session.add(participant)
                        participants_added += 1

                    if participants_added > 0:
                        logger.info(f"Added {participants_added} participants to meeting {meeting_id}")

                    # Fetch and store invitees (people invited but may not have attended)
                    # This provides correct name spellings for AI summary generation
                    attendee_emails = {p.get("email", "").lower() for p in participants if p.get("email")}
                    invitees = self._fetch_meeting_invitees(organizer_user_id, online_meeting_id)
                    invitees_added = 0
                    for inv in invitees:
                        inv_email = inv.get("email", "").lower()
                        # Only add if they didn't actually attend
                        if inv_email and inv_email not in attendee_emails:
                            invitee_participant = MeetingParticipant(
                                meeting_id=meeting_id,
                                email=inv_email,
                                display_name=inv.get("name") or inv_email.split("@")[0],
                                role=inv.get("role", "attendee"),
                                attended=False,
                                participant_type="internal"
                            )
                            session.add(invitee_participant)
                            invitees_added += 1
                    if invitees_added > 0:
                        logger.info(f"Added {invitees_added} invitees who didn't attend")

                # Check if fetch_transcript job already exists for this meeting
                # This prevents duplicates when webhook and backfill both process same meeting
                existing_job = session.query(JobQueue).filter(
                    JobQueue.meeting_id == meeting_id,
                    JobQueue.job_type == "fetch_transcript",
                    JobQueue.status.in_(["pending", "running", "retrying", "completed"])
                ).first()

                if existing_job:
                    logger.info(f"fetch_transcript job already exists for meeting {meeting_id} (job {existing_job.id}, status={existing_job.status})")
                    # Still mark callRecord as processed to avoid re-checking
                    session.add(ProcessedCallRecord(
                        call_record_id=call_record_id,
                        source=source
                    ))
                    session.commit()
                    return {
                        "status": "job_exists",
                        "call_record_id": call_record_id,
                        "meeting_id": meeting_id,
                        "existing_job_id": existing_job.id
                    }

                # Mark callRecord as processed
                session.add(ProcessedCallRecord(
                    call_record_id=call_record_id,
                    source=source
                ))

                # Enqueue fetch_transcript job with auto_process flag
                # Try immediately - if transcript not ready, retry schedule handles it
                job = JobQueue(
                    job_type="fetch_transcript",
                    meeting_id=meeting_id,
                    input_data={
                        "meeting_id": meeting_id,
                        "auto_process": has_opted_in  # Controls downstream job creation
                    },
                    priority=5,
                    next_retry_at=None,  # Try immediately
                    max_retries=4  # Retry schedule: 5, 10, 15, 30 min if not ready
                )
                session.add(job)
                session.commit()

                status_msg = "will auto-process" if has_opted_in else "transcript only"
                logger.info(f"✅ Enqueued fetch_transcript job for meeting {meeting_id} ({status_msg})")

                return {
                    "status": "processed",
                    "call_record_id": call_record_id,
                    "meeting_id": meeting_id,
                    "opted_in_count": len(opted_in_participants),
                    "auto_process": has_opted_in,
                    "job_created": True
                }

            except Exception as e:
                logger.error(f"Error processing callRecord {call_record_id}: {e}", exc_info=True)
                session.rollback()
                return {"status": "error", "error": str(e)}

    def _extract_enterprise_metadata(self, call_record: Dict[str, Any], org_tenant_id: str = None) -> Dict[str, Any]:
        """
        Extract enterprise intelligence metadata from callRecord.

        Extracts modalities, quality metrics, device types, and external domain detection
        from the callRecord and its sessions/segments.

        Args:
            call_record: Full callRecord with sessions (and optionally segments)
            org_tenant_id: Organization's tenant ID for external detection

        Returns:
            Dict with enterprise metadata fields ready to save to Meeting model
        """
        metadata = {
            # Modality info
            "primary_modality": None,
            "modalities_used": None,
            "is_pstn_call": False,

            # Duration
            "actual_duration_seconds": None,

            # Quality metrics
            "avg_packet_loss_rate": None,
            "avg_jitter_ms": None,
            "avg_round_trip_ms": None,
            "network_quality_score": None,
            "connection_types": None,
            "had_quality_issues": False,

            # Participants
            "external_domains": None,
            "device_types": None,
        }

        try:
            # Extract modalities from callRecord level
            modalities = call_record.get("modalities", [])
            if modalities:
                metadata["modalities_used"] = modalities
                # Determine primary modality (video > screenSharing > audio)
                if "video" in modalities:
                    metadata["primary_modality"] = "video"
                elif "screenSharing" in modalities:
                    metadata["primary_modality"] = "screenSharing"
                elif "audio" in modalities:
                    metadata["primary_modality"] = "audio"

            # Calculate actual duration from callRecord timestamps
            start_dt = self._parse_datetime(call_record.get("startDateTime"))
            end_dt = self._parse_datetime(call_record.get("endDateTime"))
            if start_dt and end_dt:
                metadata["actual_duration_seconds"] = int((end_dt - start_dt).total_seconds())

            # Process sessions for quality, devices, and external detection
            sessions = call_record.get("sessions", [])
            if not sessions:
                return metadata

            # Collect metrics across all sessions/segments
            packet_loss_values = []
            jitter_values = []
            rtt_values = []
            connection_counts = {}
            device_counts = {}
            external_tenant_ids = set()

            for session in sessions:
                # Check for PSTN participants
                for endpoint in ["caller", "callee"]:
                    identity = session.get(endpoint, {}).get("identity", {})
                    if identity.get("phone"):
                        metadata["is_pstn_call"] = True

                    # External domain detection via tenant ID
                    user = identity.get("user", {})
                    tenant_id = user.get("tenantId")
                    if tenant_id and org_tenant_id and tenant_id != org_tenant_id:
                        external_tenant_ids.add(tenant_id)

                    # Device type detection from userAgent
                    user_agent = session.get(endpoint, {}).get("userAgent", {})
                    platform = user_agent.get("platform", "")
                    if platform:
                        platform_lower = platform.lower()
                        if "windows" in platform_lower or "mac" in platform_lower or "linux" in platform_lower:
                            device_type = "desktop"
                        elif "ios" in platform_lower or "android" in platform_lower:
                            device_type = "mobile"
                        elif "room" in platform_lower or "teams room" in platform_lower:
                            device_type = "room"
                        else:
                            device_type = "other"
                        device_counts[device_type] = device_counts.get(device_type, 0) + 1

                    # Connection type from userAgent
                    connection_type = user_agent.get("networkInfo", {}).get("connectionType", "")
                    if connection_type:
                        conn_lower = connection_type.lower()
                        if "wired" in conn_lower or "ethernet" in conn_lower:
                            conn_type = "wired"
                        elif "wifi" in conn_lower or "wireless" in conn_lower:
                            conn_type = "wifi"
                        elif "cellular" in conn_lower or "mobile" in conn_lower or "4g" in conn_lower or "5g" in conn_lower:
                            conn_type = "cellular"
                        else:
                            conn_type = "other"
                        connection_counts[conn_type] = connection_counts.get(conn_type, 0) + 1

                # Process segments for quality metrics
                segments = session.get("segments", [])
                for segment in segments:
                    media = segment.get("media", {})
                    for stream in media.get("streams", []):
                        # Packet loss rate (0-1)
                        loss = stream.get("averagePacketLossRate")
                        if loss is not None:
                            packet_loss_values.append(float(loss))

                        # Jitter (ISO 8601 duration like "PT0.015S")
                        jitter_str = stream.get("averageJitter")
                        if jitter_str:
                            jitter_ms = self._parse_duration_ms(jitter_str)
                            if jitter_ms is not None:
                                jitter_values.append(jitter_ms)

                        # Round trip time (ISO 8601 duration)
                        rtt_str = stream.get("averageRoundTripTime")
                        if rtt_str:
                            rtt_ms = self._parse_duration_ms(rtt_str)
                            if rtt_ms is not None:
                                rtt_values.append(rtt_ms)

            # Calculate averages
            if packet_loss_values:
                metadata["avg_packet_loss_rate"] = round(sum(packet_loss_values) / len(packet_loss_values), 4)

            if jitter_values:
                metadata["avg_jitter_ms"] = int(sum(jitter_values) / len(jitter_values))

            if rtt_values:
                metadata["avg_round_trip_ms"] = int(sum(rtt_values) / len(rtt_values))

            # Calculate quality score (0-1)
            if metadata["avg_packet_loss_rate"] is not None or metadata["avg_jitter_ms"] is not None or metadata["avg_round_trip_ms"] is not None:
                score = self._calculate_quality_score(
                    metadata.get("avg_packet_loss_rate"),
                    metadata.get("avg_jitter_ms"),
                    metadata.get("avg_round_trip_ms")
                )
                metadata["network_quality_score"] = score

                # Flag quality issues if score is below threshold
                if score is not None and score < 0.7:
                    metadata["had_quality_issues"] = True

            # Store aggregated data
            if connection_counts:
                metadata["connection_types"] = connection_counts

            if device_counts:
                metadata["device_types"] = device_counts

            if external_tenant_ids:
                metadata["external_domains"] = list(external_tenant_ids)

        except Exception as e:
            logger.warning(f"Error extracting enterprise metadata: {e}")

        return metadata

    def _parse_duration_ms(self, duration_str: str) -> int:
        """Parse ISO 8601 duration (e.g., 'PT0.015S') to milliseconds."""
        if not duration_str:
            return None
        try:
            # Format: PT<seconds>S (e.g., PT0.015S = 15ms)
            import re
            match = re.search(r'PT([\d.]+)S', duration_str)
            if match:
                seconds = float(match.group(1))
                return int(seconds * 1000)
        except:
            pass
        return None

    def _calculate_quality_score(self, packet_loss: float, jitter_ms: int, rtt_ms: int) -> float:
        """
        Compute 0-1 quality score based on standard thresholds.

        Good thresholds: packet_loss < 0.01, jitter < 30ms, rtt < 150ms
        """
        scores = []

        if packet_loss is not None:
            # 0 at 5% loss, 1 at 0% loss
            loss_score = max(0, 1 - (packet_loss / 0.05))
            scores.append(loss_score * 0.5)  # Weight: 50%

        if jitter_ms is not None:
            # 0 at 100ms, 1 at 0ms
            jitter_score = max(0, 1 - (jitter_ms / 100))
            scores.append(jitter_score * 0.25)  # Weight: 25%

        if rtt_ms is not None:
            # 0 at 400ms, 1 at 0ms
            rtt_score = max(0, 1 - (rtt_ms / 400))
            scores.append(rtt_score * 0.25)  # Weight: 25%

        if not scores:
            return None

        # Normalize to account for missing metrics
        total_weight = sum([0.5 if packet_loss is not None else 0,
                          0.25 if jitter_ms is not None else 0,
                          0.25 if rtt_ms is not None else 0])
        if total_weight > 0:
            return round(sum(scores) / total_weight, 2)

        return None

    def _extract_participants(self, call_record: Dict[str, Any]) -> list:
        """Extract participant list from callRecord.

        Note: Call record sessions only include user ID and displayName, NOT email.
        We must look up each user in Graph API to get their email address.

        Also extracts:
        - PSTN participants (phone dial-in) with phone numbers
        - Guest users (external Teams users)
        - ACS users (Azure Communication Services)
        """
        participants = []
        seen_ids = set()  # Track seen user IDs and phone numbers

        for session_data in call_record.get("sessions", []):
            for endpoint in ["caller", "callee"]:
                identity = session_data.get(endpoint, {}).get("identity", {})

                # Handle internal Teams users
                user = identity.get("user")
                if user and user.get("id"):
                    user_id = user["id"]

                    # Skip if already processed this user
                    if user_id in seen_ids:
                        continue
                    seen_ids.add(user_id)

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
                            "user_id": user_id,
                            "type": "internal"
                        })
                    continue

                # Handle PSTN/phone participants
                phone = identity.get("phone")
                if phone:
                    phone_id = phone.get("id", "")
                    display_name = phone.get("displayName", "")

                    # Use phone number as unique identifier
                    unique_id = phone_id or display_name
                    if unique_id and unique_id not in seen_ids:
                        seen_ids.add(unique_id)
                        participants.append({
                            "email": None,  # PSTN users don't have email
                            "name": display_name or "Phone Participant",
                            "phone": phone_id,
                            "role": "attendee",
                            "type": "pstn"
                        })
                        logger.debug(f"Found PSTN participant: {display_name} ({phone_id})")
                    continue

                # Handle guest users (external Teams users)
                guest = identity.get("guest")
                if guest:
                    guest_id = guest.get("id", "")
                    if guest_id and guest_id not in seen_ids:
                        seen_ids.add(guest_id)
                        participants.append({
                            "email": guest.get("email", "").lower() if guest.get("email") else None,
                            "name": guest.get("displayName", "Guest"),
                            "role": "attendee",
                            "type": "guest"
                        })
                        logger.debug(f"Found guest participant: {guest.get('displayName')}")
                    continue

                # Handle ACS users (Azure Communication Services - external)
                acs_user = identity.get("acsUser")
                if acs_user:
                    acs_id = acs_user.get("id", "")
                    if acs_id and acs_id not in seen_ids:
                        seen_ids.add(acs_id)
                        participants.append({
                            "email": None,
                            "name": acs_user.get("displayName", "External Participant"),
                            "role": "attendee",
                            "type": "external"
                        })
                        logger.debug(f"Found ACS participant: {acs_user.get('displayName')}")

        # Log summary of extracted participants
        internal = sum(1 for p in participants if p.get("type") == "internal")
        pstn = sum(1 for p in participants if p.get("type") == "pstn")
        guest = sum(1 for p in participants if p.get("type") == "guest")
        external = sum(1 for p in participants if p.get("type") == "external")
        logger.info(f"Extracted {len(participants)} participants: {internal} internal, {pstn} PSTN, {guest} guest, {external} external")

        return participants

    def _parse_datetime(self, dt_string: str) -> datetime:
        """Parse ISO datetime string to UTC-naive datetime.

        Graph API returns times in UTC. We store as UTC-naive and the
        display layer converts to Eastern timezone.
        """
        if not dt_string:
            return None
        try:
            dt = datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)  # Store as UTC-naive
        except:
            return None

    def _get_email_from_user_info(self, user_info: dict) -> str:
        """Extract email address from Graph API user info.

        Prefers the 'mail' field (actual email) over 'userPrincipalName' (UPN).
        UPN can be different from email for guests/external users.

        Args:
            user_info: User data from Graph API /users/{id} endpoint

        Returns:
            Email address string, or empty string if not found
        """
        # Prefer actual email address
        email = user_info.get("mail", "")
        if email:
            return email.lower()

        # Fall back to UPN only if it looks like an email (contains @)
        upn = user_info.get("userPrincipalName", "")
        if upn and "@" in upn:
            return upn.lower()

        return ""

    def _fetch_meeting_invitees(self, organizer_user_id: str, join_url: str) -> list:
        """
        Fetch meeting invitees from online meeting.

        Args:
            organizer_user_id: The organizer's user ID
            join_url: The meeting join URL

        Returns:
            List of invitee dicts with email and name
        """
        if not organizer_user_id or not join_url:
            return []

        try:
            # Find online meeting by join URL
            meetings = self.graph_client.get(
                f"/users/{organizer_user_id}/onlineMeetings",
                params={"$filter": f"joinWebUrl eq '{join_url}'"}
            )

            if not meetings.get("value"):
                logger.debug(f"No online meeting found for join URL")
                return []

            online_meeting = meetings["value"][0]
            participants_data = online_meeting.get("participants", {})

            invitees = []

            # Add organizer
            organizer = participants_data.get("organizer", {})
            if organizer.get("upn"):
                invitees.append({
                    "email": organizer["upn"].lower(),
                    "name": organizer.get("identity", {}).get("user", {}).get("displayName"),
                    "role": "organizer"
                })

            # Add attendees
            for attendee in participants_data.get("attendees", []):
                if attendee.get("upn"):
                    invitees.append({
                        "email": attendee["upn"].lower(),
                        "name": attendee.get("identity", {}).get("user", {}).get("displayName"),
                        "role": attendee.get("role", "attendee")
                    })

            logger.info(f"Found {len(invitees)} invitees from online meeting")
            return invitees

        except Exception as e:
            logger.warning(f"Could not fetch meeting invitees: {e}")
            return []

    async def backfill_recent_meetings(self, lookback_hours: int = 48, source: str = "automatic", triggered_by: str = "system") -> Dict[str, Any]:
        """
        Enhanced backfill with retry logic and progress tracking.

        Smart gap detection: Fills gap from last webhook to present instead of
        using fixed lookback period. Returns detailed statistics for monitoring.

        Args:
            lookback_hours: Maximum hours to look back (fallback if no previous webhook found)
            source: Source of backfill trigger ('automatic', 'manual', 'force')
            triggered_by: Who/what triggered the backfill (user email or 'system')

        Returns:
            Dict with statistics:
            - call_records_found, meetings_created, transcripts_found,
            - transcripts_pending, skipped_no_optin, jobs_created, errors
        """
        from ..core.database import BackfillRun

        stats = {
            "call_records_found": 0,
            "meetings_created": 0,
            "transcripts_found": 0,
            "transcripts_pending": 0,
            "skipped_no_optin": 0,
            "jobs_created": 0,
            "errors": 0
        }

        # Create BackfillRun record for tracking
        backfill_run = None
        cutoff = None

        try:
            with self.db.get_session() as session:
                backfill_run = BackfillRun(
                    lookback_hours=lookback_hours,
                    source=source,
                    triggered_by=triggered_by,
                    status="running"
                )
                session.add(backfill_run)
                session.commit()
                backfill_run_id = backfill_run.id

        except Exception as e:
            logger.warning(f"Could not create BackfillRun record: {e}")
            backfill_run_id = None

        try:
            # Calculate cutoff time - use the LATER of:
            # 1. Last webhook time minus 5 minutes (smart gap detection) - PREFERRED
            # 2. lookback_hours from now (maximum cap to prevent huge queries)
            # This ensures efficient backfill - only look back to last webhook, not full lookback
            max_lookback_cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

            with self.db.get_session() as session:
                last_webhook = session.query(ProcessedCallRecord).filter_by(
                    source='webhook'
                ).order_by(ProcessedCallRecord.processed_at.desc()).first()

                if last_webhook:
                    # Make processed_at timezone-aware if it's naive
                    processed_at = last_webhook.processed_at
                    if processed_at.tzinfo is None:
                        processed_at = processed_at.replace(tzinfo=timezone.utc)

                    gap_cutoff = processed_at - timedelta(minutes=5)
                    time_gap = datetime.now(timezone.utc) - processed_at
                    hours_gap = time_gap.total_seconds() / 3600

                    # Use the LATER time (more recent) - prefer gap detection for efficiency
                    # But cap at max_lookback_cutoff to prevent going too far back
                    if gap_cutoff > max_lookback_cutoff:
                        cutoff = gap_cutoff
                        logger.info(f"Using gap detection ({hours_gap:.1f}h since last webhook)")
                    else:
                        cutoff = max_lookback_cutoff
                        logger.info(f"Gap too large ({hours_gap:.1f}h), capping at {lookback_hours}h lookback")
                else:
                    # No webhooks - use requested lookback
                    cutoff = max_lookback_cutoff
                    logger.info(f"No webhooks found, backfilling last {lookback_hours} hours...")

            cutoff_str = cutoff.isoformat().replace('+00:00', 'Z')

            # Query callRecords API with PAGINATION support
            # Graph API returns max 60 results per page with @odata.nextLink for more
            logger.info(f"Querying callRecords since {cutoff_str}...")
            call_records = []
            page = 1

            # Initial request
            response = self.graph_client.get(
                "/communications/callRecords",
                params={
                    "$filter": f"startDateTime ge {cutoff_str}"
                }
            )

            page_records = response.get("value", [])
            call_records.extend(page_records)
            logger.info(f"Page {page}: {len(page_records)} callRecords")

            # Follow pagination links
            next_link = response.get("@odata.nextLink")
            while next_link:
                page += 1
                # The get() method already supports full URLs
                response = self.graph_client.get(next_link)
                page_records = response.get("value", [])
                call_records.extend(page_records)
                logger.info(f"Page {page}: {len(page_records)} callRecords (total: {len(call_records)})")
                next_link = response.get("@odata.nextLink")

            stats["call_records_found"] = len(call_records)
            logger.info(f"Found {len(call_records)} callRecords total across {page} pages")

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

                    # Skip 1-on-1 calls (less than 3 participants) - only capture group meetings
                    if len(participants) < 3:
                        logger.debug(f"Skipping 1-on-1 call {call_record_id} with {len(participants)} participants")
                        stats["skipped_no_optin"] += 1  # Reuse this counter for skipped calls
                        try:
                            with self.db.get_session() as session:
                                existing = session.query(ProcessedCallRecord).filter_by(
                                    call_record_id=call_record_id
                                ).first()
                                if not existing:
                                    session.add(ProcessedCallRecord(
                                        call_record_id=call_record_id,
                                        source="backfill"
                                    ))
                                    session.commit()
                        except Exception as e:
                            logger.debug(f"Could not mark {call_record_id} as processed: {e}")
                        continue

                    # Check for opted-in participants (for logging, but process all meetings now)
                    opted_in_participants = [
                        p for p in participants
                        if p.get("email") and self.pref_manager.get_user_preference(p["email"])
                    ]
                    has_opted_in = len(opted_in_participants) > 0

                    if not has_opted_in:
                        logger.info(f"No opted-in participants for callRecord {call_record_id}, will capture transcript only")

                    # Process the callRecord (creates meeting and fetches transcript for ALL meetings)
                    result = await self._process_call_record(call_record_id, source="backfill")

                    if result["status"] == "processed":
                        stats["meetings_created"] += 1
                        stats["jobs_created"] += 1
                    elif result["status"] == "job_exists":
                        # Meeting already has a pending/running job (from webhook or earlier backfill)
                        logger.info(f"Job already exists for meeting {result.get('meeting_id')}, skipping duplicate")
                        # Don't count as error - this is expected deduplication
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

            # Update BackfillRun with success stats
            if backfill_run_id:
                try:
                    with self.db.get_session() as session:
                        run = session.query(BackfillRun).filter_by(id=backfill_run_id).first()
                        if run:
                            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                            run.status = "completed"
                            run.cutoff_time = cutoff.replace(tzinfo=None) if cutoff else None
                            run.call_records_found = stats["call_records_found"]
                            run.meetings_created = stats["meetings_created"]
                            run.transcripts_found = stats["transcripts_found"]
                            run.transcripts_pending = stats["transcripts_pending"]
                            run.skipped_no_optin = stats["skipped_no_optin"]
                            run.jobs_created = stats["jobs_created"]
                            run.errors = stats["errors"]
                            session.commit()
                except Exception as e:
                    logger.warning(f"Could not update BackfillRun record: {e}")

            return stats

        except Exception as e:
            logger.error(f"Backfill failed: {e}", exc_info=True)
            # Update BackfillRun with failure status
            if backfill_run_id:
                try:
                    with self.db.get_session() as session:
                        run = session.query(BackfillRun).filter_by(id=backfill_run_id).first()
                        if run:
                            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                            run.status = "failed"
                            run.errors = stats.get("errors", 0) + 1
                            session.commit()
                except Exception:
                    pass
            raise
