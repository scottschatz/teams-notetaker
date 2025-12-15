-- Database Migration: Enhanced Features
-- Version: 2.0.0
-- Date: 2025-12-15
-- Description: Add SharePoint links, enhanced summaries, chat commands, and user preferences
--
-- This migration adds support for:
-- - SharePoint URLs for transcripts and recordings (permission-respecting links)
-- - Enhanced AI summaries with structured data (action items, decisions, topics, highlights, mentions)
-- - Summary versioning for re-summarization
-- - Chat command tracking (processed messages)
-- - User preference management (email opt-in/opt-out)
-- - Chat monitoring (last_chat_check timestamp)

-- ====================
-- 1. Meetings Table Updates
-- ====================

-- Add chat monitoring timestamp
ALTER TABLE meetings
ADD COLUMN IF NOT EXISTS last_chat_check TIMESTAMP DEFAULT NULL;

-- Add SharePoint URL for recordings (separate from join_url)
ALTER TABLE meetings
ADD COLUMN IF NOT EXISTS recording_sharepoint_url VARCHAR(1000) DEFAULT NULL;

COMMENT ON COLUMN meetings.last_chat_check IS 'Last time we checked this meeting''s chat for commands';
COMMENT ON COLUMN meetings.recording_sharepoint_url IS 'SharePoint URL for meeting recording (respects Teams permissions)';

-- ====================
-- 2. Transcripts Table Updates
-- ====================

-- Add SharePoint links for transcripts
ALTER TABLE transcripts
ADD COLUMN IF NOT EXISTS transcript_sharepoint_url VARCHAR(1000) DEFAULT NULL;

ALTER TABLE transcripts
ADD COLUMN IF NOT EXISTS transcript_expires_at TIMESTAMP DEFAULT NULL;

COMMENT ON COLUMN transcripts.transcript_sharepoint_url IS 'SharePoint URL for transcript (respects Teams permissions)';
COMMENT ON COLUMN transcripts.transcript_expires_at IS 'When SharePoint URL expires (if applicable)';

-- ====================
-- 3. Summaries Table Updates (Enhanced AI)
-- ====================

-- Add structured data columns (JSONB for performance)
ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS action_items_json JSONB DEFAULT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS decisions_json JSONB DEFAULT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS topics_json JSONB DEFAULT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS highlights_json JSONB DEFAULT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS mentions_json JSONB DEFAULT NULL;

COMMENT ON COLUMN summaries.action_items_json IS 'Structured action items: [{description, assignee, deadline, context, timestamp}]';
COMMENT ON COLUMN summaries.decisions_json IS 'Key decisions: [{decision, participants, reasoning, impact, timestamp}]';
COMMENT ON COLUMN summaries.topics_json IS 'Meeting topics: [{topic, duration, speakers, summary, key_points}]';
COMMENT ON COLUMN summaries.highlights_json IS 'Key moments: [{title, timestamp, why_important, type}]';
COMMENT ON COLUMN summaries.mentions_json IS '@mentions: [{person, mentioned_by, context, timestamp, type}]';

-- Add versioning support
ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1 NOT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS custom_instructions TEXT DEFAULT NULL;

ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS superseded_by INTEGER DEFAULT NULL;

ALTER TABLE summaries
ADD CONSTRAINT fk_summaries_superseded_by
FOREIGN KEY (superseded_by) REFERENCES summaries(id) ON DELETE SET NULL;

COMMENT ON COLUMN summaries.version IS 'Summary version (1 for initial, 2+ for re-summarization)';
COMMENT ON COLUMN summaries.custom_instructions IS 'User-provided instructions for re-summarization';
COMMENT ON COLUMN summaries.superseded_by IS 'ID of newer summary version (if re-summarized)';

-- Drop unique constraint on meeting_id to allow multiple versions
-- Note: Only drop if exists (PostgreSQL 9.6+ syntax)
DO $$
BEGIN
    ALTER TABLE summaries DROP CONSTRAINT IF EXISTS summaries_meeting_id_key;
EXCEPTION
    WHEN undefined_object THEN
        NULL; -- Constraint doesn't exist, that's fine
END $$;

-- Create composite unique index for meeting_id + version
CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_meeting_version
ON summaries(meeting_id, version);

COMMENT ON INDEX idx_summaries_meeting_version IS 'Ensure one summary per version per meeting';

-- ====================
-- 4. User Preferences Table (NEW)
-- ====================

CREATE TABLE IF NOT EXISTS user_preferences (
    user_email VARCHAR(255) PRIMARY KEY,
    receive_emails BOOLEAN DEFAULT true NOT NULL,
    email_preference VARCHAR(20) DEFAULT 'all' NOT NULL CHECK (email_preference IN ('all', 'opt_in', 'disabled')),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_by VARCHAR(50) DEFAULT 'user',
    CONSTRAINT chk_email_preference CHECK (email_preference IN ('all', 'opt_in', 'disabled'))
);

COMMENT ON TABLE user_preferences IS 'User email preferences for meeting summaries';
COMMENT ON COLUMN user_preferences.user_email IS 'User email address (primary key)';
COMMENT ON COLUMN user_preferences.receive_emails IS 'Whether user wants email summaries (true = opt-in, false = opt-out)';
COMMENT ON COLUMN user_preferences.email_preference IS 'Preference type: all (default), opt_in, or disabled';
COMMENT ON COLUMN user_preferences.updated_at IS 'When preference was last updated';
COMMENT ON COLUMN user_preferences.updated_by IS 'Who updated preference: user, organizer, admin';

-- Create index on user_email (already primary key, but explicit for clarity)
-- CREATE INDEX IF NOT EXISTS idx_user_preferences_email ON user_preferences(user_email);

-- ====================
-- 5. Processed Chat Messages Table (NEW)
-- ====================

CREATE TABLE IF NOT EXISTS processed_chat_messages (
    message_id VARCHAR(255) PRIMARY KEY,
    chat_id VARCHAR(255) NOT NULL,
    command_type VARCHAR(50) DEFAULT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    result TEXT DEFAULT NULL
);

COMMENT ON TABLE processed_chat_messages IS 'Tracks processed chat messages to avoid duplicate command execution';
COMMENT ON COLUMN processed_chat_messages.message_id IS 'Teams message ID (primary key)';
COMMENT ON COLUMN processed_chat_messages.chat_id IS 'Teams chat thread ID';
COMMENT ON COLUMN processed_chat_messages.command_type IS 'Command type or reason for processing';
COMMENT ON COLUMN processed_chat_messages.processed_at IS 'When message was processed';
COMMENT ON COLUMN processed_chat_messages.result IS 'Result or error message';

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_processed_messages_chat
ON processed_chat_messages(chat_id, processed_at);

COMMENT ON INDEX idx_processed_messages_chat IS 'Efficiently query processed messages by chat and time';

-- ====================
-- 6. Additional Indexes for Performance
-- ====================

-- Index on meeting chat_id for chat monitoring
CREATE INDEX IF NOT EXISTS idx_meetings_chat_id
ON meetings(chat_id)
WHERE chat_id IS NOT NULL AND chat_id != '';

COMMENT ON INDEX idx_meetings_chat_id IS 'Efficiently find meetings with chat threads';

-- Index on meeting start_time for chat monitoring lookback
CREATE INDEX IF NOT EXISTS idx_meetings_start_time
ON meetings(start_time);

COMMENT ON INDEX idx_meetings_start_time IS 'Efficiently query recent meetings';

-- Index on summaries meeting_id for latest version lookup
CREATE INDEX IF NOT EXISTS idx_summaries_meeting_latest
ON summaries(meeting_id, version DESC);

COMMENT ON INDEX idx_summaries_meeting_latest IS 'Efficiently find latest summary version';

-- Index on summaries superseded_by for version chain lookup
CREATE INDEX IF NOT EXISTS idx_summaries_superseded_by
ON summaries(superseded_by)
WHERE superseded_by IS NOT NULL;

COMMENT ON INDEX idx_summaries_superseded_by IS 'Efficiently find summary version chains';

-- ====================
-- 7. Update Existing Data (Optional)
-- ====================

-- Set default version=1 for existing summaries (if not already set)
UPDATE summaries SET version = 1 WHERE version IS NULL;

-- ====================
-- 8. Verify Migration
-- ====================

-- Verify new columns exist
DO $$
DECLARE
    col_count INTEGER;
BEGIN
    -- Check meetings columns
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_name = 'meetings'
    AND column_name IN ('last_chat_check', 'recording_sharepoint_url');

    IF col_count < 2 THEN
        RAISE EXCEPTION 'Migration failed: meetings table columns missing';
    END IF;

    -- Check transcripts columns
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_name = 'transcripts'
    AND column_name IN ('transcript_sharepoint_url', 'transcript_expires_at');

    IF col_count < 2 THEN
        RAISE EXCEPTION 'Migration failed: transcripts table columns missing';
    END IF;

    -- Check summaries columns
    SELECT COUNT(*) INTO col_count
    FROM information_schema.columns
    WHERE table_name = 'summaries'
    AND column_name IN ('action_items_json', 'decisions_json', 'topics_json', 'highlights_json', 'mentions_json', 'version', 'custom_instructions', 'superseded_by');

    IF col_count < 8 THEN
        RAISE EXCEPTION 'Migration failed: summaries table columns missing';
    END IF;

    -- Check new tables exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_preferences') THEN
        RAISE EXCEPTION 'Migration failed: user_preferences table missing';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_chat_messages') THEN
        RAISE EXCEPTION 'Migration failed: processed_chat_messages table missing';
    END IF;

    RAISE NOTICE 'Migration completed successfully!';
END $$;

-- ====================
-- Migration Complete!
-- ====================

-- To apply this migration:
-- psql -h localhost -U postgres -d teams_notetaker -f migrations/add_enhanced_features.sql

-- To verify:
-- \d meetings
-- \d transcripts
-- \d summaries
-- \d user_preferences
-- \d processed_chat_messages
