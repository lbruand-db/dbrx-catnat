-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — CatNat events (hand-seeded)
-- MAGIC
-- MAGIC A small list of real recent CatNat events drives the Act 2 ("what just
-- MAGIC happened?") demo flow. Hand-seeded as a literal table — only ~6 rows,
-- MAGIC no generation logic. Update by editing the VALUES list below.
-- MAGIC
-- MAGIC `event_id` is the stable join key used by `portfolio_claims` (Phase 1).

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  COMMENT 'Silver: recent French CatNat events, hand-seeded. Drives Act 2 of the demo (claims triage on a specific event).'
  TBLPROPERTIES (
    'catnat.layer'     = 'events',
    'catnat.medallion' = 'silver'
  )
AS
SELECT
  event_id, event_name, event_type, event_date, jo_publication_date,
  affected_depts, description
FROM (VALUES
  ('STORM_CIARAN_2023',  'Tempête Ciarán',         'storm',
   DATE'2023-11-02', DATE'2023-12-15',
   ARRAY('22','29','35','44','50','56'),
   'Tempête atlantique majeure: vents > 150 km/h sur la façade nord-ouest, ~1.5 Md€ d''indemnisations.'),
  ('STORM_DOMINGOS_2023','Tempête Domingos',       'storm',
   DATE'2023-11-04', DATE'2023-12-15',
   ARRAY('17','33','40','44','64','85'),
   'Suit Ciarán de 48h. Côte aquitaine, rafales 130 km/h, dégâts secondaires sur sols déjà saturés.'),
  ('FLOOD_VAR_ALEX_2020','Tempête Alex (Vésubie)', 'flood',
   DATE'2020-10-02', DATE'2020-10-09',
   ARRAY('06'),
   'Crue éclair de la Vésubie et de la Roya, ~30 victimes, plusieurs villages détruits.'),
  ('FLOOD_GARD_2002',    'Inondations du Gard',    'flood',
   DATE'2002-09-08', DATE'2002-12-17',
   ARRAY('30','34','13','84'),
   'Épisode cévenol historique: ~700 mm en 24h, 24 victimes, > 1 Md€ de dommages.'),
  ('DROUGHT_RGA_2022',   'Sécheresse RGA 2022',    'drought',
   DATE'2022-08-31', DATE'2023-03-30',
   ARRAY('all'),
   'Année record de sécheresse: ~2.9 Md€ d''indemnisations RGA, reconnu CatNat dans > 7000 communes.'),
  ('STORM_EUNICE_2022',  'Tempête Eunice',         'storm',
   DATE'2022-02-18', DATE'2022-03-25',
   ARRAY('29','22','35','50','56','76'),
   'Tempête nord-atlantique, rafales 160 km/h en Bretagne, dégâts assurés ~150 M€ en France.')
) AS t(event_id, event_name, event_type, event_date, jo_publication_date,
       affected_depts, description);

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN event_id            COMMENT 'Identifiant événement / Stable event key (PK)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN event_name          COMMENT 'Nom usuel / Common name (e.g. Tempête Ciarán)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN event_type          COMMENT 'Type de péril / Peril type (flood | drought | storm)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN event_date          COMMENT 'Date de l''événement / Event date';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN jo_publication_date COMMENT 'Date d''arrêté CatNat (JO) / Journal Officiel CatNat declaration';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.events')
  ALTER COLUMN affected_depts      COMMENT 'Codes départements concernés / Affected department codes (or [''all''])';

-- COMMAND ----------

SELECT event_type, COUNT(*) AS n_events,
       MIN(event_date) AS earliest,
       MAX(event_date) AS latest
FROM IDENTIFIER(:catalog || '.catnat_silver.events')
GROUP BY event_type
ORDER BY event_type;
