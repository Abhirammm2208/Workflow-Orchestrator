-- Run once on first DB init inside Docker
-- Enables UUID generation and JSONB indexing helpers

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- fuzzy text search on ticket content
