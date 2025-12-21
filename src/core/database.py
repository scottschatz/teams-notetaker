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
    UniqueConstraint,
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
    meeting_id = Column(String(255), unique=True, nullable=False, index=True)  # Graph API ID (legacy - mixed types)

    # Explicit ID types (NEW - prefer these over meeting_id)
    online_meeting_id = Column(String(500), index=True)  # MSp... format, required for transcript API
    calendar_event_id = Column(String(500), index=True)  # AAMk... format, from calendar discovery
    call_record_id = Column(String(500), index=True)     # From callRecords webhook

    # Meeting metadata
    subject = Column(String(500))
    organizer_email = Column(String(255), index=True)
    organizer_name = Column(String(500))
    organizer_user_id = Column(String(255))  # User ID (GUID) for getAllTranscripts API
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime)
    duration_minutes = Column(Integer)
    participant_count = Column(Integer)

    # Teams meeting URLs and IDs
    join_url = Column(String(1000))  # Teams meeting join URL
    recording_url = Column(String(1000))  # Recording URL (if available) - DEPRECATED, use recording_sharepoint_url
    recording_sharepoint_url = Column(String(1000))  # SharePoint recording URL (NEW - respects permissions)
    chat_id = Column(String(255))  # Teams chat thread ID
    graph_transcript_id = Column(String(500))  # Graph API transcript ID (from webhook, avoids time-based search)

    # Chat monitoring (NEW)
    last_chat_check = Column(DateTime)  # When we last checked chat for commands

    # Distribution control (NEW - Opt-in/opt-out system)
    distribution_enabled = Column(Boolean, default=True)  # Organizer can disable email distribution
    distribution_disabled_by = Column(String(255))  # Email of person who disabled distribution
    distribution_disabled_at = Column(DateTime)  # When distribution was disabled

    # Discovery metadata (stored in UTC)
    discovered_at = Column(DateTime, default=datetime.utcnow, index=True)
    discovery_source = Column(String(50), default="calendar")  # 'webhook' or 'calendar'
    discovery_run_id = Column(Integer, ForeignKey("processing_runs.id"))

    # Processing status
    status = Column(
        String(50),
        default="discovered",
        index=True,
    )  # 'discovered', 'queued', 'processing', 'completed', 'failed', 'skipped', 'transcript_only'

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

    # Meeting settings (from Graph API onlineMeeting properties)
    allow_transcription = Column(Boolean)  # None=unknown, True=enabled, False=disabled
    allow_recording = Column(Boolean)  # None=unknown, True=enabled, False=disabled

    # Chat event signals (from meeting chat messages)
    # These indicate transcript readiness based on system messages in the chat
    recording_started = Column(Boolean)  # callRecordingEventMessageDetail seen = recording was started
    transcript_available = Column(Boolean)  # callTranscriptEventMessageDetail seen = transcript IS READY

    # Call type (from callRecords API)
    # Values: 'groupCall', 'peerToPeer', 'scheduled', 'adHoc', 'unknown'
    call_type = Column(String(50))

    # ========================================================================
    # ENTERPRISE INTELLIGENCE METADATA (Graph API callRecords expansion)
    # ========================================================================

    # Modality information (from callRecords)
    primary_modality = Column(String(20))  # audio, video, screenSharing
    modalities_used = Column(JSONB)  # ["audio", "video", "screenSharing"]
    is_pstn_call = Column(Boolean, default=False)  # Any participant dialed in via phone

    # Duration & timing (from callRecords)
    actual_duration_seconds = Column(Integer)  # Actual call duration from Graph
    scheduled_duration_seconds = Column(Integer)  # From calendar event
    overtime_seconds = Column(Integer)  # How much it ran over/under

    # Engagement metrics (from callRecords sessions)
    screen_share_duration_pct = Column(DECIMAL(5, 2))  # % of time screen sharing
    video_on_duration_pct = Column(DECIMAL(5, 2))  # % of time with video on
    chat_message_count = Column(Integer)  # In-meeting chat messages

    # Meeting series (from callRecords/calendar)
    is_recurring = Column(Boolean, default=False)  # Part of recurring series
    meeting_series_id = Column(String(255))  # Link to series

    # Participant metadata (from callRecords sessions)
    external_domains = Column(JSONB)  # ["client.com", "vendor.io"]
    device_types = Column(JSONB)  # {"desktop": 3, "mobile": 1, "room": 1}

    # Quality metrics (from callRecords sessions/segments)
    avg_packet_loss_rate = Column(DECIMAL(5, 4))  # Network quality (0.0001 - 1.0)
    avg_jitter_ms = Column(Integer)  # Audio/video jitter
    avg_round_trip_ms = Column(Integer)  # Network latency
    network_quality_score = Column(DECIMAL(3, 2))  # Computed 0-1 score
    connection_types = Column(JSONB)  # {"wired": 3, "wifi": 2, "cellular": 1}
    had_quality_issues = Column(Boolean, default=False)  # Any metrics below threshold

    # Relationships
    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    transcript = relationship("Transcript", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    summaries = relationship("Summary", back_populates="meeting", cascade="all, delete-orphan")  # Changed to plural for versioning
    distributions = relationship("Distribution", back_populates="meeting", cascade="all, delete-orphan")
    jobs = relationship("JobQueue", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered', 'queued', 'processing', 'completed', 'failed', 'skipped', 'transcript_only', 'no_transcript', 'transcription_disabled')",
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
    attended = Column(Boolean, default=True, index=True)  # True=joined call, False=invited but didn't join
    participant_type = Column(String(20))  # 'internal', 'pstn', 'guest', 'external'

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

    # SharePoint links (NEW - for secure sharing)
    transcript_sharepoint_url = Column(String(1000))  # SharePoint URL (respects permissions)
    transcript_expires_at = Column(DateTime)  # Track URL expiration if applicable

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
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)  # Changed: not unique (for versioning)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)

    # Summary content
    summary_text = Column(Text, nullable=False)
    summary_html = Column(Text)  # Formatted version with markdown

    # Enhanced summary data (NEW - structured extractions)
    action_items_json = Column(JSONB)  # List of {description, assignee, deadline, context, timestamp}
    decisions_json = Column(JSONB)  # List of {decision, participants, reasoning, impact, timestamp}
    topics_json = Column(JSONB)  # List of {topic, duration, speakers, summary, key_points}
    highlights_json = Column(JSONB)  # List of {title, timestamp, why_important, type}
    mentions_json = Column(JSONB)  # List of {person, mentioned_by, context, timestamp, type}
    key_numbers_json = Column(JSONB)  # List of {value, unit, context, magnitude} - financial/quantitative metrics

    # Versioning (NEW - for re-summarization)
    version = Column(Integer, default=1, nullable=False, index=True)
    custom_instructions = Column(Text)  # User-provided instructions for custom summaries
    superseded_by = Column(Integer, ForeignKey("summaries.id"))  # Points to newer version

    # AI metadata
    model = Column(String(100), default="claude-sonnet-4-20250514")
    approach = Column(String(50))  # gemini_single_call, haiku_fallback, or legacy (pre-Gemini)
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)

    # Processing details
    generated_at = Column(DateTime, default=func.now(), index=True)
    generation_time_ms = Column(Integer)

    # Quality metadata
    confidence_score = Column(DECIMAL(3, 2))  # 0.00-1.00

    # ========================================================================
    # ENTERPRISE INTELLIGENCE METADATA (AI-extracted classification)
    # ========================================================================

    # Meeting Classification (8 fields)
    meeting_type = Column(String(50))  # sales_call, internal_sync, onboarding, coaching, planning, etc.
    meeting_category = Column(String(50))  # internal, external_client, external_vendor, mixed
    seniority_level = Column(String(50))  # c_suite, executive, management, individual_contributor, mixed
    department_context = Column(String(100))  # inferred department(s) involved
    is_onboarding = Column(Boolean, default=False)  # New hire/customer onboarding
    is_coaching = Column(Boolean, default=False)  # 1:1 coaching/mentoring
    is_sales_meeting = Column(Boolean, default=False)  # Sales/revenue focused
    is_support_call = Column(Boolean, default=False)  # Customer support issue

    # Sentiment & Tone (7 fields)
    overall_sentiment = Column(String(20))  # positive, neutral, negative, mixed
    urgency_level = Column(String(20))  # critical, high, medium, low
    consensus_level = Column(String(20))  # unanimous, strong_agreement, split, contentious
    has_concerns = Column(Boolean, default=False)  # Quick filter for meetings with issues
    meeting_effectiveness = Column(String(20))  # highly_productive, productive, neutral, unproductive
    communication_style = Column(String(20))  # formal, professional, casual, mixed
    energy_level = Column(String(20))  # high, medium, low (engagement/enthusiasm)

    # Counts & Metrics (5 fields)
    action_item_count = Column(Integer)  # Number of action items generated
    decision_count = Column(Integer)  # Number of decisions made
    open_question_count = Column(Integer)  # Unresolved questions
    blocker_count = Column(Integer)  # Blockers/risks identified
    follow_up_required = Column(Boolean, default=False)  # Needs explicit follow-up

    # Content & Topics (7 JSONB fields)
    topics_discussed = Column(JSONB)  # ["budget", "hiring", "Q4 planning"]
    projects_mentioned = Column(JSONB)  # ["Project Phoenix", "Website Redesign"]
    products_mentioned = Column(JSONB)  # ["Salesforce", "our CRM", "new feature"]
    technologies_discussed = Column(JSONB)  # ["Python", "AWS", "Teams"]
    people_mentioned = Column(JSONB)  # People referenced but not in meeting
    deadlines_mentioned = Column(JSONB)  # [{"date": "2024-01-15", "context": "launch"}]
    financial_mentions = Column(JSONB)  # [{"amount": 50000, "context": "budget"}]

    # Enhanced Structured Data (4 JSONB fields)
    concerns_json = Column(JSONB)  # Detailed concern/complaint tracking
    blockers_json = Column(JSONB)  # Detailed blocker/risk tracking
    market_intelligence_json = Column(JSONB)  # Competitor mentions, market insights
    training_content_json = Column(JSONB)  # Knowledge transfer/training detected

    # External Detection (4 fields)
    has_external_participants = Column(Boolean)  # Any non-internal participants?
    external_company_names = Column(JSONB)  # ["Acme Corp", "Client Inc"]
    client_names = Column(JSONB)  # Identified client organizations
    competitor_names = Column(JSONB)  # Competitors mentioned by name

    # Flags for Quick Filtering (6 fields)
    has_financial_discussion = Column(Boolean, default=False)  # Money/budget discussed
    has_deadline_pressure = Column(Boolean, default=False)  # Tight deadlines mentioned
    has_escalation = Column(Boolean, default=False)  # Issue escalated/needs attention
    has_customer_complaint = Column(Boolean, default=False)  # Customer expressed issue
    has_technical_discussion = Column(Boolean, default=False)  # Technical/engineering content
    is_confidential = Column(Boolean, default=False)  # Sensitive/NDA content detected

    # Relationships
    meeting = relationship("Meeting", back_populates="summaries")  # Changed to plural for multiple versions
    transcript = relationship("Transcript", back_populates="summary")
    distributions = relationship("Distribution", back_populates="summary", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_summaries_meeting_version", "meeting_id", "version"),
        Index("idx_summaries_meeting_type", "meeting_type"),
        Index("idx_summaries_meeting_category", "meeting_category"),
        Index("idx_summaries_has_concerns", "has_concerns"),
        Index("idx_summaries_has_external", "has_external_participants"),
        Index("idx_summaries_overall_sentiment", "overall_sentiment"),
        Index("idx_summaries_is_sales", "is_sales_meeting"),
        Index("idx_summaries_has_escalation", "has_escalation"),
        Index("idx_summaries_follow_up", "follow_up_required"),
    )


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


class UserPreference(Base):
    """
    User email preferences for meeting summaries.

    Allows users to manage whether they receive meeting summary emails,
    and tracks preference changes via chat commands or organizer settings.

    Uses user_id (Azure AD GUID) as primary key for stable identity matching.
    The GUID never changes even if user's email changes.
    """
    __tablename__ = "user_preferences"

    user_id = Column(String(50), primary_key=True)  # Azure AD GUID (stable identity)
    user_email = Column(String(255), nullable=False, index=True)  # Display/reference email
    display_name = Column(String(500))  # Cached display name
    receive_emails = Column(Boolean, default=True, nullable=False)
    email_preference = Column(String(20), default='all')  # 'all', 'opt_in', 'disabled'
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    updated_by = Column(String(50))  # 'user' or 'organizer'

    def __repr__(self):
        return f"<UserPreference(user_id='{self.user_id}', email='{self.user_email}', receive={self.receive_emails})>"


class MeetingPreference(Base):
    """
    Per-meeting email preferences for users.

    Allows users to opt in/out of specific meetings while maintaining their
    global preference. Per-meeting preferences override global preferences.

    Uses user_id (Azure AD GUID) for stable identity matching.
    """
    __tablename__ = "meeting_preferences"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(50), nullable=False, index=True)  # Azure AD GUID
    user_email = Column(String(255), nullable=False, index=True)  # Display/reference email
    receive_emails = Column(Boolean, nullable=False)
    updated_by = Column(String(50), default="user")  # 'user', 'organizer', or 'system'
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('meeting_id', 'user_id', name='uq_meeting_user_pref'),
        Index('idx_meeting_prefs_meeting', 'meeting_id'),
        Index('idx_meeting_prefs_user_id', 'user_id'),
        Index('idx_meeting_prefs_email', 'user_email'),
        Index('idx_meeting_prefs_lookup', 'meeting_id', 'user_id'),
    )

    def __repr__(self):
        return f"<MeetingPreference(meeting_id={self.meeting_id}, user_id='{self.user_id}', receive={self.receive_emails})>"


class ProcessedChatMessage(Base):
    """
    Tracking for processed chat commands.

    Prevents duplicate processing of chat commands by storing message IDs
    that have already been handled.
    """
    __tablename__ = "processed_chat_messages"

    message_id = Column(String(255), primary_key=True)
    chat_id = Column(String(255), nullable=False, index=True)
    command_type = Column(String(50))  # 'email_me', 'email_all', 'no_emails', 'summarize_again'
    processed_at = Column(DateTime, default=func.now(), index=True)
    result = Column(Text)  # Success/error message

    __table_args__ = (
        Index("idx_processed_messages_chat", "chat_id", "processed_at"),
    )

    def __repr__(self):
        return f"<ProcessedChatMessage(id='{self.message_id}', type='{self.command_type}')>"


class ProcessedCallRecord(Base):
    """
    Tracking for processed Microsoft Graph callRecords.

    Prevents duplicate processing of callRecords from webhooks, backfills,
    and safety net syncs.
    """
    __tablename__ = "processed_call_records"

    call_record_id = Column(String(255), primary_key=True)
    processed_at = Column(DateTime, default=func.now(), index=True)
    source = Column(String(20))  # 'webhook', 'backfill', 'safety_net'

    def __repr__(self):
        return f"<ProcessedCallRecord(id='{self.call_record_id}', source='{self.source}')>"


class BackfillRun(Base):
    """
    Track backfill operations for monitoring and debugging.

    Records each backfill execution with statistics for:
    - Troubleshooting backfill issues
    - Monitoring system health
    - Analytics and reporting
    """
    __tablename__ = "backfill_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=func.now(), index=True)
    completed_at = Column(DateTime)
    status = Column(String(50), default="running", index=True)

    # Configuration
    lookback_hours = Column(Integer)
    cutoff_time = Column(DateTime)  # Actual cutoff used
    source = Column(String(20))  # 'manual', 'automatic', 'force'
    triggered_by = Column(String(255))  # User email or 'system'

    # Statistics
    call_records_found = Column(Integer, default=0)
    meetings_created = Column(Integer, default=0)
    transcripts_found = Column(Integer, default=0)
    transcripts_pending = Column(Integer, default=0)
    skipped_no_optin = Column(Integer, default=0)
    jobs_created = Column(Integer, default=0)
    errors = Column(Integer, default=0)

    # Error tracking
    error_message = Column(Text)
    error_stack = Column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="valid_backfill_status"
        ),
        Index("idx_backfill_runs_status_time", "status", "started_at"),
    )

    def __repr__(self):
        return f"<BackfillRun(id={self.id}, status='{self.status}', started={self.started_at})>"


class SubscriptionEvent(Base):
    """
    Track webhook subscription lifecycle events for monitoring and statistics.

    Events are logged when subscriptions go down, recover, or encounter errors.
    Used to calculate uptime, average downtime, and display on diagnostics page.
    """
    __tablename__ = "subscription_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(20), nullable=False, index=True)  # 'down', 'up', 'created', 'renewed', 'failed'
    timestamp = Column(DateTime, default=func.now(), nullable=False, index=True)

    # Subscription details (if applicable)
    subscription_id = Column(String(100))

    # For 'down' events - track what caused it
    error_message = Column(Text)

    # For 'up' events - link to the 'down' event and calculate downtime
    down_event_id = Column(Integer, ForeignKey("subscription_events.id"))
    downtime_seconds = Column(Integer)  # Calculated when going back up

    # Source of the event
    source = Column(String(50))  # 'startup', 'check', 'daily_refresh', 'manual'

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('down', 'up', 'created', 'renewed', 'failed')",
            name="valid_subscription_event_type"
        ),
        Index("idx_subscription_events_type_time", "event_type", "timestamp"),
    )

    def __repr__(self):
        return f"<SubscriptionEvent(id={self.id}, type='{self.event_type}', time={self.timestamp})>"


# ============================================================================
# INBOX MONITORING MODELS (Phase 5)
# ============================================================================


class UserFeedback(Base):
    """
    Store feedback received via email from users.

    Feedback can be replies to summary emails or direct emails to the
    note.taker mailbox. AI analysis provides sentiment and categorization.
    """
    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="SET NULL"), index=True)
    user_email = Column(String(255), nullable=False, index=True)
    feedback_text = Column(Text, nullable=False)
    subject = Column(String(500))
    source_email_id = Column(String(500))  # Graph API message ID
    in_reply_to = Column(String(500))  # Message ID this is replying to
    received_at = Column(DateTime, default=func.now(), index=True)

    # AI analysis (populated by feedback processor)
    ai_sentiment = Column(String(20))  # positive, negative, neutral, suggestion
    ai_category = Column(String(50))   # bug, feature_request, praise, question, other
    ai_summary = Column(Text)  # Brief AI summary of feedback

    # Relationship
    meeting = relationship("Meeting", backref="feedback")

    __table_args__ = (
        Index("idx_feedback_user_date", "user_email", "received_at"),
    )

    def __repr__(self):
        return f"<UserFeedback(id={self.id}, user='{self.user_email}', sentiment='{self.ai_sentiment}')>"


class ProcessedInboxMessage(Base):
    """
    Track processed emails from the note.taker inbox.

    Prevents duplicate processing of subscribe/unsubscribe/feedback emails.
    Similar to ProcessedChatMessage but for inbox monitoring.
    """
    __tablename__ = "processed_inbox_messages"

    message_id = Column(String(500), primary_key=True)
    message_type = Column(String(50), index=True)  # subscribe, unsubscribe, feedback, summary_request, unknown
    processed_at = Column(DateTime, default=func.now(), index=True)
    user_email = Column(String(255), index=True)
    result = Column(Text)  # Success/failure details, JSON or plain text

    __table_args__ = (
        Index("idx_inbox_user_date", "user_email", "processed_at"),
    )

    def __repr__(self):
        return f"<ProcessedInboxMessage(id='{self.message_id[:20]}...', type='{self.message_type}')>"


class EmailAlias(Base):
    """
    Maps email aliases to primary email addresses and Azure AD user IDs.

    Users may send from aliases (e.g., scott.s@company.com) but their
    primary email (sschatz@company.com) is what appears in meeting participant
    lists. This table caches the mapping to avoid repeated Graph API calls.

    The user_id (Azure AD object ID) is stable and never changes, even if
    the user's email changes (e.g., after marriage or department change).
    """
    __tablename__ = "email_aliases"

    alias_email = Column(String(255), primary_key=True)  # The alias email (lowercase)
    primary_email = Column(String(255), nullable=False, index=True)  # User's primary email
    user_id = Column(String(50), index=True)  # Azure AD object ID (stable GUID)
    display_name = Column(String(500))  # User's display name
    job_title = Column(String(255))  # User's job title from Azure AD
    resolved_at = Column(DateTime, default=func.now())
    last_used_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<EmailAlias(alias='{self.alias_email}', primary='{self.primary_email}', user_id='{self.user_id}')>"


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


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

_db_manager_instance: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """
    Get or create a shared DatabaseManager instance.

    Uses the connection string from the application config.
    Thread-safe due to SQLAlchemy's built-in connection pooling.

    Returns:
        DatabaseManager instance
    """
    global _db_manager_instance

    if _db_manager_instance is None:
        from .config import get_config
        config = get_config()
        _db_manager_instance = DatabaseManager(config.database.connection_string)

    return _db_manager_instance
