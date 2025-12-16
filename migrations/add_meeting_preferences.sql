-- Migration: Add opt-in/opt-out preference system
-- Date: 2025-12-16
-- Description: Adds meeting-level distribution controls and per-meeting user preferences

-- Add meeting-level distribution controls to meetings table
ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS distribution_enabled BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS distribution_disabled_by VARCHAR(255),
    ADD COLUMN IF NOT EXISTS distribution_disabled_at TIMESTAMP;

COMMENT ON COLUMN meetings.distribution_enabled IS
    'Organizer can disable email distribution for entire meeting';
COMMENT ON COLUMN meetings.distribution_disabled_by IS
    'Email of person who disabled distribution';
COMMENT ON COLUMN meetings.distribution_disabled_at IS
    'When distribution was disabled';

-- Create meeting_preferences table for per-meeting user preferences
CREATE TABLE IF NOT EXISTS meeting_preferences (
    id SERIAL PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    user_email VARCHAR(255) NOT NULL,
    receive_emails BOOLEAN NOT NULL,
    updated_by VARCHAR(50) DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_meeting_user_pref UNIQUE (meeting_id, user_email)
);

CREATE INDEX idx_meeting_prefs_meeting ON meeting_preferences(meeting_id);
CREATE INDEX idx_meeting_prefs_email ON meeting_preferences(user_email);
CREATE INDEX idx_meeting_prefs_lookup ON meeting_preferences(meeting_id, user_email);

COMMENT ON TABLE meeting_preferences IS
    'Per-meeting email preferences (overrides global preferences)';
COMMENT ON COLUMN meeting_preferences.receive_emails IS
    'Whether user wants emails for this specific meeting';
COMMENT ON COLUMN meeting_preferences.updated_by IS
    'Who updated: user, organizer, or system';

-- Add helpful trigger for updated_at
CREATE OR REPLACE FUNCTION update_meeting_preferences_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER meeting_preferences_updated_at
    BEFORE UPDATE ON meeting_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_meeting_preferences_updated_at();
