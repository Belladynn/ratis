
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT UNIQUE NOT NULL,
  provider      TEXT NOT NULL DEFAULT 'email'
                CHECK (provider IN ('google', 'apple', 'email')),
  provider_id   TEXT,
  password_hash TEXT,
  created_at    TIMESTAMP NOT NULL DEFAULT now(),
  updated_at    TIMESTAMP NOT NULL DEFAULT now(),
  display_name  TEXT,
  avatar_url    TEXT,
  current_level_id UUID,
  CONSTRAINT email_format CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  CONSTRAINT auth_coherence CHECK (
    (provider = 'email'  AND password_hash IS NOT NULL AND provider_id IS NULL) OR
    (provider != 'email' AND provider_id   IS NOT NULL AND password_hash IS NULL)
  ),
  UNIQUE (provider, provider_id)
);
CREATE TABLE categories (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  parent_id  UUID,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT name_not_empty CHECK (name != ''),
  CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE SET NULL
);
CREATE OR REPLACE FUNCTION fn_check_category_cycle()
RETURNS TRIGGER AS $$
DECLARE
  current_id UUID;
BEGIN
  current_id := NEW.parent_id;
  WHILE current_id IS NOT NULL LOOP
    IF current_id = NEW.id THEN
      RAISE EXCEPTION 'Cycle détecté dans la hiérarchie des catégories : id=%', NEW.id;
    END IF;
    SELECT parent_id INTO current_id FROM categories WHERE id = current_id;
  END LOOP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_categories_no_cycle
BEFORE INSERT OR UPDATE OF parent_id ON categories
FOR EACH ROW
WHEN (NEW.parent_id IS NOT NULL)
EXECUTE FUNCTION fn_check_category_cycle();
CREATE TABLE stores (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  brand       TEXT,
  address     TEXT,
  city        TEXT,
  postal_code TEXT,
  lat         DECIMAL(9,6) NOT NULL,
  lng         DECIMAL(9,6) NOT NULL,
  is_disabled  BOOLEAN NOT NULL DEFAULT false,
  disabled_at  TIMESTAMP,
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT name_not_empty    CHECK (name        != ''),
  CONSTRAINT brand_not_empty   CHECK (brand        IS NULL OR brand        != ''),
  CONSTRAINT city_not_empty    CHECK (city         IS NULL OR city         != ''),
  CONSTRAINT address_not_empty CHECK (address      IS NULL OR address      != ''),
  CONSTRAINT postal_not_empty  CHECK (postal_code  IS NULL OR postal_code  != ''),
  CONSTRAINT lat_range         CHECK (lat  BETWEEN -90  AND  90),
  CONSTRAINT lng_range         CHECK (lng  BETWEEN -180 AND 180),
  CONSTRAINT disabled_at_check CHECK (
    (is_disabled = true  AND disabled_at IS NOT NULL) OR
    (is_disabled = false AND disabled_at IS NULL)
  )
);
CREATE UNIQUE INDEX unique_store ON stores (
  COALESCE(brand, ''), COALESCE(address, ''), COALESCE(postal_code, '')
);
CREATE TABLE products (
  ean         TEXT PRIMARY KEY NOT NULL,
  name        TEXT NOT NULL,
  photo_url   TEXT,
  category_id UUID,
  source      TEXT NOT NULL DEFAULT 'off'
              CHECK (source IN ('off', 'obp', 'opf', 'opff', 'internal')),
  unit        TEXT CHECK (unit IN ('kg', 'l', 'unit')),
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_category FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
  CONSTRAINT name_not_empty CHECK (name != ''),
  CONSTRAINT ean_format CHECK (ean ~ '^\d{8,14}$'),
  CONSTRAINT internal_has_unit CHECK (
    source != 'internal' OR unit IS NOT NULL
  ),
  -- catalogue sources (OFF/OBP/OPF/OPFF) are all packaged-by-EAN, no unit.
  -- Renamed from off_no_unit by migration 20260511_0900_obp_opf.
  CONSTRAINT catalogue_no_unit CHECK (
    source NOT IN ('off', 'obp', 'opf', 'opff') OR unit IS NULL
  ),
  CONSTRAINT internal_ean_prefix CHECK (
    source != 'internal' OR ean LIKE '2%'
  )
);
CREATE TABLE receipts (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID,
  store_id     UUID NOT NULL,
  purchased_at DATE NOT NULL,
  tva_total    DECIMAL(10,2),
  total_amount DECIMAL(10,2),
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_at   TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user  FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE SET NULL,
  CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE RESTRICT,
  CONSTRAINT tva_pos          CHECK (tva_total    IS NULL OR tva_total    >= 0),
  CONSTRAINT total_amount_pos CHECK (total_amount IS NULL OR total_amount >  0),
  CONSTRAINT purchased_not_future CHECK (purchased_at <= CURRENT_DATE)
);
CREATE TABLE scans (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID,
  store_id     UUID NOT NULL,
  product_ean  TEXT,
  scanned_name TEXT,
  price        DECIMAL(10,2) NOT NULL,
  quantity     DECIMAL(10,3) NOT NULL DEFAULT 1,
  tva_amount   DECIMAL(10,2),
  scan_type    TEXT NOT NULL CHECK (
                 scan_type IN ('receipt', 'electronic_label', 'manual')
               ),
  receipt_id   UUID,
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (
                 status IN ('pending', 'unmatched', 'accepted', 'rejected')
               ),
  rejected_reason TEXT,
  scanned_at        TIMESTAMP NOT NULL DEFAULT now(),
  status_updated_at TIMESTAMP NOT NULL DEFAULT now(),
  image_url TEXT,
  CONSTRAINT fk_user    FOREIGN KEY (user_id)     REFERENCES users(id)     ON DELETE SET NULL,
  CONSTRAINT fk_store   FOREIGN KEY (store_id)    REFERENCES stores(id)    ON DELETE RESTRICT,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE SET NULL,
  CONSTRAINT fk_receipt FOREIGN KEY (receipt_id)  REFERENCES receipts(id)  ON DELETE SET NULL,
  CONSTRAINT price_pos    CHECK (price > 0),
  CONSTRAINT quantity_pos CHECK (quantity > 0),
  CONSTRAINT tva_pos      CHECK (tva_amount IS NULL OR tva_amount >= 0),
  CONSTRAINT tva_receipt_only CHECK (tva_amount IS NULL OR scan_type = 'receipt'),
  CONSTRAINT receipt_required CHECK (
    (scan_type = 'receipt'  AND receipt_id IS NOT NULL) OR
    (scan_type != 'receipt' AND receipt_id IS NULL)
  ),
  CONSTRAINT rejected_reason_check CHECK (
    (status = 'rejected' AND rejected_reason IS NOT NULL) OR
    (status != 'rejected' AND rejected_reason IS NULL)
  ),
  CONSTRAINT unmatched_requires_null_ean CHECK (
    (status = 'unmatched' AND product_ean IS NULL) OR
    status != 'unmatched'
  ),
  CONSTRAINT unmatched_not_manual CHECK (
    NOT (status = 'unmatched' AND scan_type = 'manual')
  ),
  CONSTRAINT unmatched_requires_scanned_name CHECK (
    status != 'unmatched' OR scanned_name IS NOT NULL
  ),
  CONSTRAINT accepted_requires_ean CHECK (
    status != 'accepted' OR product_ean IS NOT NULL
  ),
  CONSTRAINT manual_no_scanned_name CHECK (
    scan_type != 'manual' OR (
      product_ean IS NOT NULL AND scanned_name IS NULL
    )
  ),
  UNIQUE (user_id, store_id, product_ean, scanned_at)
);
CREATE OR REPLACE FUNCTION fn_check_scan_status_transition()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status = NEW.status THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'accepted' AND NEW.status != 'accepted' THEN
    RAISE EXCEPTION 'Transition interdite : un scan accepté ne peut pas changer de statut (id=%)', OLD.id;
  END IF;
  IF OLD.status = 'rejected' AND NEW.status != 'rejected' THEN
    RAISE EXCEPTION 'Transition interdite : un scan rejeté ne peut pas changer de statut (id=%)', OLD.id;
  END IF;
  NEW.status_updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_scan_status_transition
BEFORE UPDATE OF status ON scans
FOR EACH ROW EXECUTE FUNCTION fn_check_scan_status_transition();
CREATE TABLE price_consensus (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  store_id      UUID NOT NULL,
  product_ean   TEXT NOT NULL,
  price         DECIMAL(10,2) NOT NULL,
  trust_score   DECIMAL(5,2) NOT NULL
                CHECK (trust_score >= 0 AND trust_score <= 100),
  first_seen_at TIMESTAMP NOT NULL,
  last_seen_at  TIMESTAMP NOT NULL,
  frozen_until  TIMESTAMP,
  computed_at   TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_store   FOREIGN KEY (store_id)    REFERENCES stores(id)    ON DELETE RESTRICT,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE RESTRICT,
  CONSTRAINT price_pos        CHECK (price > 0),
  CONSTRAINT seen_order       CHECK (first_seen_at <= last_seen_at),
  CONSTRAINT frozen_in_future CHECK (frozen_until IS NULL OR frozen_until > now()),
  UNIQUE (store_id, product_ean)
);
CREATE TABLE price_consensus_scans (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  consensus_id UUID NOT NULL,
  scan_id      UUID NOT NULL,
  CONSTRAINT fk_consensus FOREIGN KEY (consensus_id) REFERENCES price_consensus(id) ON DELETE CASCADE,
  CONSTRAINT fk_scan      FOREIGN KEY (scan_id)      REFERENCES scans(id)           ON DELETE RESTRICT,
  UNIQUE (consensus_id, scan_id)
);
CREATE TABLE price_consensus_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  consensus_id  UUID NOT NULL,
  store_id      UUID NOT NULL,
  product_ean   TEXT NOT NULL,
  price         DECIMAL(10,2) NOT NULL,
  trust_score   DECIMAL(5,2)  NOT NULL,
  first_seen_at TIMESTAMP     NOT NULL,
  last_seen_at  TIMESTAMP     NOT NULL,
  frozen_until  TIMESTAMP,
  recorded_at   TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_consensus FOREIGN KEY (consensus_id) REFERENCES price_consensus(id) ON DELETE CASCADE,
  CONSTRAINT fk_store     FOREIGN KEY (store_id)     REFERENCES stores(id)          ON DELETE RESTRICT,
  CONSTRAINT fk_product   FOREIGN KEY (product_ean)  REFERENCES products(ean)       ON DELETE RESTRICT,
  CONSTRAINT price_pos    CHECK (price > 0),
  CONSTRAINT trust_range  CHECK (trust_score >= 0 AND trust_score <= 100),
  CONSTRAINT seen_order   CHECK (first_seen_at <= last_seen_at)
);
CREATE TABLE shopping_lists (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL,
  name             TEXT NOT NULL DEFAULT '',
  has_default_name BOOLEAN NOT NULL DEFAULT true,
  created_at       TIMESTAMP NOT NULL DEFAULT now(),
  updated_at       TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE OR REPLACE FUNCTION fn_shopping_list_name()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.has_default_name = true AND trim(NEW.name) != '' THEN
    NEW.name = '';
  END IF;
  IF NEW.name IS NULL OR trim(NEW.name) = '' THEN
    NEW.name             = '';
    NEW.has_default_name = true;
  ELSE
    NEW.has_default_name = false;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_shopping_list_name
BEFORE INSERT OR UPDATE OF name, has_default_name ON shopping_lists
FOR EACH ROW EXECUTE FUNCTION fn_shopping_list_name();
CREATE TABLE shopping_list_items (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  list_id     UUID NOT NULL,
  product_ean TEXT NOT NULL,
  quantity    DECIMAL(10,3) NOT NULL DEFAULT 1,
  checked     BOOLEAN NOT NULL DEFAULT false,
  checked_at  TIMESTAMP,
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_list    FOREIGN KEY (list_id)     REFERENCES shopping_lists(id) ON DELETE CASCADE,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean)      ON DELETE RESTRICT,
  CONSTRAINT quantity_pos CHECK (quantity > 0),
  CONSTRAINT checked_at_check CHECK (
    (checked = true  AND checked_at IS NOT NULL) OR
    (checked = false AND checked_at IS NULL)
  ),
  UNIQUE (list_id, product_ean)
);
CREATE TABLE product_tracking (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL,
  product_ean    TEXT NOT NULL,
  active         BOOLEAN NOT NULL DEFAULT true,
  created_at     TIMESTAMP NOT NULL DEFAULT now(),
  updated_at     TIMESTAMP NOT NULL DEFAULT now(),
  deactivated_at TIMESTAMP,
  avg_quantity       DECIMAL(10,3),
  avg_frequency_days INTEGER,
  CONSTRAINT fk_user    FOREIGN KEY (user_id)     REFERENCES users(id)     ON DELETE CASCADE,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE RESTRICT,
  CONSTRAINT deactivated_check CHECK (
    (active = false AND deactivated_at IS NOT NULL) OR
    (active = true  AND deactivated_at IS NULL)
  ),
  CONSTRAINT avg_quantity_pos   CHECK (avg_quantity       IS NULL OR avg_quantity       > 0),
  CONSTRAINT avg_frequency_pos  CHECK (avg_frequency_days IS NULL OR avg_frequency_days > 0),
  UNIQUE (user_id, product_ean)
);
CREATE TABLE user_push_tokens (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  token      TEXT NOT NULL UNIQUE,
  platform   TEXT NOT NULL CHECK (platform IN ('ios', 'android', 'web')),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_push_tokens_user ON user_push_tokens(user_id);
CREATE TABLE user_preferences (
  user_id          UUID PRIMARY KEY,
  search_radius_km INTEGER NOT NULL DEFAULT 5
                   CHECK (search_radius_km > 0 AND search_radius_km <= 50),
  transport_mode   TEXT NOT NULL DEFAULT 'driving'
                   CHECK (transport_mode IN ('driving', 'walking', 'cycling')),
  created_at       TIMESTAMP NOT NULL DEFAULT now(),
  updated_at       TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE optimized_routes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  list_id       UUID NOT NULL,
  total_price   DECIMAL(10,2) NOT NULL,
  total_savings DECIMAL(10,2) NOT NULL
                DEFAULT 0,
  distance_km   DECIMAL(8,2),
  steps         JSONB NOT NULL,
  computed_at   TIMESTAMP NOT NULL DEFAULT now(),
  expires_at    TIMESTAMP NOT NULL
                DEFAULT now() + INTERVAL '48 hours',
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_list FOREIGN KEY (list_id) REFERENCES shopping_lists(id) ON DELETE CASCADE,
  CONSTRAINT total_price_pos   CHECK (total_price > 0),
  CONSTRAINT total_savings_pos CHECK (total_savings >= 0),
  CONSTRAINT savings_lte_price CHECK (total_savings <= total_price),
  CONSTRAINT distance_pos      CHECK (distance_km IS NULL OR distance_km >= 0),
  CONSTRAINT expires_after_computed CHECK (expires_at > computed_at)
);
CREATE INDEX idx_routes_user    ON optimized_routes(user_id, computed_at DESC);
CREATE INDEX idx_routes_expires ON optimized_routes(expires_at)
  WHERE expires_at > now();
CREATE TABLE reward_config (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action_type TEXT NOT NULL UNIQUE CHECK (
                action_type IN (
                  'DAILY_LOGIN',
                  'SCAN_RECEIPT',
                  'VIDEO_SCAN',
                  'PRICE_CHALLENGE'
                )
              ),
  base_amount INTEGER NOT NULL CHECK (base_amount > 0),
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE TABLE streak_tiers (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  days         INTEGER NOT NULL UNIQUE CHECK (days > 0),
  multiplier   DECIMAL(4,2) NOT NULL CHECK (multiplier > 1),
  label        TEXT NOT NULL,
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_at   TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT label_not_empty CHECK (label != '')
);
CREATE TABLE level_tiers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  level           INTEGER NOT NULL UNIQUE CHECK (level > 0),
  label           TEXT NOT NULL,
  cab_threshold   INTEGER NOT NULL CHECK (cab_threshold >= 0),
  created_at      TIMESTAMP NOT NULL DEFAULT now(),
  updated_at      TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT label_not_empty    CHECK (label != ''),
  CONSTRAINT cab_threshold_unique UNIQUE (cab_threshold)
);
ALTER TABLE users
  ADD CONSTRAINT fk_current_level
  FOREIGN KEY (current_level_id) REFERENCES level_tiers(id) ON DELETE SET NULL;
CREATE TABLE badges (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  description TEXT NOT NULL,
  icon_url    TEXT,
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT code_not_empty  CHECK (code != ''),
  CONSTRAINT code_uppercase  CHECK (code = upper(code))
);
CREATE TABLE user_cab_balance (
  user_id    UUID PRIMARY KEY,
  balance    INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
  updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE user_cashback_balance (
  user_id    UUID PRIMARY KEY,
  balance    DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
  updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE cabecoin_transactions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,
  action_type TEXT NOT NULL CHECK (
                action_type IN ('DAILY_LOGIN', 'SCAN_RECEIPT', 'VIDEO_SCAN', 'PRICE_CHALLENGE', 'BOOST_CASHBACK')
              ),
  direction   TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
  base_amount INTEGER NOT NULL CHECK (base_amount > 0),
  rate        DECIMAL(4,2),
  rate_reason TEXT,
  scan_id     UUID,
  receipt_id  UUID,
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
  CONSTRAINT fk_scan    FOREIGN KEY (scan_id)    REFERENCES scans(id)    ON DELETE SET NULL,
  CONSTRAINT fk_receipt FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE SET NULL,
  CONSTRAINT rate_coherence CHECK (
    (rate IS NOT NULL AND rate_reason IS NOT NULL) OR
    (rate IS NULL     AND rate_reason IS NULL)
  ),
  CONSTRAINT rate_only_video CHECK (
    rate IS NULL OR action_type = 'VIDEO_SCAN'
  ),
  CONSTRAINT rate_pos CHECK (rate IS NULL OR rate > 0),
  CONSTRAINT boost_is_debit CHECK (
    (action_type = 'BOOST_CASHBACK' AND direction = 'debit') OR
    (action_type != 'BOOST_CASHBACK' AND direction = 'credit')
  )
);
CREATE TABLE affiliate_offers (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider       TEXT NOT NULL CHECK (provider IN ('affilae', 'awin', 'cj')),
  external_id    TEXT NOT NULL,
  product_ean    TEXT,
  store_brand    TEXT,
  cashback_rate  DECIMAL(5,4) NOT NULL,
  valid_from     TIMESTAMP NOT NULL,
  valid_until    TIMESTAMP,
  created_at     TIMESTAMP NOT NULL DEFAULT now(),
  updated_at     TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_product    FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE RESTRICT,
  CONSTRAINT external_unique UNIQUE (provider, external_id),
  CONSTRAINT rate_pos       CHECK (cashback_rate > 0),
  CONSTRAINT valid_range    CHECK (valid_until IS NULL OR valid_until > valid_from)
);
CREATE INDEX idx_affiliate_offers_ean   ON affiliate_offers(product_ean)  WHERE product_ean IS NOT NULL;
CREATE INDEX idx_affiliate_offers_brand ON affiliate_offers(store_brand)   WHERE store_brand IS NOT NULL;
CREATE INDEX idx_affiliate_offers_valid ON affiliate_offers(valid_until)   WHERE valid_until IS NOT NULL;
CREATE TABLE cashback_transactions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL,
  type            TEXT NOT NULL CHECK (
                    type IN (
                      'CREDIT',
                      'BOOST',
                      'WITHDRAWAL',
                      'SUBSCRIPTION_PAYMENT'
                    )
                  ),
  amount          DECIMAL(10,2) NOT NULL CHECK (amount > 0),
  product_ean     TEXT,
  affiliate_offer_id UUID,
  boost_applied   BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user             FOREIGN KEY (user_id)            REFERENCES users(id)            ON DELETE CASCADE,
  CONSTRAINT fk_product          FOREIGN KEY (product_ean)        REFERENCES products(ean)        ON DELETE RESTRICT,
  CONSTRAINT fk_affiliate_offer  FOREIGN KEY (affiliate_offer_id) REFERENCES affiliate_offers(id) ON DELETE SET NULL,
  CONSTRAINT credit_requires_product CHECK (
    type NOT IN ('CREDIT', 'BOOST') OR product_ean IS NOT NULL
  ),
  CONSTRAINT credit_requires_offer CHECK (
    type NOT IN ('CREDIT', 'BOOST') OR affiliate_offer_id IS NOT NULL
  )
);
CREATE TABLE discount_campaigns (
  code        TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  type        TEXT NOT NULL CHECK (type IN ('percentage', 'fixed')),
  value       DECIMAL(10,2) NOT NULL
              CHECK (value > 0),
  valid_from  TIMESTAMP,
  valid_until TIMESTAMP,
  max_uses    INTEGER
              CHECK (max_uses IS NULL OR max_uses > 0),
  uses_count  INTEGER NOT NULL DEFAULT 0
              CHECK (uses_count >= 0),
  is_public   BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  updated_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT code_not_empty  CHECK (code != ''),
  CONSTRAINT label_not_empty CHECK (label != ''),
  CONSTRAINT code_uppercase  CHECK (code = upper(code)),
  CONSTRAINT valid_range CHECK (
    valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from
  ),
  CONSTRAINT uses_not_exceed_max CHECK (
    max_uses IS NULL OR uses_count <= max_uses
  ),
  CONSTRAINT percentage_max CHECK (
    type != 'percentage' OR value <= 100
  )
);
CREATE INDEX idx_discount_campaigns_valid  ON discount_campaigns(valid_until)
  WHERE valid_until IS NOT NULL;
CREATE INDEX idx_discount_campaigns_active ON discount_campaigns(valid_from, valid_until)
  WHERE valid_from IS NOT NULL OR valid_until IS NOT NULL;
CREATE INDEX idx_discount_campaigns_public ON discount_campaigns(is_public)
  WHERE is_public = true;
CREATE TABLE subscriptions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'cancelled', 'expired')),
  price                  DECIMAL(10,2) NOT NULL DEFAULT 11.99,
  paid_with              TEXT NOT NULL DEFAULT 'stripe',
  discount_campaign_code TEXT,
  discount_amount        DECIMAL(10,2),
  started_at      TIMESTAMP NOT NULL DEFAULT now(),
  expires_at      TIMESTAMP NOT NULL,
  cancelled_at    TIMESTAMP,
  CONSTRAINT fk_user          FOREIGN KEY (user_id)               REFERENCES users(id)              ON DELETE CASCADE,
  CONSTRAINT fk_discount      FOREIGN KEY (discount_campaign_code) REFERENCES discount_campaigns(code) ON DELETE RESTRICT,
  CONSTRAINT price_pos        CHECK (price > 0),
  CONSTRAINT discount_coherence CHECK (
    (discount_campaign_code IS NOT NULL AND discount_amount IS NOT NULL) OR
    (discount_campaign_code IS NULL     AND discount_amount IS NULL)
  ),
  CONSTRAINT discount_amount_pos CHECK (
    discount_amount IS NULL OR discount_amount > 0
  ),
  CONSTRAINT discount_not_exceed_price CHECK (
    discount_amount IS NULL OR discount_amount < price
  ),
  CONSTRAINT expires_after_start CHECK (expires_at > started_at),
  CONSTRAINT cancelled_check CHECK (
    (status = 'cancelled' AND cancelled_at IS NOT NULL) OR
    (status != 'cancelled' AND cancelled_at IS NULL)
  )
);
CREATE INDEX idx_subscriptions_user   ON subscriptions(user_id, started_at DESC);
CREATE INDEX idx_subscriptions_active ON subscriptions(user_id)
  WHERE status = 'active';
CREATE UNIQUE INDEX idx_one_active_subscription ON subscriptions(user_id)
  WHERE status = 'active';
CREATE TABLE user_streaks (
  user_id               UUID PRIMARY KEY,
  daily_streak          INTEGER NOT NULL DEFAULT 0 CHECK (daily_streak >= 0),
  daily_streak_best     INTEGER NOT NULL DEFAULT 0,
  last_login_date       DATE,
  weekly_streak         INTEGER NOT NULL DEFAULT 0 CHECK (weekly_streak >= 0),
  weekly_streak_best    INTEGER NOT NULL DEFAULT 0,
  last_scan_week        INTEGER,
  last_scan_year        INTEGER,
  updated_at            TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT best_gte_current CHECK (
    daily_streak_best  >= daily_streak AND
    weekly_streak_best >= weekly_streak
  ),
  CONSTRAINT scan_week_coherence CHECK (
    (last_scan_week IS NULL AND last_scan_year IS NULL) OR
    (last_scan_week IS NOT NULL AND last_scan_year IS NOT NULL)
  ),
  CONSTRAINT scan_week_range CHECK (
    last_scan_week IS NULL OR last_scan_week BETWEEN 1 AND 53
  )
);
CREATE TABLE user_badges (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  badge_id   UUID NOT NULL,
  unlocked_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user  FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
  CONSTRAINT fk_badge FOREIGN KEY (badge_id) REFERENCES badges(id) ON DELETE CASCADE,
  UNIQUE (user_id, badge_id)
);
CREATE INDEX idx_user_badges_user ON user_badges(user_id);
CREATE TABLE leaderboard_snapshots (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,
  period_year  INTEGER NOT NULL CHECK (period_year BETWEEN 2024 AND 2100),
  period_month INTEGER NOT NULL CHECK (period_month BETWEEN 1 AND 12),
  cab_earned  INTEGER NOT NULL CHECK (cab_earned >= 0),
  rank        INTEGER NOT NULL CHECK (rank > 0),
  created_at  TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE (user_id, period_year, period_month)
);
CREATE INDEX idx_leaderboard_period ON leaderboard_snapshots(period_year, period_month, rank);
CREATE INDEX idx_leaderboard_user   ON leaderboard_snapshots(user_id, period_year DESC, period_month DESC);
CREATE TABLE price_challenges (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id          UUID NOT NULL,
  store_id         UUID NOT NULL,
  product_ean      TEXT,
  image_crop_url   TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'validated', 'rejected')),
  validated_price  DECIMAL(10,2),
  trust_score      DECIMAL(5,2) NOT NULL DEFAULT 0
                   CHECK (trust_score >= 0 AND trust_score <= 100),
  created_at       TIMESTAMP NOT NULL DEFAULT now(),
  updated_at       TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_scan    FOREIGN KEY (scan_id)     REFERENCES scans(id)     ON DELETE RESTRICT,
  CONSTRAINT fk_store   FOREIGN KEY (store_id)    REFERENCES stores(id)    ON DELETE RESTRICT,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE SET NULL,
  UNIQUE (scan_id),
  CONSTRAINT validated_coherence CHECK (
    (status = 'validated' AND validated_price IS NOT NULL) OR
    (status != 'validated' AND validated_price IS NULL)
  ),
  CONSTRAINT validated_price_pos CHECK (
    validated_price IS NULL OR validated_price > 0
  )
);
CREATE INDEX idx_price_challenges_pending ON price_challenges(store_id, created_at ASC)
  WHERE status = 'pending';
CREATE INDEX idx_price_challenges_scan    ON price_challenges(scan_id);
CREATE TABLE price_challenge_responses (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  challenge_id UUID NOT NULL,
  user_id      UUID,
  price        DECIMAL(10,2) NOT NULL CHECK (price > 0),
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_challenge FOREIGN KEY (challenge_id) REFERENCES price_challenges(id) ON DELETE CASCADE,
  CONSTRAINT fk_user      FOREIGN KEY (user_id)      REFERENCES users(id)            ON DELETE SET NULL,
  UNIQUE (challenge_id, user_id)
);
CREATE INDEX idx_challenge_responses      ON price_challenge_responses(challenge_id);
CREATE INDEX idx_challenge_responses_user ON price_challenge_responses(user_id)
  WHERE user_id IS NOT NULL;
CREATE TABLE price_alerts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  product_ean   TEXT NOT NULL,
  store_id      UUID,
  target_price  DECIMAL(10,2) NOT NULL CHECK (target_price > 0),
  active        BOOLEAN NOT NULL DEFAULT true,
  triggered_at  TIMESTAMP,
  created_at    TIMESTAMP NOT NULL DEFAULT now(),
  updated_at    TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user    FOREIGN KEY (user_id)     REFERENCES users(id)     ON DELETE CASCADE,
  CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES products(ean) ON DELETE CASCADE,
  CONSTRAINT fk_store   FOREIGN KEY (store_id)    REFERENCES stores(id)    ON DELETE CASCADE,
  CONSTRAINT triggered_check CHECK (
    triggered_at IS NULL OR active = false
  ),
  UNIQUE (user_id, product_ean, store_id, target_price)
);
CREATE INDEX idx_price_alerts_active ON price_alerts(product_ean, store_id)
  WHERE active = true;
CREATE INDEX idx_price_alerts_user ON price_alerts(user_id);
CREATE TABLE cashback_withdrawals (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 UUID NOT NULL,
  amount                  DECIMAL(10,2) NOT NULL CHECK (amount > 0),
  status                  TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'processed', 'failed')),
  cashback_transaction_id UUID,
  payment_provider_ref    TEXT,
  provider_initiated_at   TIMESTAMP,
  last_reconciled_at      TIMESTAMP,
  requested_at            TIMESTAMP NOT NULL DEFAULT now(),
  processed_at            TIMESTAMP,
  failure_reason          TEXT,
  updated_at              TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user        FOREIGN KEY (user_id)                 REFERENCES users(id)                ON DELETE RESTRICT,
  CONSTRAINT fk_transaction FOREIGN KEY (cashback_transaction_id) REFERENCES cashback_transactions(id) ON DELETE RESTRICT,
  CONSTRAINT processed_check CHECK (
    (status = 'processed' AND processed_at IS NOT NULL) OR
    (status != 'processed' AND processed_at IS NULL)
  ),
  CONSTRAINT transaction_required CHECK (
    status != 'processed' OR cashback_transaction_id IS NOT NULL
  ),
  CONSTRAINT failure_check CHECK (
    (status = 'failed' AND failure_reason IS NOT NULL) OR
    (status != 'failed' AND failure_reason IS NULL)
  ),
  CONSTRAINT provider_coherence CHECK (
    (payment_provider_ref IS NOT NULL AND provider_initiated_at IS NOT NULL) OR
    (payment_provider_ref IS NULL     AND provider_initiated_at IS NULL)
  )
);
CREATE INDEX idx_withdrawals_user        ON cashback_withdrawals(user_id, requested_at DESC);
CREATE INDEX idx_withdrawals_pending     ON cashback_withdrawals(status, requested_at ASC)
  WHERE status = 'pending';
CREATE INDEX idx_withdrawals_reconcile   ON cashback_withdrawals(last_reconciled_at)
  WHERE status = 'pending' AND payment_provider_ref IS NOT NULL;
CREATE TABLE notification_logs (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  type       TEXT NOT NULL CHECK (type IN (
               'price_drop',
               'streak_reminder',
               'weekly_recap',
               'challenge_available',
               'cashback_credited',
               'level_up'
             )),
  payload    JSONB,
  sent_at    TIMESTAMP NOT NULL DEFAULT now(),
  read_at    TIMESTAMP,
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_notif_user_unread ON notification_logs(user_id, sent_at DESC)
  WHERE read_at IS NULL;
CREATE INDEX idx_notif_type ON notification_logs(type, sent_at DESC);
CREATE TABLE user_store_preferences (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  store_id   UUID NOT NULL,
  preference TEXT NOT NULL CHECK (preference IN ('favourite', 'excluded')),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user  FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE,
  CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
  UNIQUE (user_id, store_id)
);
CREATE INDEX idx_store_prefs_user ON user_store_preferences(user_id, preference);
CREATE TABLE user_sessions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL,
  platform   TEXT NOT NULL CHECK (platform IN ('ios', 'android', 'web')),
  started_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_sessions_user  ON user_sessions(user_id, started_at DESC);
CREATE INDEX idx_sessions_daily ON user_sessions(started_at DESC);
CREATE TABLE user_session_stats (
  user_id       UUID NOT NULL,
  period_year   INTEGER NOT NULL CHECK (period_year BETWEEN 2024 AND 2100),
  period_month  INTEGER NOT NULL CHECK (period_month BETWEEN 1 AND 12),
  ios_count     INTEGER NOT NULL DEFAULT 0 CHECK (ios_count     >= 0),
  android_count INTEGER NOT NULL DEFAULT 0 CHECK (android_count >= 0),
  web_count     INTEGER NOT NULL DEFAULT 0 CHECK (web_count     >= 0),
  CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, period_year, period_month)
);
CREATE INDEX idx_session_stats_period ON user_session_stats(period_year, period_month);
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_categories_updated_at
BEFORE UPDATE ON categories
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_stores_updated_at
BEFORE UPDATE ON stores
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_products_updated_at
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_receipts_updated_at
BEFORE UPDATE ON receipts
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_shopping_lists_updated_at
BEFORE UPDATE ON shopping_lists
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_product_tracking_updated_at
BEFORE UPDATE ON product_tracking
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_shopping_list_items_updated_at
BEFORE UPDATE ON shopping_list_items
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_user_preferences_updated_at
BEFORE UPDATE ON user_preferences
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_reward_config_updated_at
BEFORE UPDATE ON reward_config
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE OR REPLACE FUNCTION fn_increment_discount_uses()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.discount_campaign_code IS NOT NULL THEN
    UPDATE discount_campaigns
      SET uses_count = uses_count + 1
      WHERE code = NEW.discount_campaign_code
        AND (max_uses IS NULL OR uses_count < max_uses)
        AND (valid_from  IS NULL OR valid_from  <= now())
        AND (valid_until IS NULL OR valid_until >= now());
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Code promo % invalide, expiré ou épuisé', NEW.discount_campaign_code;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_subscription_discount_uses
AFTER INSERT ON subscriptions
FOR EACH ROW EXECUTE FUNCTION fn_increment_discount_uses();
CREATE TRIGGER trg_discount_campaigns_updated_at
BEFORE UPDATE ON discount_campaigns
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_streak_tiers_updated_at
BEFORE UPDATE ON streak_tiers
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_level_tiers_updated_at
BEFORE UPDATE ON level_tiers
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_user_cab_balance_updated_at
BEFORE UPDATE ON user_cab_balance
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_user_cashback_balance_updated_at
BEFORE UPDATE ON user_cashback_balance
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_affiliate_offers_updated_at
BEFORE UPDATE ON affiliate_offers
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_user_streaks_updated_at
BEFORE UPDATE ON user_streaks
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_price_challenges_updated_at
BEFORE UPDATE ON price_challenges
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_price_alerts_updated_at
BEFORE UPDATE ON price_alerts
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_cashback_withdrawals_updated_at
BEFORE UPDATE ON cashback_withdrawals
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE VIEW price_history AS
SELECT
  id           AS observation_id,
  store_id,
  product_ean,
  price,
  quantity,
  scan_type,
  scanned_name,
  scanned_at   AS recorded_at
FROM scans
WHERE status = 'accepted';
CREATE VIEW product_observed_names AS
SELECT DISTINCT
  product_ean,
  store_id,
  scan_type,
  scanned_name
FROM scans
WHERE scanned_name IS NOT NULL
  AND status = 'accepted'
ORDER BY product_ean, store_id, scan_type;
CREATE VIEW leaderboard_weekly AS
SELECT
  user_id,
  SUM(base_amount * COALESCE(rate, 1)) AS cab_earned_week,
  RANK() OVER (ORDER BY SUM(base_amount * COALESCE(rate, 1)) DESC) AS rank
FROM cabecoin_transactions
WHERE direction = 'credit'
  AND created_at >= now() - INTERVAL '7 days'
GROUP BY user_id;
CREATE INDEX idx_products_category            ON products(category_id);
CREATE INDEX idx_scans_product                ON scans(product_ean, scanned_at DESC)
  WHERE product_ean IS NOT NULL;
CREATE INDEX idx_scans_pending                ON scans(store_id, scanned_at ASC)
  WHERE status = 'pending';
CREATE INDEX idx_scans_unmatched              ON scans(store_id, scanned_at DESC)
  WHERE status = 'unmatched';
CREATE INDEX idx_scans_rejected               ON scans(store_id, scanned_at DESC)
  WHERE status = 'rejected';
CREATE INDEX idx_scans_store                  ON scans(store_id, scanned_at DESC);
CREATE INDEX idx_scans_receipt                ON scans(receipt_id, store_id, scanned_at DESC)
  WHERE receipt_id IS NOT NULL;
CREATE INDEX idx_scans_user_store             ON scans(user_id, store_id, scanned_at DESC)
  WHERE user_id IS NOT NULL;
CREATE INDEX idx_consensus_product            ON price_consensus(product_ean);
CREATE INDEX idx_consensus_trust              ON price_consensus(trust_score DESC);
CREATE INDEX idx_consensus_last_seen          ON price_consensus(last_seen_at DESC);
CREATE INDEX idx_consensus_frozen             ON price_consensus(frozen_until)
  WHERE frozen_until IS NOT NULL;
CREATE INDEX idx_consensus_scans_scan         ON price_consensus_scans(scan_id);
CREATE INDEX idx_receipts_user                ON receipts(user_id, purchased_at)
  WHERE user_id IS NOT NULL;
CREATE INDEX idx_stores_geo                   ON stores(lat, lng);
CREATE INDEX idx_stores_disabled              ON stores(is_disabled)
  WHERE is_disabled = true;
CREATE INDEX idx_consensus_hist_store_product ON price_consensus_history(store_id, product_ean, recorded_at DESC);
CREATE INDEX idx_consensus_hist_product       ON price_consensus_history(product_ean, recorded_at DESC);
CREATE INDEX idx_consensus_hist_consensus     ON price_consensus_history(consensus_id, recorded_at DESC);
CREATE INDEX idx_tracking_active ON product_tracking(user_id)
  WHERE active = true;
CREATE INDEX idx_shopping_lists_user          ON shopping_lists(user_id);
CREATE INDEX idx_shopping_lists_default       ON shopping_lists(user_id, has_default_name)
  WHERE has_default_name = true;
CREATE INDEX idx_list_items_unchecked ON shopping_list_items(list_id)
  WHERE checked = false;
CREATE INDEX idx_cab_tx_user       ON cabecoin_transactions(user_id, created_at DESC);
CREATE INDEX idx_cab_tx_action     ON cabecoin_transactions(action_type, created_at DESC);
CREATE INDEX idx_cab_tx_credits    ON cabecoin_transactions(user_id, created_at DESC)
  WHERE direction = 'credit';
CREATE INDEX idx_cashback_tx_user    ON cashback_transactions(user_id, created_at DESC);
CREATE INDEX idx_cashback_tx_type    ON cashback_transactions(type, created_at DESC);
CREATE INDEX idx_cashback_tx_product ON cashback_transactions(product_ean)
  WHERE product_ean IS NOT NULL;
CREATE INDEX idx_user_badges_badge ON user_badges(badge_id);
CREATE INDEX idx_subscriptions_discount ON subscriptions(discount_campaign_code)
  WHERE discount_campaign_code IS NOT NULL;
CREATE INDEX idx_push_tokens_platform ON user_push_tokens(platform);