-- Migration: Migrate UserPreference to use user_id (GUID) as primary key
-- Purpose: Enable stable identity matching using Azure AD GUIDs instead of email addresses
-- Date: 2025-12-19
--
-- IMPORTANT: Run "Refresh All from Azure AD" in admin UI before this migration
-- to populate EmailAlias records with user_id mappings.

BEGIN;

-- Step 1: Add new columns to user_preferences
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS user_id VARCHAR(50);
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS display_name VARCHAR(500);

-- Step 2: Populate user_id from email_aliases table (where mapping exists)
UPDATE user_preferences up
SET user_id = ea.user_id,
    display_name = ea.display_name
FROM email_aliases ea
WHERE LOWER(up.user_email) = LOWER(ea.alias_email)
  AND ea.user_id IS NOT NULL
  AND up.user_id IS NULL;

-- Step 3: Log unmapped records (for manual review)
DO $$
DECLARE
    unmapped_count INTEGER;
    unmapped_emails TEXT;
BEGIN
    SELECT COUNT(*), string_agg(user_email, ', ')
    INTO unmapped_count, unmapped_emails
    FROM user_preferences
    WHERE user_id IS NULL;

    IF unmapped_count > 0 THEN
        RAISE NOTICE 'Warning: % user_preferences records have no user_id mapping', unmapped_count;
        RAISE NOTICE 'Unmapped emails: %', unmapped_emails;
        RAISE NOTICE 'Run "Refresh All from Azure AD" in admin UI to populate missing GUIDs';
    END IF;
END $$;

-- Step 4: For unmapped records, create temporary GUID placeholders
-- (These will be replaced when user next subscribes or admin refreshes)
UPDATE user_preferences
SET user_id = 'pending-' || LOWER(REPLACE(user_email, '@', '-at-'))
WHERE user_id IS NULL;

-- Step 5: Make user_id NOT NULL and change PRIMARY KEY
ALTER TABLE user_preferences ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS user_preferences_pkey;
ALTER TABLE user_preferences ADD PRIMARY KEY (user_id);

-- Step 6: Add index on user_email for fallback lookups
CREATE INDEX IF NOT EXISTS idx_user_preferences_email ON user_preferences(LOWER(user_email));

-- Step 7: Update MeetingPreference table
-- Add user_id column
ALTER TABLE meeting_preferences ADD COLUMN IF NOT EXISTS user_id VARCHAR(50);

-- Populate user_id from email_aliases
UPDATE meeting_preferences mp
SET user_id = ea.user_id
FROM email_aliases ea
WHERE LOWER(mp.user_email) = LOWER(ea.alias_email)
  AND ea.user_id IS NOT NULL
  AND mp.user_id IS NULL;

-- For unmapped records, create temporary GUID placeholders
UPDATE meeting_preferences
SET user_id = 'pending-' || LOWER(REPLACE(user_email, '@', '-at-'))
WHERE user_id IS NULL;

-- Make user_id NOT NULL
ALTER TABLE meeting_preferences ALTER COLUMN user_id SET NOT NULL;

-- Drop old unique constraint (meeting_id + user_email) - may fail if doesn't exist
ALTER TABLE meeting_preferences DROP CONSTRAINT IF EXISTS uq_meeting_user_pref;

-- Add new unique constraint (meeting_id + user_id)
ALTER TABLE meeting_preferences ADD CONSTRAINT uq_meeting_user_pref
    UNIQUE (meeting_id, user_id);

-- Update indexes
DROP INDEX IF EXISTS idx_meeting_prefs_lookup;
CREATE INDEX IF NOT EXISTS idx_meeting_prefs_user_id ON meeting_preferences(user_id);
CREATE INDEX IF NOT EXISTS idx_meeting_prefs_lookup ON meeting_preferences(meeting_id, user_id);

-- Add comments
COMMENT ON COLUMN user_preferences.user_id IS 'Azure AD user ID (GUID). Records with "pending-" prefix need GUID resolution via Graph API.';
COMMENT ON COLUMN user_preferences.display_name IS 'Cached display name from Azure AD';
COMMENT ON COLUMN meeting_preferences.user_id IS 'Azure AD user ID (GUID). Records with "pending-" prefix need GUID resolution via Graph API.';

COMMIT;

-- Verification queries (run after migration)
-- 1. Check for pending GUIDs in user_preferences
-- SELECT user_email, user_id FROM user_preferences WHERE user_id LIKE 'pending-%';

-- 2. Check for pending GUIDs in meeting_preferences
-- SELECT user_email, user_id FROM meeting_preferences WHERE user_id LIKE 'pending-%';

-- 3. Verify primary key change
-- \d user_preferences
