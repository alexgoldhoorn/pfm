-- PostgreSQL initialization script for Portfolio Manager
-- This script runs automatically when the PostgreSQL container starts for the first time

-- Create extensions if needed
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Set timezone to UTC
SET timezone = 'UTC';

-- Create additional schemas if needed (optional)
-- CREATE SCHEMA IF NOT EXISTS portfolio;

-- Grant permissions (these are already handled by Docker environment variables)
-- GRANT ALL PRIVILEGES ON DATABASE portf_db TO portf_user;

-- You can add any additional initialization SQL here
-- For example, creating custom functions, triggers, or seed data

-- Log successful initialization
DO $$
BEGIN
    RAISE NOTICE 'Portfolio Manager PostgreSQL database initialized successfully';
END $$;
