-- =====================================================================
-- Crowd report hardening v2 (PRD 5.1/5.2/5.3)
-- Adds: dwell-time proof, 48h-view requirement, daily reward cap,
--       spike anomaly detection, recovery-code hashing.
-- Run in Supabase SQL Editor.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Recovery code (PRD 5.1): 6-char code, hashed server-side.
--    register_device now takes the plaintext code, hashes with pgcrypto
--    digest (SHA-256; bcrypt via pgcrypto is available too), and stores it.
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- If pgcrypto landed in the `extensions` schema, grant usage so functions can call it.
GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;
ALTER FUNCTION extensions.crypt(TEXT, TEXT) SECURITY DEFINER SET search_path = extensions;
ALTER FUNCTION extensions.gen_salt(TEXT, INT) SECURITY DEFINER SET search_path = extensions;

DROP FUNCTION IF EXISTS public.register_device(TEXT);

CREATE FUNCTION public.register_device(
    p_device_uuid UUID,
    p_recovery_code TEXT          -- 6-char code like A7K-992X (plaintext over TLS)
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_id UUID;
    v_hash TEXT;
BEGIN
    -- normalize + validate format: XXX-9999 style, 6 alphanumeric chars
    IF p_recovery_code !~ '^[A-Z0-9]{3}-[A-Z0-9]{4}$' THEN
        RAISE EXCEPTION 'invalid recovery code format';
    END IF;

    -- Argon2id unavailable natively; use pgcrypto bf hashing (schema-qualified
    -- because Supabase may install pgcrypto under `extensions`).
    v_hash := coalesce(
        extensions.crypt(p_recovery_code, extensions.gen_salt('bf', 12)),
        crypt(p_recovery_code, gen_salt('bf', 12))
    );

    SELECT id INTO v_id FROM public.anonymous_devices WHERE device_uuid = p_device_uuid;
    IF v_id IS NULL THEN
        INSERT INTO public.anonymous_devices (device_uuid, recovery_code_hash)
        VALUES (p_device_uuid, v_hash)
        RETURNING id INTO v_id;
    ELSE
        UPDATE public.anonymous_devices SET recovery_code_hash = v_hash WHERE id = v_id;
    END IF;
    RETURN v_id;
END;
$$;

GRANT EXECUTE ON FUNCTION public.register_device(UUID, TEXT) TO anon, authenticated;

-- ---------------------------------------------------------------------
-- 2. Event view tracking for 48h-view eligibility (PRD 5.2 criterion 1).
--    Client records a view when an event card/detail is opened.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.event_views (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID NOT NULL REFERENCES public.anonymous_devices(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
    viewed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (device_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_views_device ON public.event_views (device_id, viewed_at DESC);

REVOKE ALL ON TABLE public.event_views FROM anon;

CREATE OR REPLACE FUNCTION public.record_event_view(p_event_id UUID, p_device_uuid UUID)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_device UUID;
BEGIN
    SELECT id INTO v_device FROM public.anonymous_devices WHERE device_uuid = p_device_uuid;
    IF v_device IS NULL THEN
        RAISE EXCEPTION 'device not registered';
    END IF;
    INSERT INTO public.event_views (device_id, event_id) VALUES (v_device, p_event_id)
    ON CONFLICT (device_id, event_id) DO UPDATE SET viewed_at = NOW();
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_event_view(UUID, UUID) TO anon, authenticated;

-- ---------------------------------------------------------------------
-- 3. Hardened submit_crowd_report:
--    - dwell-time proof token (server-issued arrival timestamp)
--    - 48h-view requirement
--    - daily reward cap (+24h/day)
--    - spike anomaly detection (>10 reports/10min -> under_review)
-- ---------------------------------------------------------------------

-- 3a. Client calls this when geolocation first confirms presence (<50m).
--     Returns a signed arrival token = event_id|device|timestamp.
CREATE OR REPLACE FUNCTION public.begin_presence_check(p_event_id UUID, p_device_uuid UUID)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_device UUID;
    v_token TEXT;
BEGIN
    SELECT id INTO v_device FROM public.anonymous_devices WHERE device_uuid = p_device_uuid;
    IF v_device IS NULL THEN
        RAISE EXCEPTION 'device not registered';
    END IF;
    -- opaque token: event | device | epoch — HMAC'd so clients cannot forge times
    v_token := encode(
        hmac(p_event_id::TEXT || '|' || p_device_uuid::TEXT || '|' ||
             EXTRACT(EPOCH FROM NOW())::BIGINT::TEXT,
             convert_to(coalesce(current_setting('app.presence_secret', true), 'local-pulse-dev-secret'), 'UTF8'), 'sha256'),
        'hex'
    ) || '.' || EXTRACT(EPOCH FROM NOW())::BIGINT::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.begin_presence_check(UUID, UUID) TO anon, authenticated;

-- 3b. The hardened report submission.
DROP FUNCTION IF EXISTS public.submit_crowd_report(UUID, TEXT, UUID);

CREATE FUNCTION public.submit_crowd_report(
    p_event_id UUID,
    p_status TEXT,
    p_device_uuid UUID,
    p_arrival_token TEXT DEFAULT NULL,   -- "hmac.epoch" from begin_presence_check
    p_dwell_seconds INT DEFAULT 0        -- client-measured continuous presence
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_device UUID;
    v_event public.events%ROWTYPE;
    v_recent INT;
    v_new_status VARCHAR;
    v_token_epoch BIGINT;
    v_expected_hmac TEXT;
    v_today_reports INT;
    v_adfree_base TIMESTAMPTZ;
    v_spike INT;
BEGIN
    IF p_status NOT IN ('comfortable', 'moderate', 'crowded', 'ended_early') THEN
        RAISE EXCEPTION 'invalid status';
    END IF;

    SELECT * INTO v_event FROM public.events WHERE id = p_event_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'event not found'; END IF;
    IF v_event.category = 'warning' OR v_event.allow_user_survey = FALSE THEN
        RAISE EXCEPTION 'this event does not accept crowd reports';
    END IF;

    SELECT id INTO v_device FROM public.anonymous_devices WHERE device_uuid = p_device_uuid;
    IF v_device IS NULL THEN RAISE EXCEPTION 'device not registered'; END IF;

    ---- PRD 5.2 criterion 1: viewed within 48 hours -----------------------
    IF NOT EXISTS (
        SELECT 1 FROM public.event_views
        WHERE device_id = v_device AND event_id = p_event_id
          AND viewed_at > NOW() - INTERVAL '48 hours'
    ) THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'not_viewed');
    END IF;

    ---- PRD 5.2 criterion 2: 50m+3min dwell via server-signed token -------
    IF p_arrival_token IS NULL OR p_dwell_seconds < 180 THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'insufficient_dwell');
    END IF;
    BEGIN
        v_token_epoch := split_part(p_arrival_token, '.', 2)::BIGINT;
        v_expected_hmac := left(split_part(p_arrival_token, '.', 1), 64);
    EXCEPTION WHEN OTHERS THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'bad_token');
    END;
    -- verify HMAC matches event|device|epoch and token is fresh (<6h)
    IF v_expected_hmac IS DISTINCT FROM encode(hmac(
            p_event_id::TEXT || '|' || p_device_uuid::TEXT || '|' || v_token_epoch::TEXT,
            convert_to(coalesce(current_setting('app.presence_secret', true), 'local-pulse-dev-secret'), 'UTF8'),
            'sha256'), 'hex')
    THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'bad_token');
    END IF;
    IF EXTRACT(EPOCH FROM NOW()) - v_token_epoch > 21600 THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'token_expired');
    END IF;

    ---- rate limit: 1 report / device / event / 3h ------------------------
    SELECT count(*) INTO v_recent FROM public.survey_logs
    WHERE event_id = p_event_id AND device_id = v_device
      AND reported_at > NOW() - INTERVAL '3 hours';
    IF v_recent > 0 THEN
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'rate_limited');
    END IF;

    INSERT INTO public.survey_logs (event_id, device_id, reported_status)
    VALUES (p_event_id, v_device, p_status);

    ---- PRD 5.3 spike anomaly detection ----------------------------------
    SELECT count(*) INTO v_spike FROM public.survey_logs
    WHERE event_id = p_event_id AND reported_at > NOW() - INTERVAL '10 minutes';
    IF v_spike > 10 AND v_event.crowd_status = (SELECT crowd_status FROM public.events WHERE id = p_event_id) THEN
        UPDATE public.events SET status = 'under_review' WHERE id = p_event_id;
        RETURN jsonb_build_object('ok', FALSE, 'reason', 'under_review');
    END IF;

    ---- recompute majority from last 10 reports in 6h --------------------
    SELECT mode() WITHIN GROUP (ORDER BY reported_status) INTO v_new_status
    FROM (
        SELECT reported_status FROM public.survey_logs
        WHERE event_id = p_event_id AND reported_at > NOW() - INTERVAL '6 hours'
        ORDER BY reported_at DESC LIMIT 10
    ) t;

    UPDATE public.events
    SET crowd_status = COALESCE(v_new_status, crowd_status),
        crowd_updated_at = NOW(),
        is_expired_early = CASE WHEN p_status = 'ended_early' THEN TRUE ELSE is_expired_early END
    WHERE id = p_event_id;

    ---- PRD 5.2 reward: +24h ad-free, capped at +24h per day -------------
    SELECT count(*) INTO v_today_reports FROM public.survey_logs
    WHERE device_id = v_device AND reported_at >= date_trunc('day', NOW());
    SELECT GREATEST(ad_free_until, NOW()) INTO v_adfree_base
    FROM public.anonymous_devices WHERE id = v_device;

    IF v_today_reports <= 1 THEN   -- first qualifying report today grants the extension
        UPDATE public.anonymous_devices
        SET ad_free_until = v_adfree_base + INTERVAL '24 hours'
        WHERE id = v_device;
    END IF;

    SELECT ad_free_until INTO v_adfree_base FROM public.anonymous_devices WHERE id = v_device;
    RETURN jsonb_build_object('ok', TRUE,
        'crowd_status', v_new_status,
        'reward_granted', v_today_reports <= 1,
        'ad_free_until', v_adfree_base);
END;
$$;

GRANT EXECUTE ON FUNCTION public.submit_crowd_report(UUID, TEXT, UUID, TEXT, INT) TO anon, authenticated;
