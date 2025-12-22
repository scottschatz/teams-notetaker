#!/usr/bin/env python3
"""
Backfill chat_id for existing meetings.

This script extracts chat_id from join_url for meetings that have a join_url
but are missing chat_id. This enables chat event detection (recording_started,
transcript_available) for transcript fetch retry logic.
"""

import sys
import os
import re
import urllib.parse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import get_config
from src.core.database import DatabaseManager, Meeting
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_chat_id_from_url(join_url: str) -> str:
    """Extract Teams chat ID from meeting join URL."""
    if not join_url:
        return None

    try:
        decoded = urllib.parse.unquote(join_url)
        match = re.search(r'(19:[^/]+@thread\.v2)', decoded)
        if match:
            return match.group(1)
    except Exception as e:
        logger.debug(f"Could not extract chat_id from URL: {e}")

    return None


def backfill_chat_ids():
    """Backfill chat_id for existing meetings."""
    config = get_config()
    db = DatabaseManager(config.database.connection_string)

    updated = 0
    skipped = 0
    no_url = 0

    with db.get_session() as session:
        # Get all meetings without chat_id but with join_url
        meetings = session.query(Meeting).filter(
            Meeting.chat_id.is_(None) | (Meeting.chat_id == ''),
            Meeting.join_url.isnot(None),
            Meeting.join_url != ''
        ).all()

        logger.info(f"Found {len(meetings)} meetings to check for chat_id backfill")

        for meeting in meetings:
            chat_id = extract_chat_id_from_url(meeting.join_url)

            if chat_id:
                meeting.chat_id = chat_id
                updated += 1
                if updated % 100 == 0:
                    logger.info(f"Updated {updated} meetings...")
                    session.commit()
            else:
                skipped += 1

        session.commit()

    # Also check meetings with online_meeting_id as fallback
    with db.get_session() as session:
        meetings = session.query(Meeting).filter(
            Meeting.chat_id.is_(None) | (Meeting.chat_id == ''),
            Meeting.online_meeting_id.isnot(None),
            Meeting.online_meeting_id != ''
        ).all()

        logger.info(f"Found {len(meetings)} additional meetings to check via online_meeting_id")

        for meeting in meetings:
            chat_id = extract_chat_id_from_url(meeting.online_meeting_id)

            if chat_id:
                meeting.chat_id = chat_id
                updated += 1
            else:
                skipped += 1

        session.commit()

    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped (no extractable chat_id)")


if __name__ == "__main__":
    backfill_chat_ids()
