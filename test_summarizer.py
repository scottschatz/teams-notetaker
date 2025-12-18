#!/usr/bin/env python
"""Test script to manually run summarizer on meeting 177."""

import sys
from src.core.config import get_config
from src.core.database import DatabaseManager, Meeting, Transcript, Summary
from src.ai.summarizer import EnhancedMeetingSummarizer
from src.utils.vtt_parser import parse_vtt

def main():
    # Load config
    config = get_config()

    # Get database manager
    db = DatabaseManager(config.database.connection_string)
    session = db.get_session()

    try:
        # Get meeting 177
        meeting = session.query(Meeting).filter_by(id=177).first()
        if not meeting:
            print("Meeting 177 not found")
            return 1

        print(f"Meeting: {meeting.subject}")
        print(f"Start time: {meeting.start_time}")

        # Get transcript
        transcript = session.query(Transcript).filter_by(meeting_id=177).first()
        if not transcript:
            print("Transcript not found")
            return 1

        print(f"Transcript word count: {transcript.word_count}")

        # Get parsed content directly from database
        parsed_vtt = transcript.parsed_content
        if not parsed_vtt:
            print("No parsed content")
            return 1

        print(f"Parsed {len(parsed_vtt)} segments")

        # Create summarizer
        print("Creating summarizer...")
        summarizer = EnhancedMeetingSummarizer(config.claude)

        # Generate summary
        print("Generating summary (this takes 1-2 minutes for 7 API calls)...")
        print("Calling API stage 1/7: Action Items...")

        meeting_metadata = {
            "subject": meeting.subject,
            "organizer": meeting.organizer_name or "Unknown",
            "start_time": str(meeting.start_time),
            "duration_minutes": meeting.duration_minutes or 0,
            "participant_count": len(parsed_vtt) if parsed_vtt else 0
        }

        summary = summarizer.generate_enhanced_summary(
            transcript_segments=parsed_vtt,
            meeting_metadata=meeting_metadata
        )

        print("\n=== SUMMARY GENERATED ===")
        print(f"Action items: {len(summary.action_items)}")
        print(f"Decisions: {len(summary.decisions)}")
        print(f"Topics: {len(summary.topics)}")
        print(f"Highlights: {len(summary.highlights)}")
        print(f"Mentions: {len(summary.mentions)}")
        print(f"Key numbers: {len(summary.key_numbers)}")
        print(f"Summary length: {len(summary.overall_summary)} chars")

        # Save to database
        print("\nSaving to database...")
        db_summary = Summary(
            meeting_id=177,
            transcript_id=transcript.id,  # Add required transcript_id
            summary_text=summary.overall_summary,
            action_items_json=summary.action_items,
            decisions_json=summary.decisions,
            topics_json=summary.topics,
            highlights_json=summary.highlights,
            mentions_json=summary.mentions,
            key_numbers_json=summary.key_numbers
        )
        session.add(db_summary)
        session.commit()

        print(f"Summary saved with ID: {db_summary.id}")

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
