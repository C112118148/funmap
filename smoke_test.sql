-- Functional smoke test for the Local Pulse schema
\set ON_ERROR_STOP on

-- 1. Insert test events (Taipei coords)
INSERT INTO public.events (title, category, geom, start_time, end_time, is_verified) VALUES
('華山快閃市集', 'market', ST_SetSRID(ST_MakePoint(121.5276, 25.0421), 4326), NOW() - INTERVAL '1 hour', NOW() + INTERVAL '6 hours', TRUE),
('信義商圈優惠', 'promotion', ST_SetSRID(ST_MakePoint(121.5648, 25.0330), 4326), NOW() - INTERVAL '2 hours', NOW() + INTERVAL '3 hours', TRUE),
('過期展覽', 'exhibition', ST_SetSRID(ST_MakePoint(121.5100, 25.0500), 4326), NOW() - INTERVAL '48 hours', NOW() - INTERVAL '24 hours', TRUE),
('審核中事件', 'market', ST_SetSRID(ST_MakePoint(121.5200, 25.0400), 4326), NOW() - INTERVAL '1 hour', NOW() + INTERVAL '5 hours', FALSE);
UPDATE public.events SET status = 'under_review' WHERE title = '審核中事件';
UPDATE public.events SET is_expired_early = TRUE WHERE title = '過期展覽';

-- CHECK constraint should reject an out-of-range end_time
DO $test$
BEGIN
    BEGIN
        INSERT INTO public.events (title, category, geom, start_time, end_time)
        VALUES ('bad', 'market', ST_SetSRID(ST_MakePoint(121.5, 25.0), 4326), NOW(), NOW());
        RAISE EXCEPTION 'FAIL: end_time > start_time constraint not enforced';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: end_time constraint enforced';
    END;
END
$test$;

-- 2. BBox RPC returns only active, non-expired events
SELECT 'RPC result' AS step, id, title FROM public.get_events_in_bbox(121.40, 24.95, 121.65, 25.10);

-- 3. updated_at trigger fires
UPDATE public.events SET summary = 'updated' WHERE title = '華山快閃市集';
SELECT 'trigger' AS step,
       CASE WHEN updated_at > created_at THEN 'PASS: updated_at touched' ELSE 'FAIL' END AS result
FROM public.events WHERE title = '華山快閃市集';

-- 4. RLS: anon must NOT see under_review / expired-early events
SET ROLE anon;
SELECT 'RLS anon view' AS step,
       count(*) AS visible,
       CASE WHEN count(*) = 2 THEN 'PASS: only 2 active events visible to anon'
            ELSE 'FAIL: anon sees ' || count(*) || ' events (expected 2)' END AS result
FROM public.events;
RESET ROLE;

-- 5. anon can execute the RPC but cannot touch locked tables
SET ROLE anon;
SELECT 'anon RPC exec' AS step,
       CASE WHEN count(*) >= 1 THEN 'PASS: anon can call get_events_in_bbox' ELSE 'FAIL' END AS result
FROM public.get_events_in_bbox(121.40, 24.95, 121.65, 25.10);

DO $test$
BEGIN
    BEGIN
        PERFORM 1 FROM public.anonymous_devices LIMIT 1;
        RAISE EXCEPTION 'FAIL: anon could read anonymous_devices';
    EXCEPTION WHEN insufficient_privilege OR undefined_table THEN
        RAISE NOTICE 'PASS: anon blocked from anonymous_devices';
    END;
END
$test$;
RESET ROLE;
