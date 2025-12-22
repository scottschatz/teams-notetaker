#!/usr/bin/env python3
"""
Backfill Azure AD properties for existing meeting participants.

This script fetches job_title, department, office_location, and company_name
from Azure AD for all internal participants that don't already have this data.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import get_config
from src.core.database import DatabaseManager, MeetingParticipant
from src.graph.client import GraphAPIClient
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def backfill_azure_ad_properties():
    """Backfill Azure AD properties for existing participants."""
    config = get_config()
    db = DatabaseManager(config.database.connection_string)
    graph_client = GraphAPIClient(config.graph_api)

    updated = 0
    skipped = 0
    errors = 0

    with db.get_session() as session:
        # Get all internal participants without Azure AD data
        participants = session.query(MeetingParticipant).filter(
            MeetingParticipant.participant_type == 'internal',
            MeetingParticipant.email.isnot(None),
            MeetingParticipant.email != '',
            MeetingParticipant.department.is_(None)  # Not yet populated
        ).all()

        logger.info(f"Found {len(participants)} participants to backfill")

        # Group by email to avoid duplicate lookups
        emails_seen = {}

        for p in participants:
            email = p.email.lower() if p.email else None
            if not email:
                skipped += 1
                continue

            # Check if we already looked up this email
            if email in emails_seen:
                user_details = emails_seen[email]
            else:
                try:
                    user_details = graph_client.get_user_details(email)
                    emails_seen[email] = user_details
                except Exception as e:
                    logger.debug(f"Could not fetch details for {email}: {e}")
                    emails_seen[email] = None
                    errors += 1
                    continue

            if user_details:
                p.job_title = user_details.get("jobTitle")
                p.department = user_details.get("department")
                p.office_location = user_details.get("officeLocation")
                p.company_name = user_details.get("companyName")
                updated += 1

                if updated % 50 == 0:
                    logger.info(f"Updated {updated} participants...")
                    session.commit()
            else:
                skipped += 1

        session.commit()

    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped, {errors} errors")
    logger.info(f"Unique emails looked up: {len(emails_seen)}")


if __name__ == "__main__":
    backfill_azure_ad_properties()
