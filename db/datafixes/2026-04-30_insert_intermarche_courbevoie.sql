-- ============================================================
-- DATAFIX — Insert manuel : Intermarché Courbevoie
-- ============================================================
-- Date    : 2026-04-30
-- Source  : ticket alpha (Guillaume) + Google Maps coords
-- Raison  : OSM bulk import (PR #203 fix) skipait les ways
--           polygones, dont ce store. En attendant le re-run du
--           batch en prod, on insère manuellement pour débloquer
--           le contract test pipeline_v3 (Intermarché Courbevoie
--           sert d'oracle absolu).
--
-- Idempotent : ON CONFLICT DO NOTHING sur (lat, lng) ne suffit
-- pas (pas de UNIQUE), donc on guard via NOT EXISTS sur l'adresse.
--
-- Exécution :
--   psql ratis -f db/datafixes/2026-04-30_insert_intermarche_courbevoie.sql
-- ============================================================

INSERT INTO stores (
  name,
  retailer,
  address,
  city,
  postal_code,
  lat,
  lng,
  source,
  is_disabled
)
SELECT
  'Intermarché',
  'intermarche',
  '18 ter rue de Bezons',
  'Courbevoie',
  '92400',
  48.89363400829515,
  2.252612498120094,
  'admin',
  false
WHERE NOT EXISTS (
  SELECT 1 FROM stores
  WHERE address = '18 ter rue de Bezons'
    AND city = 'Courbevoie'
);

-- Trace
INSERT INTO datafix_logs (procedure, params, notes)
VALUES (
  'manual_insert_store',
  jsonb_build_object(
    'name', 'Intermarché',
    'address', '18 ter rue de Bezons',
    'city', 'Courbevoie',
    'lat', 48.89363400829515,
    'lng', 2.252612498120094
  ),
  'Manual insert pour débloquer pipeline_v3 contract test (Intermarché Courbevoie alpha)'
);
