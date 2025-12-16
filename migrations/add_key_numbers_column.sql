-- Migration: Add key_numbers_json column to summaries table
-- Date: 2025-12-16
-- Description: Adds a new JSONB column to store extracted financial and quantitative metrics from meetings

-- Add key_numbers_json column to summaries table
ALTER TABLE summaries
ADD COLUMN IF NOT EXISTS key_numbers_json JSONB;

-- Add comment explaining the column
COMMENT ON COLUMN summaries.key_numbers_json IS
'Extracted financial and numeric metrics from meeting transcript. Format: [{value, unit, context, magnitude}]. Used for Key Numbers section in email summaries.';

-- Optional: Create a GIN index for better query performance on JSONB data
-- CREATE INDEX IF NOT EXISTS idx_summaries_key_numbers ON summaries USING gin (key_numbers_json);

-- Verify the column was added
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'summaries' AND column_name = 'key_numbers_json';
