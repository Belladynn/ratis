-- ============================================================
-- RATIS — PROCÉDURES DE DATAFIX
-- ============================================================
-- Ces procédures sont destinées exclusivement à l'administration.
-- Elles ne doivent être exécutées que par un utilisateur PostgreSQL
-- disposant du rôle ratis_admin.
--
-- Principe :
--   - Chaque datafix est une procédure stockée nommée df_<description>
--   - Chaque exécution est tracée dans datafix_logs
--   - Les procédures sont idempotentes quand c'est possible
--   - Elles ne contiennent jamais de logique métier applicative
--
-- Usage :
--   CALL df_fix_product_ean('<ancien_ean>', '<nouvel_ean>', 'raison');
--
-- Créer le rôle admin (à exécuter une seule fois) :
--   CREATE ROLE ratis_admin;
--   GRANT EXECUTE ON ALL PROCEDURES IN SCHEMA public TO ratis_admin;
-- ============================================================


-- ============================================================
-- TABLE DE TRACE DES DATAFIXES
-- ============================================================
CREATE TABLE IF NOT EXISTS datafix_logs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  procedure    TEXT NOT NULL,
  params       JSONB NOT NULL DEFAULT '{}',
  executed_by  TEXT NOT NULL DEFAULT current_user,
  executed_at  TIMESTAMP NOT NULL DEFAULT now(),
  notes        TEXT
);

-- ============================================================
-- TABLE DE SAUVEGARDE AVANT DATAFIX
-- Snapshot de l'état courant avant toute opération destructive.
-- Permet le rollback manuel en cas de datafix trop agressif.
--
-- current_state : état exact des lignes AVANT modification (JSON)
-- expected_state : état attendu APRÈS modification (JSON, optionnel)
--
-- Rollback : lire current_state et réappliquer manuellement les valeurs.
-- ============================================================
CREATE TABLE IF NOT EXISTS datafix_backup (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  datafix_log_id UUID,                    -- lien vers datafix_logs une fois exécuté
  action         TEXT NOT NULL,           -- ex: 'DELETE product', 'UPDATE scan status'
  impacted_table TEXT NOT NULL,           -- ex: 'products', 'scans'
  impacted_id    TEXT NOT NULL,           -- PK de la ligne impactée (UUID ou EAN)
  current_state  JSONB NOT NULL,          -- état AVANT datafix
  expected_state JSONB,                   -- état APRÈS datafix attendu (NULL si suppression)
  backed_up_at   TIMESTAMP NOT NULL DEFAULT now(),
  backed_up_by   TEXT NOT NULL DEFAULT current_user,

  CONSTRAINT fk_log FOREIGN KEY (datafix_log_id)
    REFERENCES datafix_logs(id) ON DELETE SET NULL
);


-- ============================================================
-- df_fix_product_ean
-- Corrige un EAN incorrect en migrant toutes les références
-- vers le nouvel EAN, puis supprime ou désactive l'ancien produit.
--
-- Séquence :
--   0a. Migrer shopping_list_items
--   0b. Migrer product_tracking
--   1.  Rejeter les scans pending/unmatched (accepted reste accepted)
--   2.  Détacher les scans des snapshots historiques
--   3.  Supprimer les snapshots historiques
--   4.  Détacher les scans du consensus actif
--   5.  Supprimer le consensus actif
--   6.  Supprimer l'ancien produit (le nouvel EAN doit déjà exister en base)
-- ============================================================
CREATE OR REPLACE PROCEDURE df_fix_product_ean(
  p_ancien_ean TEXT,
  p_nouvel_ean TEXT,
  p_raison     TEXT DEFAULT 'datafix: correction EAN'
)
LANGUAGE plpgsql
AS $$
BEGIN
  -- Vérifications préalables
  IF NOT EXISTS (SELECT 1 FROM products WHERE ean = p_nouvel_ean) THEN
    RAISE EXCEPTION 'Le nouvel EAN % n''existe pas en base. Créer le produit avant d''exécuter ce datafix.', p_nouvel_ean;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM products WHERE ean = p_ancien_ean) THEN
    RAISE EXCEPTION 'L''ancien EAN % n''existe pas en base.', p_ancien_ean;
  END IF;

  IF p_ancien_ean = p_nouvel_ean THEN
    RAISE EXCEPTION 'Les deux EAN sont identiques.';
  END IF;

  -- BACKUP — snapshot de toutes les données impactées avant modification
  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT
      'UPDATE product_ean',
      'shopping_list_items',
      id::TEXT,
      to_jsonb(sli),
      jsonb_set(to_jsonb(sli), '{product_ean}', to_jsonb(p_nouvel_ean))
    FROM shopping_list_items sli WHERE product_ean = p_ancien_ean;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT
      'UPDATE product_ean',
      'product_tracking',
      id::TEXT,
      to_jsonb(pt),
      jsonb_set(to_jsonb(pt), '{product_ean}', to_jsonb(p_nouvel_ean))
    FROM product_tracking pt WHERE product_ean = p_ancien_ean;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT
      'UPDATE status → rejected',
      'scans',
      id::TEXT,
      to_jsonb(s),
      jsonb_set(to_jsonb(s), '{status}', '"rejected"')
    FROM scans s WHERE product_ean = p_ancien_ean AND status IN ('pending', 'unmatched');

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT 'DELETE', 'price_consensus_history', id::TEXT, to_jsonb(pch), NULL
    FROM price_consensus_history pch WHERE product_ean = p_ancien_ean;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT 'DELETE', 'price_consensus', id::TEXT, to_jsonb(pc), NULL
    FROM price_consensus pc WHERE product_ean = p_ancien_ean;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT 'DELETE', 'products', ean, to_jsonb(p), NULL
    FROM products p WHERE ean = p_ancien_ean;

  -- 0a. Migrer les listes de courses
  UPDATE shopping_list_items
    SET product_ean = p_nouvel_ean
    WHERE product_ean = p_ancien_ean;

  -- 0b. Migrer les suivis de prix
  UPDATE product_tracking
    SET product_ean = p_nouvel_ean
    WHERE product_ean = p_ancien_ean;

  -- 0c. Migrer les alertes prix
  UPDATE price_alerts
    SET product_ean = p_nouvel_ean
    WHERE product_ean = p_ancien_ean;

  -- 1. Rejeter les scans non traités
  -- (accepted reste accepted — état terminal et légitime)
  UPDATE scans
    SET status = 'rejected',
        rejected_reason = p_raison
    WHERE product_ean = p_ancien_ean
      AND status IN ('pending', 'unmatched');

  -- 2. Supprimer les snapshots historiques
  DELETE FROM price_consensus_history
    WHERE product_ean = p_ancien_ean;

  -- 4. Détacher les scans du consensus actif
  DELETE FROM price_consensus_scans
    WHERE consensus_id IN (
      SELECT id FROM price_consensus
      WHERE product_ean = p_ancien_ean
    );

  -- 5. Supprimer le consensus actif
  DELETE FROM price_consensus
    WHERE product_ean = p_ancien_ean;

  -- 6. Supprimer l'ancien produit
  DELETE FROM products WHERE ean = p_ancien_ean;

  -- Trace
  INSERT INTO datafix_logs (procedure, params, notes)
    VALUES (
      'df_fix_product_ean',
      jsonb_build_object('ancien_ean', p_ancien_ean, 'nouvel_ean', p_nouvel_ean),
      p_raison
    );

  RAISE NOTICE 'df_fix_product_ean exécuté : % → % (raison: %)',
    p_ancien_ean, p_nouvel_ean, p_raison;
END;
$$;


-- ============================================================
-- df_disable_store
-- Désactive un magasin (soft delete).
-- À utiliser quand un magasin ferme définitivement.
-- ============================================================
CREATE OR REPLACE PROCEDURE df_disable_store(
  p_store_id UUID,
  p_raison   TEXT DEFAULT 'datafix: magasin fermé'
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM stores WHERE id = p_store_id) THEN
    RAISE EXCEPTION 'Le magasin % n''existe pas.', p_store_id;
  END IF;

  IF EXISTS (SELECT 1 FROM stores WHERE id = p_store_id AND is_disabled = true) THEN
    RAISE NOTICE 'Le magasin % est déjà désactivé.', p_store_id;
    RETURN;
  END IF;

  UPDATE stores
    SET is_disabled = true,
        disabled_at = now()
    WHERE id = p_store_id;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT
      'UPDATE is_disabled → true',
      'stores',
      id::TEXT,
      to_jsonb(s),
      jsonb_set(jsonb_set(to_jsonb(s), '{is_disabled}', 'true'), '{disabled_at}', to_jsonb(now()::TEXT))
    FROM stores s WHERE id = p_store_id;

  INSERT INTO datafix_logs (procedure, params, notes)
    VALUES (
      'df_disable_store',
      jsonb_build_object('store_id', p_store_id),
      p_raison
    );

  RAISE NOTICE 'df_disable_store exécuté : store_id=% (raison: %)', p_store_id, p_raison;
END;
$$;


-- ============================================================
-- df_reactivate_store
-- Réactive un magasin précédemment désactivé.
-- ============================================================
CREATE OR REPLACE PROCEDURE df_reactivate_store(
  p_store_id UUID,
  p_raison   TEXT DEFAULT 'datafix: magasin réouvert'
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM stores WHERE id = p_store_id AND is_disabled = true) THEN
    RAISE EXCEPTION 'Le magasin % n''existe pas ou n''est pas désactivé.', p_store_id;
  END IF;

  UPDATE stores
    SET is_disabled = false,
        disabled_at = NULL
    WHERE id = p_store_id;

  INSERT INTO datafix_logs (procedure, params, notes)
    VALUES (
      'df_reactivate_store',
      jsonb_build_object('store_id', p_store_id),
      p_raison
    );

  RAISE NOTICE 'df_reactivate_store exécuté : store_id=% (raison: %)', p_store_id, p_raison;
END;
$$;


-- ============================================================
-- df_set_knowledge_correction
-- Renseigne ou crée une correction manuelle dans product_knowledge.
--
-- Cas 1 — entrée existante avec corrected IS NULL (token inconnu
--          découvert automatiquement) : met à jour corrected,
--          confidence et source.
-- Cas 2 — entrée inexistante : insère une nouvelle entrée manuelle
--          (pré-alimentation équipe Ratis, ex : P0T → POT).
-- Cas 3 — entrée existante avec corrected déjà renseigné :
--          bloqué sauf si p_force = true.
--
-- Usage :
--   CALL df_set_knowledge_correction('P0T', 'POT');
--   CALL df_set_knowledge_correction('P0T', 'POT', 'token', NULL, true);
-- ============================================================
CREATE OR REPLACE PROCEDURE df_set_knowledge_correction(
  p_raw_ocr    TEXT,
  p_corrected  TEXT,
  p_match_type TEXT    DEFAULT 'token',
  p_notes      TEXT    DEFAULT NULL,
  p_force      BOOLEAN DEFAULT false
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_existing RECORD;
BEGIN
  -- Validation match_type
  IF p_match_type NOT IN ('sequence', 'ngram', 'token') THEN
    RAISE EXCEPTION 'match_type invalide : %. Valeurs acceptées : sequence, ngram, token.', p_match_type;
  END IF;

  IF p_corrected IS NULL OR trim(p_corrected) = '' THEN
    RAISE EXCEPTION 'p_corrected ne peut pas être NULL ou vide.';
  END IF;

  SELECT * INTO v_existing FROM product_knowledge WHERE raw_ocr = p_raw_ocr;

  IF v_existing IS NULL THEN
    -- Cas 2 : nouvelle entrée manuelle
    INSERT INTO product_knowledge (raw_ocr, corrected, match_type, source, confidence, seen_count)
      VALUES (p_raw_ocr, p_corrected, p_match_type, 'manual', 1.0, 1);

    INSERT INTO datafix_logs (procedure, params, notes)
      VALUES (
        'df_set_knowledge_correction',
        jsonb_build_object('raw_ocr', p_raw_ocr, 'corrected', p_corrected, 'action', 'insert'),
        p_notes
      );

    RAISE NOTICE 'Entrée créée : % → %', p_raw_ocr, p_corrected;

  ELSIF v_existing.corrected IS NOT NULL AND NOT p_force THEN
    -- Cas 3 : déjà corrigé, protection
    RAISE EXCEPTION
      'product_knowledge.raw_ocr=''%'' a déjà une correction : ''%''. Utiliser p_force=true pour écraser.',
      p_raw_ocr, v_existing.corrected;

  ELSE
    -- Cas 1 (corrected IS NULL) ou Cas 3 forcé
    INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
      VALUES (
        'UPDATE knowledge correction',
        'product_knowledge',
        v_existing.id::TEXT,
        to_jsonb(v_existing),
        jsonb_build_object(
          'corrected',   p_corrected,
          'confidence',  1.0,
          'source',      'manual',
          'match_type',  p_match_type
        )
      );

    UPDATE product_knowledge
      SET corrected   = p_corrected,
          confidence  = 1.0,
          source      = 'manual',
          match_type  = p_match_type
      WHERE raw_ocr = p_raw_ocr;

    INSERT INTO datafix_logs (procedure, params, notes)
      VALUES (
        'df_set_knowledge_correction',
        jsonb_build_object('raw_ocr', p_raw_ocr, 'corrected', p_corrected, 'action', 'update'),
        p_notes
      );

    RAISE NOTICE 'Correction appliquée : % → %', p_raw_ocr, p_corrected;
  END IF;
END;
$$;


-- ============================================================
-- df_reject_scan
-- Rejette manuellement un scan spécifique avec une raison.
-- Bloqué si le scan est déjà accepted (état terminal).
-- ============================================================
CREATE OR REPLACE PROCEDURE df_reject_scan(
  p_scan_id UUID,
  p_raison  TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_status TEXT;
BEGIN
  SELECT status INTO v_status FROM scans WHERE id = p_scan_id;

  IF v_status IS NULL THEN
    RAISE EXCEPTION 'Le scan % n''existe pas.', p_scan_id;
  END IF;

  IF v_status = 'accepted' THEN
    RAISE EXCEPTION 'Le scan % est en état accepted (terminal) — impossible de le rejeter.', p_scan_id;
  END IF;

  IF v_status = 'rejected' THEN
    RAISE NOTICE 'Le scan % est déjà rejected.', p_scan_id;
    RETURN;
  END IF;

  INSERT INTO datafix_backup (action, impacted_table, impacted_id, current_state, expected_state)
    SELECT
      'UPDATE status → rejected',
      'scans',
      id::TEXT,
      to_jsonb(s),
      jsonb_set(to_jsonb(s), '{status}', '"rejected"')
    FROM scans s WHERE id = p_scan_id;

  UPDATE scans
    SET status = 'rejected',
        rejected_reason = p_raison
    WHERE id = p_scan_id;

  INSERT INTO datafix_logs (procedure, params, notes)
    VALUES (
      'df_reject_scan',
      jsonb_build_object('scan_id', p_scan_id),
      p_raison
    );

  RAISE NOTICE 'df_reject_scan exécuté : scan_id=% (raison: %)', p_scan_id, p_raison;
END;
$$;
