-- Fix transaction dates that are stored as full datetimes instead of date-only
-- This converts '2025-08-25T20:34:04Z' to '2025-08-25'

UPDATE transactions 
SET transaction_date = DATE(transaction_date::timestamp)
WHERE transaction_date ~ 'T.*[Z]?$';

-- Verify the fix
SELECT id, transaction_date, created_at 
FROM transactions 
ORDER BY id DESC 
LIMIT 5;
