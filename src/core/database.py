"""
Database models and management for Teams Meeting Transcript Summarizer.

This module contains all SQLAlchemy models and the DatabaseManager class
for database operations.
"""

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    CheckConstraint,
    DECIMAL,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.sql import func
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import logging

Base = declarative_base()


# ============================================================================
# PILOT PROGRAM & CONFIGURATION
# ============================================================================


class PilotUser(Base):
    """Users in the pilot program."""

    __tablename__ = "pilot_users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(500))
    added_at = Column(DateTime, default=func.now())
    added_by = Column(String(255))
    is_active = Column(Boolean, default=True, index=True)
    notes = Column(Text)


class AppConfig(Base):
    """Runtime application configuration (editable via dashboard)."""

    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text)  # JSON serialized value
    data_type = Column(String(20), nullable=False)  # 'string', 'int', 'bool', 'json'
    description = Column(Text)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    updated_by = Column(String(255))


class Exclusion(Base):
    """Users/domains to exclude from processing."""

    __tablename__ = "exclusions"

    id = Column(Integer, primary_key=True)
    type = Column(String(20), nullable=False)  # 'user', 'domain', 'organizer'
    value = Column(String(255), nullable=False)
    reason = Column(Text)
    added_at = Column(DateTime, default=func.now())
    added_by = Column(String(255))
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_type_active", "type", "is_active"),
        CheckConstraint("type IN ('user', 'domain', 'organizer')", name="valid_exclusion_type"),
    )


# ============================================================================
# MEETINGS & PROCESSING
# ============================================================================


class Meeting(Base):
    """Discovered meetings and their processing status."""

    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(String(255), unique=True, nullable=False, index=True)  # Graph API ID

    # Meeting metadata
    subject = Column(String(500))
    organizer_email = Column(String(255), index=True)
    organizer_name = Column(String(500))
    organizer_user_id = Column(String(255))  # User ID (GUID) for getAllTranscripts API
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime)
    duration_minutes = Column(Integer)
    participant_count = Column(Integer)

    # Discovery metadata
    discovered_at = Column(DateTime, default=func.now(), index=True)
    discovery_run_id = Column(Integer, ForeignKey("processing_runs.id"))

    # Processing status
    status = Column(
        String(50),
        default="discovered",
        index=True,
    )  # 'discovered', 'queued', 'processing', 'completed', 'failed', 'skipped'

    # Processing timestamps
    queued_at = Column(DateTime)
    processing_started_at = Column(DateTime)
    processing_completed_at = Column(DateTime)

    # Results
    has_transcript = Column(Boolean, default=False)
    has_summary = Column(Boolean, default=False)
    has_distribution = Column(Boolean, default=False)

    # Error tracking
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    last_retry_at = Column(DateTime)

    # Filtering metadata
    is_pilot_eligible = Column(Boolean)
    skip_reason = Column(String(255))

    # Relationships
    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    transcript = relationship("Transcript", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    summary = relationship("Summary", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    distributions = relationship("Distribution", back_populates="meeting", cascade="all, delete-orphan")
    jobs = relationship("JobQueue", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered', 'queued', 'processing', 'completed', 'failed', 'skipped')",
            name="valid_meeting_status",
        ),
    )


class MeetingParticipant(Base):
    """Meeting participants/attendees."""

    __tablename__ = "meeting_participants"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), index=True)
    display_name = Column(String(500))
    role = Column(String(50))  # 'organizer', 'presenter', 'attendee'
    joined_at = Column(DateTime)
    left_at = Column(DateTime)
    is_pilot_user = Column(Boolean, default=False, index=True)

    # Relationships
    meeting = relationship("Meeting", back_populates="participants")


# ============================================================================
# JOB QUEUE SYSTEM
# ============================================================================


class JobQueue(Base):
    """Job queue for asynchronous processing."""

    __tablename__ = "job_queue"

    id = Column(Integer, primary_key=True)

    # Job identification
    job_type = Column(
        String(50), nullable=False, index=True
    )  # 'fetch_transcript', 'generate_summary', 'distribute'
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)

    # Job priority (1=highest, 10=lowest)
    priority = Column(Integer, default=5)

    # Job status
    status = Column(
        String(50), default="pending", index=True
    )  # 'pending', 'running', 'completed', 'failed', 'retrying'

    # Timestamps
    created_at = Column(DateTime, default=func.now(), index=True)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    next_retry_at = Column(DateTime, index=True)  # For exponential backoff

    # Retry logic
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Job data (JSON)
    input_data = Column(JSONB)  # Job-specific parameters
    output_data = Column(JSONB)  # Results from job execution
    error_message = Column(Text)
    error_stack = Column(Text)

    # Worker tracking
    worker_id = Column(String(100), index=True)  # Worker that claimed this job
    heartbeat_at = Column(DateTime)  # Last worker heartbeat

    # Dependencies
    depends_on_job_id = Column(Integer, ForeignKey("job_queue.id"))

    # Relationships
    meeting = relationship("Meeting", back_populates="jobs")

    __table_args__ = (
        Index("idx_status_priority", "status", priority.desc(), "created_at"),
        Index(
            "idx_job_queue_next_job",
            "status",
            priority.desc(),
            "created_at",
            postgresql_where=(status.in_(["pending", "retrying"])),
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'retrying')", name="valid_job_status"
        ),
        CheckConstraint(
            "job_type IN ('fetch_transcript', 'generate_summary', 'distribute')", name="valid_job_type"
        ),
    )


# ============================================================================
# TRANSCRIPTS & SUMMARIES
# ============================================================================


class Transcript(Base):
    """Meeting transcripts (VTT content)."""

    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)

    # Raw transcript
    vtt_content = Column(Text)  # Full VTT file content
    vtt_url = Column(String(1000))  # Graph API URL (may expire)

    # Parsed transcript
    parsed_content = Column(JSONB)  # Array of {speaker, timestamp, text} objects
    speaker_count = Column(Integer)
    word_count = Column(Integer)

    # Metadata
    fetched_at = Column(DateTime, default=func.now(), index=True)
    language = Column(String(10))  # e.g., 'en-US'

    # Relationships
    meeting = relationship("Meeting", back_populates="transcript")
    summary = relationship("Summary", back_populates="transcript", uselist=False, cascade="all, delete-orphan")


class Summary(Base):
    """AI-generated meeting summaries."""

    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)

    # Summary content
    summary_text = Column(Text, nullable=False)
    summary_html = Column(Text)  # Formatted version with markdown

    # AI metadata
    model = Column(String(100), default="claude-sonnet-4-20250514")
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)

    # Processing details
    generated_at = Column(DateTime, default=func.now(), index=True)
    generation_time_ms = Column(Integer)

    # Quality metadata
    confidence_score = Column(DECIMAL(3, 2))  # 0.00-1.00

    # Relationships
    meeting = relationship("Meeting", back_populates="summary")
    transcript = relationship("Transcript", back_populates="summary")
    distributions = relationship("Distribution", back_populates="summary", cascade="all, delete-orphan")


# ============================================================================
# DISTRIBUTION TRACKING
# ============================================================================


class Distribution(Base):
    """Email and Teams chat distribution tracking."""

    __tablename__ = "distributions"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    summary_id = Column(Integer, ForeignKey("summaries.id", ondelete="CASCADE"), nullable=False, index=True)

    # Distribution details
    distribution_type = Column(String(20), nullable=False)  # 'email', 'teams_chat'
    recipient = Column(String(255), nullable=False, index=True)

    # Status
    status = Column(String(50), default="pending", index=True)  # 'pending', 'sent', 'failed', 'retrying'

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    sent_at = Column(DateTime, index=True)

    # External IDs (from Graph API)
    message_id = Column(String(500))  # Email message ID or Teams chat ID
    conversation_id = Column(String(500))  # Teams conversation/thread ID

    # Error tracking
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)

    # Relationships
    meeting = relationship("Meeting", back_populates="distributions")
    summary = relationship("Summary", back_populates="distributions")

    __table_args__ = (
        CheckConstraint("distribution_type IN ('email', 'teams_chat')", name="valid_distribution_type"),
        CheckConstraint("status IN ('pending', 'sent', 'failed', 'retrying')", name="valid_distribution_status"),
    )


# ============================================================================
# PROCESSING RUNS (AUDIT LOG)
# ============================================================================


class ProcessingRun(Base):
    """Audit log of discovery/processing runs."""

    __tablename__ = "processing_runs"

    id = Column(Integer, primary_key=True)

    # Run metadata
    started_at = Column(DateTime, default=func.now(), index=True)
    completed_at = Column(DateTime)
    status = Column(String(50), default="running", index=True)  # 'running', 'completed', 'failed'

    # Discovery scope
    lookback_hours = Column(Integer)  # How far back we searched
    mode = Column(String(20))  # 'pilot', 'production'

    # Statistics
    meetings_discovered = Column(Integer, default=0)
    meetings_queued = Column(Integer, default=0)
    meetings_skipped = Column(Integer, default=0)
    jobs_created = Column(Integer, default=0)

    # Error tracking
    error_message = Column(Text)

    __table_args__ = (CheckConstraint("status IN ('running', 'completed', 'failed')", name="valid_run_status"),)


# ============================================================================
# AUTHENTICATION & SESSIONS
# ============================================================================


class UserSession(Base):
    """User authentication sessions."""

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    user_email = Column(String(255), nullable=False, index=True)
    session_token = Column(String(500), unique=True, nullable=False, index=True)  # JWT token

    # Session metadata
    login_at = Column(DateTime, default=func.now())
    logout_at = Column(DateTime)
    last_activity = Column(DateTime, default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime, nullable=False, index=True)

    # Auth method
    auth_method = Column(String(20), nullable=False)  # 'password', 'sso'

    # Security
    ip_address = Column(String(50))
    user_agent = Column(Text)

    # User info (cached from SSO)
    user_role = Column(String(20), default="user")  # 'admin', 'manager', 'user'
    display_name = Column(String(500))


class AuthFlow(Base):
    """OAuth authentication flow tracking (from invoice-bot pattern)."""

    __tablename__ = "auth_flows"

    id = Column(Integer, primary_key=True)
    state = Column(String(255), unique=True, nullable=False, index=True)  # OAuth state parameter
    flow_data = Column(JSONB, nullable=False)  # Encrypted MSAL flow data

    # Lifecycle
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=False, index=True)  # 10-minute expiration
    used = Column(Boolean, default=False)  # One-time use only

    # Security
    ip_address = Column(String(50))

    __table_args__ = (
        Index("idx_auth_flows_cleanup", "expires_at", postgresql_where=(used == False)),
    )


# ============================================================================
# SYSTEM HEALTH
# ============================================================================


class SystemHealthCheck(Base):
    """System health check logs."""

    __tablename__ = "system_health_checks"

    id = Column(Integer, primary_key=True)
    check_type = Column(String(50), nullable=False, index=True)  # 'graph_api', 'claude_api', 'database', 'worker'
    status = Column(String(20), nullable=False, index=True)  # 'healthy', 'degraded', 'down'

    # Timestamps
    checked_at = Column(DateTime, default=func.now(), index=True)

    # Details
    response_time_ms = Column(Integer)
    error_message = Column(Text)
    details = Column(JSONB)


# ============================================================================
# DATABASE MANAGER
# ============================================================================


class DatabaseManager:
    """Database operations manager."""

    def __init__(self, connection_string: str):
        """Initialize database manager with connection string."""
        self.logger = logging.getLogger(__name__)
        self.connection_string = connection_string

        # Create engine with connection pooling
        self.engine = create_engine(
            connection_string,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # Verify connections before using
            echo=False,  # Set to True for SQL logging
        )

        # Create session factory
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def create_tables(self):
        """Create all database tables."""
        Base.metadata.create_all(self.engine)
        self.logger.info("Database tables created successfully")

    def drop_tables(self):
        """Drop all database tables (use with caution!)."""
        Base.metadata.drop_all(self.engine)
        self.logger.warning("All database tables dropped")

    def get_session(self):
        """Get a new database session."""
        return self.SessionLocal()

    # ========================================================================
    # PILOT USER METHODS
    # ========================================================================

    def add_pilot_user(self, email: str, added_by: str = "admin", **kwargs) -> PilotUser:
        """Add user to pilot program."""
        session = self.get_session()
        try:
            user = PilotUser(email=email.lower(), added_by=added_by, **kwargs)
            session.add(user)
            session.commit()
            session.refresh(user)
            self.logger.info(f"Added pilot user: {email}")
            return user
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to add pilot user {email}: {e}")
            raise
        finally:
            session.close()

    def is_pilot_user(self, email: str) -> bool:
        """Check if user is in active pilot program."""
        session = self.get_session()
        try:
            count = (
                session.query(PilotUser).filter(PilotUser.email == email.lower(), PilotUser.is_active == True).count()
            )
            return count > 0
        finally:
            session.close()

    def get_pilot_users(self, active_only: bool = True) -> List[PilotUser]:
        """Get all pilot users."""
        session = self.get_session()
        try:
            query = session.query(PilotUser)
            if active_only:
                query = query.filter(PilotUser.is_active == True)
            return query.all()
        finally:
            session.close()

    # ========================================================================
    # MEETING METHODS
    # ========================================================================

    def create_meeting(self, **kwargs) -> Meeting:
        """Create new meeting record."""
        session = self.get_session()
        try:
            meeting = Meeting(**kwargs)
            session.add(meeting)
            session.commit()
            session.refresh(meeting)
            self.logger.info(f"Created meeting: {meeting.meeting_id}")
            return meeting
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to create meeting: {e}")
            raise
        finally:
            session.close()

    def get_meeting_by_graph_id(self, meeting_id: str) -> Optional[Meeting]:
        """Find meeting by Graph API ID."""
        session = self.get_session()
        try:
            return session.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
        finally:
            session.close()

    def get_meeting_by_id(self, id: int) -> Optional[Meeting]:
        """Find meeting by internal ID."""
        session = self.get_session()
        try:
            return session.query(Meeting).get(id)
        finally:
            session.close()

    # ========================================================================
    # JOB QUEUE METHODS
    # ========================================================================

    def enqueue_job(self, job_type: str, meeting_id: int, **kwargs) -> JobQueue:
        """Add job to queue."""
        session = self.get_session()
        try:
            job = JobQueue(job_type=job_type, meeting_id=meeting_id, **kwargs)
            session.add(job)
            session.commit()
            session.refresh(job)
            self.logger.info(f"Enqueued job {job.id}: {job_type} for meeting {meeting_id}")
            return job
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to enqueue job: {e}")
            raise
        finally:
            session.close()

    def claim_next_job(self, worker_id: str) -> Optional[JobQueue]:
        """
        Atomically claim next available job using FOR UPDATE SKIP LOCKED.

        This ensures that multiple workers can safely claim jobs concurrently
        without conflicts or duplicate processing.
        """
        session = self.get_session()
        try:
            from sqlalchemy import text

            # Raw SQL for atomic job claiming with row locking
            query = text(
                """
                UPDATE job_queue
                SET status = 'running',
                    worker_id = :worker_id,
                    started_at = NOW(),
                    heartbeat_at = NOW()
                WHERE id = (
                    SELECT id FROM job_queue
                    WHERE status IN ('pending', 'retrying')
                      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                      AND (depends_on_job_id IS NULL OR
                           depends_on_job_id IN (SELECT id FROM job_queue WHERE status = 'completed'))
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id;
            """
            )

            result = session.execute(query, {"worker_id": worker_id})
            row = result.fetchone()

            if row:
                job_id = row[0]
                session.commit()

                # Fetch the full job object
                job = session.query(JobQueue).get(job_id)
                self.logger.info(f"Worker {worker_id} claimed job {job_id}")
                return job
            else:
                self.logger.debug(f"No jobs available for worker {worker_id}")
                return None

        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to claim job: {e}")
            raise
        finally:
            session.close()

    def update_job_status(self, job_id: int, status: str, **kwargs):
        """Update job status and metadata."""
        session = self.get_session()
        try:
            job = session.query(JobQueue).get(job_id)
            if job:
                job.status = status
                for key, value in kwargs.items():
                    setattr(job, key, value)
                session.commit()
                self.logger.info(f"Updated job {job_id}: status={status}")
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to update job {job_id}: {e}")
            raise
        finally:
            session.close()

    # ========================================================================
    # AUTH METHODS (from invoice-bot pattern)
    # ========================================================================

    def save_auth_flow(self, state: str, flow_data: dict, ip_address: str = None) -> bool:
        """Save OAuth flow to database."""
        session = self.get_session()
        try:
            auth_flow = AuthFlow(
                state=state,
                flow_data=flow_data,
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                ip_address=ip_address,
            )
            session.add(auth_flow)
            session.commit()
            self.logger.info(f"Saved auth flow: {state}")
            return True
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to save auth flow: {e}")
            return False
        finally:
            session.close()

    def get_auth_flow(self, state: str) -> Optional[dict]:
        """Retrieve and mark auth flow as used."""
        session = self.get_session()
        try:
            auth_flow = (
                session.query(AuthFlow)
                .filter(AuthFlow.state == state, AuthFlow.used == False, AuthFlow.expires_at > datetime.utcnow())
                .first()
            )

            if auth_flow:
                # Mark as used (one-time use)
                auth_flow.used = True
                session.commit()
                self.logger.info(f"Retrieved and marked auth flow as used: {state}")
                return auth_flow.flow_data
            else:
                self.logger.warning(f"Auth flow not found or expired: {state}")
                return None
        finally:
            session.close()

    def cleanup_expired_auth_flows(self) -> int:
        """Remove expired auth flows."""
        session = self.get_session()
        try:
            deleted = session.query(AuthFlow).filter(AuthFlow.expires_at < datetime.utcnow()).delete()
            session.commit()
            if deleted > 0:
                self.logger.info(f"Cleaned up {deleted} expired auth flows")
            return deleted
        finally:
            session.close()

    # ========================================================================
    # SESSION METHODS
    # ========================================================================

    def create_session(self, user_email: str, session_token: str, **kwargs) -> UserSession:
        """Create user session."""
        session = self.get_session()
        try:
            user_session = UserSession(user_email=user_email.lower(), session_token=session_token, **kwargs)
            session.add(user_session)
            session.commit()
            session.refresh(user_session)
            self.logger.info(f"Created session for user: {user_email}")
            return user_session
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to create session: {e}")
            raise
        finally:
            session.close()

    def get_session_by_token(self, session_token: str) -> Optional[UserSession]:
        """Get session by token."""
        session = self.get_session()
        try:
            return (
                session.query(UserSession)
                .filter(
                    UserSession.session_token == session_token,
                    UserSession.logout_at == None,
                    UserSession.expires_at > datetime.utcnow(),
                )
                .first()
            )
        finally:
            session.close()

    # ========================================================================
    # ANALYTICS METHODS
    # ========================================================================

    def get_dashboard_stats(self) -> dict:
        """Get statistics for dashboard."""
        session = self.get_session()
        try:
            from sqlalchemy import func

            # Total meetings
            total_meetings = session.query(func.count(Meeting.id)).scalar()

            # Meetings by status
            status_counts = dict(
                session.query(Meeting.status, func.count(Meeting.id)).group_by(Meeting.status).all()
            )

            # Jobs by status
            job_counts = dict(session.query(JobQueue.status, func.count(JobQueue.id)).group_by(JobQueue.status).all())

            # Recent processing runs
            recent_runs = (
                session.query(ProcessingRun).order_by(ProcessingRun.started_at.desc()).limit(10).all()
            )

            return {
                "total_meetings": total_meetings,
                "meeting_status": status_counts,
                "job_status": job_counts,
                "recent_runs": recent_runs,
            }
        finally:
            session.close()

    # ========================================================================
    # CONFIGURATION METHODS
    # ========================================================================

    def seed_default_config(self):
        """Seed default configuration values."""
        session = self.get_session()
        try:
            defaults = [
                {
                    "key": "polling_interval_minutes",
                    "value": "5",
                    "data_type": "int",
                    "description": "How often to poll for new meetings",
                },
                {
                    "key": "lookback_hours",
                    "value": "48",
                    "data_type": "int",
                    "description": "How far back to search for meetings",
                },
                {
                    "key": "pilot_mode_enabled",
                    "value": "true",
                    "data_type": "bool",
                    "description": "Enable pilot mode filtering",
                },
                {
                    "key": "max_concurrent_jobs",
                    "value": "5",
                    "data_type": "int",
                    "description": "Max concurrent job processing",
                },
                {
                    "key": "job_timeout_minutes",
                    "value": "10",
                    "data_type": "int",
                    "description": "Max time for a single job",
                },
                {
                    "key": "summary_max_tokens",
                    "value": "2000",
                    "data_type": "int",
                    "description": "Max tokens for Claude summary",
                },
                {
                    "key": "email_enabled",
                    "value": "true",
                    "data_type": "bool",
                    "description": "Enable email distribution",
                },
                {
                    "key": "teams_chat_enabled",
                    "value": "true",
                    "data_type": "bool",
                    "description": "Enable Teams chat posting",
                },
                {
                    "key": "minimum_meeting_duration_minutes",
                    "value": "5",
                    "data_type": "int",
                    "description": "Skip meetings shorter than this",
                },
                {
                    "key": "worker_heartbeat_interval_seconds",
                    "value": "30",
                    "data_type": "int",
                    "description": "Worker heartbeat frequency",
                },
            ]

            for config_data in defaults:
                # Check if already exists
                existing = session.query(AppConfig).filter(AppConfig.key == config_data["key"]).first()
                if not existing:
                    config = AppConfig(**config_data)
                    session.add(config)

            session.commit()
            self.logger.info("Seeded default configuration values")
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to seed config: {e}")
            raise
        finally:
            session.close()
