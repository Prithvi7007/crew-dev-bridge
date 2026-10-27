-- Reviewed operator-only SQL for wedding_db. Run as postgres in wedding_db.
-- Precondition: a separate LOGIN role wedding_reporter exists without table grants.
-- These views contain only global counts: never invitation tokens, names, notes,
-- phone numbers, RSVP details, admin identities or photo contents.
BEGIN;
CREATE SCHEMA IF NOT EXISTS wedding_reporting AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA wedding_reporting FROM PUBLIC;

CREATE OR REPLACE VIEW wedding_reporting.overview
WITH (security_barrier = true) AS
SELECT
  (SELECT COUNT(*) FROM public.invitations)::bigint AS invitation_count,
  (SELECT COUNT(*) FROM public.events)::bigint AS event_count,
  (SELECT COUNT(*) FROM public.rsvps)::bigint AS rsvp_count,
  (SELECT COUNT(*) FROM public.photo_uploads)::bigint AS photo_upload_count,
  (SELECT COUNT(*) FROM public.admin_change_requests)::bigint AS change_request_count;

CREATE OR REPLACE VIEW wedding_reporting.attendance
WITH (security_barrier = true) AS
SELECT
  COUNT(*) FILTER (WHERE attending = 'Yes')::bigint AS attending_yes,
  COUNT(*) FILTER (WHERE attending = 'No')::bigint AS attending_no,
  COUNT(*) FILTER (WHERE attending = 'Maybe')::bigint AS attending_maybe,
  COALESCE(SUM(guest_count) FILTER (WHERE attending = 'Yes'), 0)::bigint AS confirmed_guest_count
FROM public.rsvps;

CREATE OR REPLACE VIEW wedding_reporting.health
WITH (security_barrier = true) AS SELECT 'ready'::text AS status;

REVOKE ALL ON ALL TABLES IN SCHEMA wedding_reporting FROM PUBLIC;
GRANT USAGE ON SCHEMA wedding_reporting TO wedding_reporter;
GRANT SELECT ON wedding_reporting.overview, wedding_reporting.attendance,
  wedding_reporting.health TO wedding_reporter;
COMMIT;
