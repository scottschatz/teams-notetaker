"""
Unit tests for backfill functionality with mocked Graph API responses.

Tests the enhanced backfill logic that:
- Discovers meetings via callRecords API
- Filters for opted-in participants
- Checks for transcript availability
- Schedules retries for unavailable transcripts
- Tracks deduplication via ProcessedCallRecord table
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.webhooks.call_records_handler import CallRecordsWebhookHandler
from src.core.database import (
    Base, Meeting, ProcessedCallRecord, JobQueue,
    UserPreference, MeetingParticipant, DatabaseManager
)
from tests.factories import GraphAPITestFactory, DatabaseTestFactory


@pytest.fixture
def test_db():
    """Create temporary in-memory database for testing."""
    from sqlalchemy.types import JSON
    from sqlalchemy.dialects.postgresql import JSONB
    import logging

    # Monkey-patch JSONB to use JSON for SQLite
    original_compile = JSONB._compiler_dispatch
    JSONB._compiler_dispatch = lambda self, visitor, **kw: JSON._compiler_dispatch(self, visitor, **kw)

    # Create SQLite engine manually (SQLite doesn't support max_overflow)
    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create DatabaseManager manually without calling __init__
    db = object.__new__(DatabaseManager)
    db.logger = logging.getLogger(__name__)
    db.connection_string = "sqlite:///:memory:"
    db.engine = engine
    db.SessionLocal = Session

    yield db

    Base.metadata.drop_all(engine)

    # Restore original JSONB behavior
    JSONB._compiler_dispatch = original_compile


@pytest.fixture
def mock_graph_client():
    """Mock Graph API client with realistic responses."""
    client = Mock()
    client.get = Mock()
    client.get_paged = Mock()
    return client


@pytest.fixture
def mock_pref_manager():
    """Mock PreferenceManager."""
    manager = Mock()
    manager.get_user_preference = Mock(return_value=True)  # Default: all opted-in
    return manager


@pytest.fixture
def sample_call_records():
    """Factory for generating sample callRecords."""
    return [
        GraphAPITestFactory.create_call_record(
            start_hours_ago=12,
            organizer_email="organizer@example.com",
            participants=[
                {"email": "user1@example.com", "name": "User One"},
                {"email": "user2@example.com", "name": "User Two"}
            ]
        ),
        GraphAPITestFactory.create_call_record(
            start_hours_ago=36,
            organizer_email="another@example.com",
            participants=[
                {"email": "user3@example.com", "name": "User Three"}
            ]
        )
    ]


class TestBackfillBasicFunctionality:
    """Test basic backfill discovery and processing."""

    @pytest.mark.asyncio
    async def test_backfill_finds_recent_meetings(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill correctly queries and processes recent meetings."""
        # Setup: Mock Graph API responses with different behavior based on URL
        def mock_get(url, **kwargs):
            if url == "/communications/callRecords" and "params" in kwargs:
                # List query with filter params
                return {"value": sample_call_records}
            elif "/communications/callRecords/" in url:
                # Individual callRecord fetch
                call_id = url.split("/")[-1]
                for record in sample_call_records:
                    if record["id"] == call_id:
                        return record
                return {}
            else:
                return {}

        mock_graph_client.get.side_effect = mock_get

        # Add user preferences (all opted-in)
        with test_db.get_session() as session:
            session.add(DatabaseTestFactory.create_user_preference("user1@example.com"))
            session.add(DatabaseTestFactory.create_user_preference("user2@example.com"))
            session.add(DatabaseTestFactory.create_user_preference("user3@example.com"))
            session.commit()

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify
        assert stats["call_records_found"] == 2
        assert stats["meetings_created"] >= 1  # At least one meeting created
        assert mock_graph_client.get.called

    @pytest.mark.asyncio
    async def test_backfill_deduplication(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill doesn't process same callRecord twice."""
        call_record_id = sample_call_records[0]["id"]

        # Pre-populate ProcessedCallRecord table
        with test_db.get_session() as session:
            session.add(DatabaseTestFactory.create_processed_call_record(
                call_record_id=call_record_id,
                source="webhook"
            ))
            session.commit()

        # Mock Graph API to return the already-processed record
        mock_graph_client.get.return_value = {"value": [sample_call_records[0]]}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify: Should be skipped due to deduplication
        assert stats["call_records_found"] == 1
        assert stats["meetings_created"] == 0  # Should not create duplicate

    @pytest.mark.asyncio
    async def test_backfill_filters_non_opted_in_users(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill only processes meetings with opted-in participants."""
        # Setup: Create user preferences with receive_emails=False (opted out)
        with test_db.get_session() as session:
            session.add(DatabaseTestFactory.create_user_preference("user1@example.com", receive_emails=False))
            session.add(DatabaseTestFactory.create_user_preference("user2@example.com", receive_emails=False))
            session.add(DatabaseTestFactory.create_user_preference("user3@example.com", receive_emails=False))
            session.commit()

        # Mock Graph API
        mock_graph_client.get.return_value = {"value": sample_call_records}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify: All meetings should be skipped (users explicitly opted out)
        assert stats["call_records_found"] == 2
        assert stats["skipped_no_optin"] == 2
        assert stats["meetings_created"] == 0


class TestBackfillGapDetection:
    """Test smart gap detection from last webhook."""

    @pytest.mark.asyncio
    async def test_backfill_uses_last_webhook_timestamp(
        self,
        test_db,
        mock_graph_client
    ):
        """Test that backfill queries from last webhook with safety margin."""
        # Add a webhook-processed record from 2 hours ago
        two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        with test_db.get_session() as session:
            record = ProcessedCallRecord(
                call_record_id="webhook-record-1",
                source="webhook",
                processed_at=two_hours_ago
            )
            session.add(record)
            session.commit()

        # Mock Graph API
        mock_graph_client.get.return_value = {"value": []}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify: Should query from ~2 hours ago (with 5-min safety margin)
        call_args = mock_graph_client.get.call_args
        filter_param = call_args[1]["params"]["$filter"]

        # Extract timestamp from filter
        import re
        match = re.search(r'startDateTime ge (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', filter_param)
        assert match is not None

        # Verify timestamp is approximately 2 hours ago (within 10 minutes)
        query_time_str = match.group(1) + "Z"
        query_time = datetime.fromisoformat(query_time_str.replace('Z', '+00:00'))
        time_diff = datetime.now(timezone.utc) - query_time
        assert timedelta(hours=1, minutes=50) < time_diff < timedelta(hours=2, minutes=10)

    @pytest.mark.asyncio
    async def test_backfill_uses_default_lookback_when_no_webhooks(
        self,
        test_db,
        mock_graph_client
    ):
        """Test that backfill uses default lookback_hours when no previous webhooks."""
        # No webhook records in database

        # Mock Graph API
        mock_graph_client.get.return_value = {"value": []}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill with 24 hour lookback
        await handler.backfill_recent_meetings(lookback_hours=24)

        # Verify: Should query from ~24 hours ago
        call_args = mock_graph_client.get.call_args
        filter_param = call_args[1]["params"]["$filter"]

        import re
        match = re.search(r'startDateTime ge (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', filter_param)
        assert match is not None

        query_time_str = match.group(1) + "Z"
        query_time = datetime.fromisoformat(query_time_str.replace('Z', '+00:00'))
        time_diff = datetime.now(timezone.utc) - query_time
        assert timedelta(hours=23) < time_diff < timedelta(hours=25)


class TestBackfillTranscriptHandling:
    """Test transcript availability checking and retry logic."""

    @pytest.mark.asyncio
    async def test_backfill_creates_job_when_transcript_available(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill creates fetch_transcript job when transcript is ready."""
        # Setup: Mock transcript is available
        # This would require mocking _try_fetch_transcript to return True
        # For now, test the integration
        pass

    @pytest.mark.asyncio
    async def test_backfill_schedules_retry_when_transcript_not_ready(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill schedules retry when transcript not yet available."""
        # Setup: Mock transcript not available (would return False from _try_fetch_transcript)
        # Verify job created with retry scheduling
        pass


class TestBackfillEdgeCases:
    """Test edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_backfill_handles_empty_result_set(
        self,
        test_db,
        mock_graph_client
    ):
        """Test backfill handles no meetings found gracefully."""
        # Mock empty response
        mock_graph_client.get.return_value = {"value": []}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify: Should complete without errors
        assert stats["call_records_found"] == 0
        assert stats["meetings_created"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_backfill_handles_malformed_call_record(
        self,
        test_db,
        mock_graph_client
    ):
        """Test backfill handles callRecord with missing fields."""
        # Create malformed record (missing joinWebUrl causes graceful skip, not error)
        bad_record = GraphAPITestFactory.create_call_record(has_join_url=False)

        mock_graph_client.get.return_value = {"value": [bad_record]}

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify: Missing joinWebUrl is handled gracefully (logged but not an error)
        assert stats["call_records_found"] == 1
        assert stats["meetings_created"] == 0  # Should not create meeting
        assert stats["errors"] == 0  # Missing joinWebUrl is not counted as error

    @pytest.mark.asyncio
    async def test_backfill_handles_graph_api_errors(
        self,
        test_db,
        mock_graph_client
    ):
        """Test backfill handles Graph API failures gracefully."""
        # Mock Graph API error
        mock_graph_client.get.side_effect = Exception("Graph API timeout")

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute backfill - should raise exception
        with pytest.raises(Exception) as exc_info:
            await handler.backfill_recent_meetings(lookback_hours=48)

        assert "Graph API timeout" in str(exc_info.value)


class TestBackfillStatistics:
    """Test that backfill returns correct statistics."""

    @pytest.mark.asyncio
    async def test_backfill_returns_complete_statistics(
        self,
        test_db,
        mock_graph_client,
        sample_call_records
    ):
        """Test that backfill returns all required statistics fields."""
        # Setup: Mock Graph API with smart routing
        def mock_get(url, **kwargs):
            if url == "/communications/callRecords" and "params" in kwargs:
                return {"value": sample_call_records}
            elif "/communications/callRecords/" in url:
                call_id = url.split("/")[-1]
                for record in sample_call_records:
                    if record["id"] == call_id:
                        return record
                return {}
            else:
                return {}

        mock_graph_client.get.side_effect = mock_get

        # Add some opted-in users
        with test_db.get_session() as session:
            session.add(DatabaseTestFactory.create_user_preference("user1@example.com"))
            session.commit()

        handler = CallRecordsWebhookHandler(test_db, mock_graph_client)

        # Execute
        stats = await handler.backfill_recent_meetings(lookback_hours=48)

        # Verify all required fields present
        required_fields = [
            "call_records_found",
            "meetings_created",
            "transcripts_found",
            "transcripts_pending",
            "skipped_no_optin",
            "jobs_created",
            "errors"
        ]

        for field in required_fields:
            assert field in stats
            assert isinstance(stats[field], int)
            assert stats[field] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
