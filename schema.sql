-- =====================================================================
-- Local Info Pulse Map — Supabase DDL (Tier 1)
-- Target: PostgreSQL 15+ / Supabase with PostGIS
-- Run in Supabase SQL Editor, or `psql -f` against the project DB.
-- =====================================================================

-- 1. Enable extensions
-- NOTE: pg_cron is pre-installed on Supabase but not on stock Postgres images.
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Events table
CREATE TABLE public.events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(100) NOT NULL,
    summary TEXT,
    category VARCHAR(20) NOT NULL CHECK (category IN ('promotion', 'market', 'exhibition', 'warning')),
    geom GEOMETRY(Point, 4326) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL CHECK (end_time > start_time),
    is_time_estimated BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT TRUE,
    crowd_status VARCHAR(12) DEFAULT 'comfortable'
        CHECK (crowd_status IN ('comfortable', 'moderate', 'crowded')),
    crowd_updated_at TIMESTAMPTZ,
    allow_user_survey BOOLEAN DEFAULT TRUE,
    is_expired_early BOOLEAN DEFAULT FALSE,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'likely_canceled', 'under_review')),
    source_url TEXT,
    confidence_weight NUMERIC DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Indexes
CREATE INDEX idx_events_geom ON public.events USING GIST (geom);
CREATE INDEX idx_events_time ON public.events (start_time, end_time);
CREATE INDEX idx_events_category ON public.events (category);

-- 4. Anonymous device & recovery
CREATE TABLE public.anonymous_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_uuid UUID UNIQUE NOT NULL,
    recovery_code_hash TEXT NOT NULL,
    failed_attempts INT DEFAULT 0,
    locked_until TIMESTAMPTZ,
    ad_free_until TIMESTAMPTZ DEFAULT NOW(),
    karma_score NUMERIC DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Crowd survey logs
CREATE TABLE public.survey_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES public.events(id) ON DELETE CASCADE,
    device_id UUID REFERENCES public.anonymous_devices(id) ON DELETE CASCADE,
    reported_status VARCHAR(10) NOT NULL
        CHECK (reported_status IN ('comfortable', 'moderate', 'crowded', 'ended_early')),
    reported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_survey_logs_event ON public.survey_logs (event_id, reported_at DESC);

-- 6. RLS
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.anonymous_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.survey_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public Read Active Events" ON public.events
    FOR SELECT TO anon, authenticated
    USING (
        is_expired_early = FALSE
        AND end_time >= NOW()
        AND status IN ('active')   -- hides under_review / likely_canceled pins
    );

REVOKE ALL ON TABLE public.anonymous_devices FROM anon;
REVOKE ALL ON TABLE public.survey_logs FROM anon;

-- 7. Bounded spatial RPC (SECURITY DEFINER must pin search_path)
CREATE OR REPLACE FUNCTION public.get_events_in_bbox(
    min_lng DOUBLE PRECISION,
    min_lat DOUBLE PRECISION,
    max_lng DOUBLE PRECISION,
    max_lat DOUBLE PRECISION
)
RETURNS TABLE (
    id UUID,
    title VARCHAR,
    summary TEXT,
    category VARCHAR,
    lng DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    is_time_estimated BOOLEAN,
    is_verified BOOLEAN,
    crowd_status VARCHAR,
    allow_user_survey BOOLEAN,
    source_url TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    SET LOCAL statement_timeout = '500ms';
    RETURN QUERY
    SELECT
        e.id, e.title, e.summary, e.category,
        ST_X(e.geom) AS lng, ST_Y(e.geom) AS lat,
        e.start_time, e.end_time,
        e.is_time_estimated, e.is_verified,
        e.crowd_status, e.allow_user_survey,
        e.source_url
    FROM public.events e
    WHERE e.geom && ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)
      AND e.is_expired_early = FALSE
      AND e.end_time >= NOW()
      AND e.status = 'active'
    ORDER BY (e.is_verified = TRUE) DESC, e.start_time ASC
    LIMIT 50;
END;
$$;

REVOKE ALL ON FUNCTION public.get_events_in_bbox(DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION) FROM anon;
GRANT EXECUTE ON FUNCTION public.get_events_in_bbox(DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION) TO anon, authenticated;

-- 8. updated_at auto-touch trigger
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_events_touch_updated_at
    BEFORE UPDATE ON public.events
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- 9. pg_cron maintenance jobs (free-tier quota hygiene)
-- Runs only where pg_cron exists (Supabase). On stock Postgres these are skipped.
DO $do_block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron') THEN
        CREATE EXTENSION IF NOT EXISTS pg_cron;
        PERFORM cron.schedule(
            'purge-expired-events',
            '17 3 * * *',
            $cmd$DELETE FROM public.events WHERE end_time < NOW() - INTERVAL '7 days'$cmd$
        );
        PERFORM cron.schedule(
            'purge-stale-survey-logs',
            '42 3 * * 0',
            $cmd$DELETE FROM public.survey_logs WHERE reported_at < NOW() - INTERVAL '30 days'$cmd$
        );
        RAISE NOTICE 'pg_cron jobs scheduled';
    ELSE
        RAISE NOTICE 'pg_cron not available — skipping scheduled purge jobs (Supabase will run them)';
    END IF;
END
$do_block$;
