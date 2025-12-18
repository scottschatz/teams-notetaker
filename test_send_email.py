#!/usr/bin/env python
"""Test script to send email for meeting 177."""

import sys
from src.core.config import get_config
from src.core.database import DatabaseManager, Meeting, Transcript, Summary, MeetingParticipant
from src.graph.mail import EmailSender
from src.graph.client import GraphAPIClient

def main():
    # Load config
    config = get_config()

    # Get database manager
    db = DatabaseManager(config.database.connection_string)
    session = db.get_session()

    try:
        # Get meeting, transcript, summary
        meeting = session.query(Meeting).filter_by(id=177).first()
        transcript = session.query(Transcript).filter_by(meeting_id=177).first()
        summary = session.query(Summary).filter_by(meeting_id=177).order_by(Summary.id.desc()).first()
        participants = session.query(MeetingParticipant).filter_by(meeting_id=177).all()

        if not meeting or not summary:
            print(f"Meeting or summary not found")
            return 1

        print(f"Meeting: {meeting.subject}")
        print(f"Summary ID: {summary.id}")
        print(f"Participants: {len(participants)}")

        # Build metadata (matching production keys)
        meeting_metadata = {
            "subject": meeting.subject,
            "organizer_name": meeting.organizer_name,  # Correct key name
            "organizer_email": meeting.organizer_email,
            "start_time": meeting.start_time,
            "end_time": meeting.end_time,
            "duration_minutes": meeting.duration_minutes,
            "join_url": meeting.join_url,
            "recording_url": meeting.recording_url,
            "chat_id": meeting.chat_id,
            "transcript_sharepoint_url": transcript.transcript_sharepoint_url if transcript else None
        }

        # Format meeting time for subject (like production)
        import pytz
        eastern = pytz.timezone('America/New_York')
        if meeting.start_time:
            start_utc = meeting.start_time.replace(tzinfo=pytz.UTC) if meeting.start_time.tzinfo is None else meeting.start_time
            meeting_time_eastern = start_utc.astimezone(eastern)
            time_str = meeting_time_eastern.strftime("%a, %b %d at %I:%M %p %Z")
        else:
            time_str = "Unknown Time"

        # Build enhanced summary data
        enhanced_summary_data = {
            "action_items": summary.action_items_json or [],
            "decisions": summary.decisions_json or [],
            "topics": summary.topics_json or [],
            "highlights": summary.highlights_json or [],
            "mentions": summary.mentions_json or [],
            "key_numbers": summary.key_numbers_json or []
        }

        print(f"Key numbers: {len(enhanced_summary_data['key_numbers'])}")

        # Initialize Graph API client for participant enrichment
        graph_client = GraphAPIClient(config.graph_api)

        # Build participants dict with Graph API enrichment (job titles, photos)
        # Deduplicate by email like production code does
        seen_emails = set()
        participants_dict = []
        for p in participants:
            email_lower = p.email.lower() if p.email else ""
            if email_lower and email_lower not in seen_emails:
                # Enrich participant with photo and job title from Graph API
                enriched = graph_client.enrich_user_with_photo_and_title(
                    p.email, p.display_name
                )

                participants_dict.append({
                    "display_name": p.display_name,
                    "email": p.email,
                    "job_title": enriched.get("jobTitle"),
                    "photo_base64": enriched.get("photo_base64"),
                    "is_organizer": (p.role == "organizer"),
                    "_speaker_data": {
                        "duration_minutes": 0,  # Not in database
                        "percentage": 0,  # Not in database
                        "word_count": 0  # Not in database
                    }
                })
                seen_emails.add(email_lower)

        # Build transcript stats
        transcript_stats = {
            "word_count": transcript.word_count if transcript else 0,
            "duration_minutes": meeting.duration_minutes or 0,
            "speaker_count": len(participants)
        }

        # Send email
        print("\nSending email (debug mode: only to Scott.Schatz@townsquaremedia.com)...")
        email_sender = EmailSender(graph_client)

        email_sender.send_meeting_summary(
            from_email="noreply@townsquaremedia.com",
            to_emails=["Scott.Schatz@townsquaremedia.com"],  # Debug mode
            subject=f"Meeting Summary: {meeting.subject} ({time_str})",
            summary_markdown=summary.summary_text,
            meeting_metadata=meeting_metadata,
            enhanced_summary_data=enhanced_summary_data,
            transcript_content=None,
            participants=participants_dict,
            transcript_stats=transcript_stats,
            include_footer=True
        )

        print("\n✅ Email sent successfully!")
        print("Check your inbox for the new email format with restructured sections.")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        session.close()

if __name__ == "__main__":
    sys.exit(main())
