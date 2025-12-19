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

                    # Fetch meeting details (subject, times) from onlineMeetings API
                    meeting_subject = "Teams Meeting"
                    # Default to current UTC time (naive) if not available
                    meeting_start_time = datetime.utcnow()
                    meeting_end_time = datetime.utcnow()
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
                            logger.info(f"Fetched meeting details: {meeting_subject}")
                        except Exception as e:
                            logger.warning(f"Could not fetch meeting details: {e}")

                    # Create meeting record with organizer info from notification
                    # Note: meeting_id from transcript notification is the online_meeting_id (MSp...)
                    meeting = Meeting(
                        meeting_id=meeting_id,
                        online_meeting_id=meeting_id,  # MSp... format from transcript notification
                        calendar_event_id=None,  # Not available from transcript notification
                        call_record_id=None,  # Not available from transcript notification
                        discovery_source="webhook",  # Discovered via webhook notification
                        subject=meeting_subject,
                        organizer_email=organizer_email,
                        organizer_name=organizer_name,
                        organizer_user_id=organizer_user_id,
                        start_time=meeting_start_time,
                        end_time=meeting_end_time,
                        status="queued",
                        participant_count=1  # At least the organizer
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

                # Look up meeting subject from online meeting details
                meeting_subject = "Unknown Meeting"
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
                            meeting_subject = meetings[0].get("subject") or "Teams Meeting"
                            logger.info(f"Found meeting subject: {meeting_subject}")
                    except Exception as e:
                        # Many callRecords are ad-hoc calls without scheduled meetings
                        logger.debug(f"Could not look up meeting subject for {organizer_user_id}: {e}")

                # Check if meeting already exists in database
                # Check multiple fields for deduplication (webhook vs calendar discovery)
                from sqlalchemy import or_
                existing_meeting = session.query(Meeting).filter(
                    or_(
                        Meeting.meeting_id == online_meeting_id,
                        Meeting.online_meeting_id == online_meeting_id,
                        Meeting.join_url == online_meeting_id
                    )
                ).first()

                if existing_meeting:
                    logger.info(f"Meeting {existing_meeting.id} already exists")
                    meeting_id = existing_meeting.id

                    # Update organizer_user_id if we have it and existing doesn't
                    if organizer_user_id and not existing_meeting.organizer_user_id:
                        existing_meeting.organizer_user_id = organizer_user_id
                        logger.info(f"Updated organizer_user_id for meeting {meeting_id}")
                else:
                    # Create meeting record from callRecord notification
                    meeting = Meeting(
                        meeting_id=online_meeting_id,
                        online_meeting_id=online_meeting_id,  # MSp... format from callRecord
                        calendar_event_id=None,  # Not available from callRecord
                        call_record_id=call_record_id,  # The callRecord ID
                        discovery_source="webhook",  # Discovered via webhook notification
                        subject=meeting_subject,
                        organizer_email=organizer_email,
                        organizer_name=organizer_name,
                        organizer_user_id=organizer_user_id,
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

                    # Add participants (skip internal participants without email)
                    from ..core.database import MeetingParticipant
                    participants_added = 0
                    for p in participants:
                        display_name = p.get("name") or "Unknown"
                        participant_type = p.get("type", "internal")
                        email = p.get("email", "")

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

                        participant = MeetingParticipant(
                            meeting_id=meeting_id,
                            email=email,
                            display_name=display_name,
                            role=p.get("role", "attendee"),
                            attended=True,
                            participant_type=participant_type
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

                    # Check for opted-in participants
                    opted_in_participants = [
                        p for p in participants
                        if self.pref_manager.get_user_preference(p["email"])
                    ]

                    if not opted_in_participants:
                        logger.info(f"No opted-in participants for callRecord {call_record_id}")
                        stats["skipped_no_optin"] += 1

                        # Still mark as processed to avoid re-checking
                        try:
                            with self.db.get_session() as session:
                                # Check if already exists first
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
                            # Duplicate key or other error - just skip
                            logger.debug(f"Could not mark {call_record_id} as processed: {e}")
                        continue

                    # Process the callRecord (creates meeting and tries to fetch transcript)
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

            return stats

        except Exception as e:
            logger.error(f"Backfill failed: {e}", exc_info=True)
            raise
