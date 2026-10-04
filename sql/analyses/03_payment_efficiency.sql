-- =====================================================================================
-- 03 · Payment efficiency
-- =====================================================================================
-- Question : how fast and how "hands-free" are our payments?
-- Metrics  : average processing time (initiated -> settled), share taking over 48 hours,
--            straight-through processing (STP) rate, share needing manual repair, share
--            that missed the cut-off; by channel and rail.
-- Chart    : STP rate by channel (bar); average hours by rail (bar); stage durations (stacked bar).
-- So what  : moving LEGACY_FILE users to API, or submitting before cut-off, saves hours per payment.
-- Tables   : fact_payment, dim_payment_type, fact_payment_event
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 · Efficiency fields per payment
-- -------------------------------------------------------------------------------------
--   * is_stp: 1 = no human touched it end to end
--   * repair_count: number of manual fixes (e.g. bad account number corrected by ops)
--   * missed_cutoff: submitted after the rail's daily cut-off, so it rolled to the next day
--   * channel: how we sent it (API, H2H_FILE, PORTAL, LEGACY_FILE). INBOUND = receipts, excluded.
--   * direction = 'OUT' only: we only control how payments we SEND are processed.
WITH eff AS (
    SELECT p.payment_id,
           p.channel,
           t.rail,
           t.is_cross_border,
           p.is_stp,
           p.repair_count,
           p.missed_cutoff,
           (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 AS hours_total
    FROM fact_payment p
    JOIN dim_payment_type t ON t.type_id = p.type_id
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT * FROM eff LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 · STP, repair and cut-off rates by channel
-- -------------------------------------------------------------------------------------
-- AVG of a 0/1 column = the share of 1s, so 100.0 * AVG(...) is a percentage.
-- Expect: LEGACY_FILE STP clearly below API (about 88% vs 97.5%).
WITH eff AS (
    SELECT p.channel,
           p.is_stp,
           p.repair_count,
           p.missed_cutoff
    FROM fact_payment p
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT channel,
       COUNT(*) AS n,
       ROUND(100.0 * AVG(is_stp), 1)            AS stp_pct,
       ROUND(100.0 * AVG(repair_count > 0), 1)  AS repaired_pct,
       ROUND(100.0 * AVG(missed_cutoff), 1)     AS missed_cutoff_pct
FROM eff
GROUP BY channel
ORDER BY stp_pct;


-- -------------------------------------------------------------------------------------
-- Step 3 · Average hours and share over 48 hours, by rail
-- -------------------------------------------------------------------------------------
-- avg_hours is the average time to settle. pct_over_48h is the share of payments taking
-- more than 48 hours: (hours_total > 48) is 1 or 0, and AVG of 1s and 0s is the share of 1s.
-- Expect: BACS and CARD slowest, FAST and SEPA_INST under 2 hours.
WITH eff AS (
    SELECT t.rail,
           p.missed_cutoff,
           (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 AS hours_total
    FROM fact_payment p
    JOIN dim_payment_type t ON t.type_id = p.type_id
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT rail,
       COUNT(*) AS n,
       ROUND(AVG(hours_total), 1)                AS avg_hours,
       ROUND(100.0 * AVG(hours_total > 48), 1)   AS pct_over_48h
FROM eff
GROUP BY rail
ORDER BY avg_hours DESC;


-- What does missing the cut-off cost? Compare WITHIN a rail. Comparing across all payments
-- is misleading: slow rails (CARD) rarely have cut-offs, fast rails (GIRO, ACH) do.
WITH eff AS (
    SELECT t.rail,
           p.missed_cutoff,
           (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 AS hours_total
    FROM fact_payment p
    JOIN dim_payment_type t ON t.type_id = p.type_id
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT rail,
       missed_cutoff,
       COUNT(*) AS n,
       ROUND(AVG(hours_total), 1) AS avg_hours
FROM eff
GROUP BY rail, missed_cutoff
HAVING n >= 30          -- small groups give noisy averages
ORDER BY rail, missed_cutoff;


-- -------------------------------------------------------------------------------------
-- Step 4 · Where the time goes: stage durations from the event log
-- -------------------------------------------------------------------------------------
-- fact_payment_event has one row per step. Pivot the times needed into one row per payment:
-- CREATED -> APPROVED (internal approval), APPROVED -> SUBMITTED (waiting for the
-- batch / cut-off), SUBMITTED -> SETTLED (the bank). Only payments with these events count.
WITH ev AS (
    SELECT payment_id,
           MIN(CASE WHEN status = 'CREATED'   THEN event_ts END) AS created_ts,
           MIN(CASE WHEN status = 'APPROVED'  THEN event_ts END) AS approved_ts,
           MIN(CASE WHEN status = 'SUBMITTED' THEN event_ts END) AS submitted_ts,
           MIN(CASE WHEN status = 'SETTLED'   THEN event_ts END) AS settled_ts
    FROM fact_payment_event
    GROUP BY payment_id
)
SELECT COUNT(*) AS n,
       ROUND(AVG((julianday(approved_ts)  - julianday(created_ts))   * 24), 1) AS created_to_approved_h,
       ROUND(AVG((julianday(submitted_ts) - julianday(approved_ts))  * 24), 1) AS approved_to_submitted_h,
       ROUND(AVG((julianday(settled_ts)   - julianday(submitted_ts)) * 24), 1) AS submitted_to_settled_h
FROM ev
WHERE approved_ts IS NOT NULL
  AND submitted_ts IS NOT NULL
  AND settled_ts IS NOT NULL;


-- How much time does a sanctions hold add? Payments with a HELD event vs without.
WITH ev AS (
    SELECT payment_id,
           MIN(CASE WHEN status = 'CREATED' THEN event_ts END) AS created_ts,
           MIN(CASE WHEN status = 'SETTLED' THEN event_ts END) AS settled_ts,
           MAX(CASE WHEN status = 'HELD' THEN 1 ELSE 0 END)    AS was_held
    FROM fact_payment_event
    GROUP BY payment_id
)
SELECT was_held,
       COUNT(*) AS n,
       ROUND(AVG((julianday(settled_ts) - julianday(created_ts)) * 24), 1) AS avg_total_hours
FROM ev
WHERE settled_ts IS NOT NULL
GROUP BY was_held;


-- -------------------------------------------------------------------------------------
-- Step 5 · Save as views for the dashboard
-- -------------------------------------------------------------------------------------
-- The clean database is rebuilt on every build, so re-run this step after a rebuild.

DROP VIEW IF EXISTS vw_03_channel_efficiency;
CREATE VIEW vw_03_channel_efficiency AS
WITH eff AS (
    SELECT p.channel,
           p.is_stp,
           p.repair_count,
           p.missed_cutoff
    FROM fact_payment p
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT channel,
       COUNT(*) AS n,
       ROUND(100.0 * AVG(is_stp), 1)            AS stp_pct,
       ROUND(100.0 * AVG(repair_count > 0), 1)  AS repaired_pct,
       ROUND(100.0 * AVG(missed_cutoff), 1)     AS missed_cutoff_pct
FROM eff
GROUP BY channel;

DROP VIEW IF EXISTS vw_03_rail_timing;
CREATE VIEW vw_03_rail_timing AS
WITH eff AS (
    SELECT t.rail,
           (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 AS hours_total
    FROM fact_payment p
    JOIN dim_payment_type t ON t.type_id = p.type_id
    WHERE p.is_intercompany = 0
      AND p.direction = 'OUT'
      AND p.status IN ('completed', 'delayed')
)
SELECT rail,
       COUNT(*) AS n,
       ROUND(AVG(hours_total), 1)                AS avg_hours,
       ROUND(100.0 * AVG(hours_total > 48), 1)   AS pct_over_48h
FROM eff
GROUP BY rail;

DROP VIEW IF EXISTS vw_03_stage_durations;
CREATE VIEW vw_03_stage_durations AS
WITH ev AS (
    SELECT payment_id,
           MIN(CASE WHEN status = 'CREATED'   THEN event_ts END) AS created_ts,
           MIN(CASE WHEN status = 'APPROVED'  THEN event_ts END) AS approved_ts,
           MIN(CASE WHEN status = 'SUBMITTED' THEN event_ts END) AS submitted_ts,
           MIN(CASE WHEN status = 'SETTLED'   THEN event_ts END) AS settled_ts
    FROM fact_payment_event
    GROUP BY payment_id
)
SELECT COUNT(*) AS n,
       ROUND(AVG((julianday(approved_ts)  - julianday(created_ts))   * 24), 1) AS created_to_approved_h,
       ROUND(AVG((julianday(submitted_ts) - julianday(approved_ts))  * 24), 1) AS approved_to_submitted_h,
       ROUND(AVG((julianday(settled_ts)   - julianday(submitted_ts)) * 24), 1) AS submitted_to_settled_h
FROM ev
WHERE approved_ts IS NOT NULL
  AND submitted_ts IS NOT NULL
  AND settled_ts IS NOT NULL;
