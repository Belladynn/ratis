--
-- PostgreSQL database dump
--

\restrict mLdh4BTVaFsCnaCpy45DyRPhFs6Js3qboUps6VLUNfKH4C9Ac9X0kWUzf5hn749

-- Dumped from database version 16.13 (Debian 16.13-1.pgdg13+1)
-- Dumped by pg_dump version 16.13 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: EXTENSION unaccent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION unaccent IS 'text search dictionary that removes accents';


--
-- Name: achievement_category; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.achievement_category AS ENUM (
    'volume',
    'savings',
    'streak',
    'social',
    'exploration',
    'seasonal',
    'secret',
    'j_y_etais'
);


--
-- Name: achievement_rarity; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.achievement_rarity AS ENUM (
    'terracotta',
    'bronze',
    'copper',
    'silver',
    'gold',
    'emerald',
    'sapphire',
    'ruby',
    'crystal',
    'diamond'
);


--
-- Name: achievement_trigger_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.achievement_trigger_type AS ENUM (
    'scan_count',
    'savings_eur_total',
    'savings_eur_in_window',
    'streak_days',
    'referral_count',
    'unique_brands_count',
    'unique_categories_count',
    'unique_products_discovered_count',
    'first_event'
);


--
-- Name: admin_settings_audit_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.admin_settings_audit_status AS ENUM (
    'applied',
    'pending_2fa',
    'expired',
    'cancelled'
);


--
-- Name: fn_cascade_retailer_canonical_name_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_cascade_retailer_canonical_name_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.canonical_name IS DISTINCT FROM OLD.canonical_name THEN
                UPDATE stores
                SET retailer = NEW.canonical_name
                WHERE retailer_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: fn_check_category_cycle(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_check_category_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE current_id UUID;
        BEGIN
          current_id := NEW.parent_id;
          WHILE current_id IS NOT NULL LOOP
            IF current_id = NEW.id THEN
              RAISE EXCEPTION 'Cycle detected in category hierarchy: id=%', NEW.id;
            END IF;
            SELECT parent_id INTO current_id FROM categories WHERE id = current_id;
          END LOOP;
          RETURN NEW;
        END;
        $$;


--
-- Name: fn_check_scan_status_transition(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_check_scan_status_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF OLD.status = NEW.status THEN RETURN NEW; END IF;
          IF OLD.status = 'accepted' AND NEW.status != 'accepted' THEN
            RAISE EXCEPTION 'Forbidden transition: an accepted scan cannot change status (id=%)', OLD.id;
          END IF;
          IF OLD.status = 'rejected' AND NEW.status != 'rejected' THEN
            RAISE EXCEPTION 'Forbidden transition: a rejected scan cannot change status (id=%)', OLD.id;
          END IF;
          NEW.status_updated_at = now();
          RETURN NEW;
        END;
        $$;


--
-- Name: fn_increment_discount_uses(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_increment_discount_uses() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF NEW.status <> 'active' THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'active' THEN
            RETURN NEW;
          END IF;
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
        $$;


--
-- Name: fn_pipeline_audit_log_no_update(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_pipeline_audit_log_no_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'pipeline_audit_log is append-only — UPDATE prohibited';
        END;
        $$;


--
-- Name: fn_set_ubp_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_ubp_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;


--
-- Name: fn_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$;


--
-- Name: fn_shopping_list_name(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_shopping_list_name() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF NEW.has_default_name = true AND trim(NEW.name) != '' THEN NEW.name = ''; END IF;
          IF NEW.name IS NULL OR trim(NEW.name) = '' THEN
            NEW.name = ''; NEW.has_default_name = true;
          ELSE
            NEW.has_default_name = false;
          END IF;
          RETURN NEW;
        END;
        $$;


--
-- Name: fn_sync_pnr_retailer_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_sync_pnr_retailer_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.store_id IS NOT NULL AND NEW.retailer_id IS NULL THEN
                NEW.retailer_id := (
                    SELECT retailer_id FROM stores WHERE id = NEW.store_id
                );
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: fn_sync_store_retailer_text(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_sync_store_retailer_text() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.retailer_id IS NOT NULL THEN
                NEW.retailer := (
                    SELECT canonical_name
                    FROM retailers
                    WHERE id = NEW.retailer_id
                );
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: immutable_unaccent(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.immutable_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $_$ SELECT public.unaccent('public.unaccent', $1) $_$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: achievements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.achievements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    label text NOT NULL,
    description text NOT NULL,
    icon text NOT NULL,
    rarity public.achievement_rarity NOT NULL,
    category public.achievement_category NOT NULL,
    trigger_type public.achievement_trigger_type NOT NULL,
    target_value numeric NOT NULL,
    window_days integer,
    extra_params jsonb,
    cab_reward integer NOT NULL,
    is_secret boolean DEFAULT false NOT NULL,
    is_hidden boolean DEFAULT false NOT NULL,
    available_from timestamp with time zone,
    available_until timestamp with time zone,
    display_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_achievements_cab_nonneg CHECK ((cab_reward >= 0)),
    CONSTRAINT ck_achievements_no_jyetais_in_catalog CHECK ((category <> 'j_y_etais'::public.achievement_category)),
    CONSTRAINT ck_achievements_target_positive CHECK ((target_value > (0)::numeric)),
    CONSTRAINT ck_achievements_window_consistent CHECK (((available_until IS NULL) OR (available_from IS NULL) OR (available_until > available_from))),
    CONSTRAINT ck_achievements_window_positive CHECK (((window_days IS NULL) OR (window_days > 0)))
);


--
-- Name: admin_settings_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_settings_audit (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    operator text NOT NULL,
    section text NOT NULL,
    reason text NOT NULL,
    old_data jsonb,
    new_data jsonb NOT NULL,
    diff jsonb,
    status public.admin_settings_audit_status DEFAULT 'applied'::public.admin_settings_audit_status NOT NULL,
    expires_at timestamp with time zone,
    applied_at timestamp with time zone,
    CONSTRAINT chk_reason_min_len CHECK ((length(reason) >= 8)),
    CONSTRAINT chk_status_2fa_coherence CHECK ((((status = 'applied'::public.admin_settings_audit_status) AND (applied_at IS NOT NULL)) OR ((status = 'pending_2fa'::public.admin_settings_audit_status) AND (expires_at IS NOT NULL) AND (applied_at IS NULL)) OR ((status = ANY (ARRAY['expired'::public.admin_settings_audit_status, 'cancelled'::public.admin_settings_audit_status])) AND (applied_at IS NULL))))
);


--
-- Name: affiliate_offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.affiliate_offers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    external_id text NOT NULL,
    product_ean text NOT NULL,
    cashback_rate numeric(5,4) NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    brand_id uuid NOT NULL,
    CONSTRAINT provider_check CHECK ((provider = ANY (ARRAY['affilae'::text, 'awin'::text, 'cj'::text]))),
    CONSTRAINT rate_pos CHECK ((cashback_rate > (0)::numeric)),
    CONSTRAINT valid_range CHECK (((valid_until IS NULL) OR (valid_until > valid_from)))
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    section text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: badges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    icon_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT code_not_empty CHECK ((code <> ''::text)),
    CONSTRAINT code_uppercase CHECK ((code = upper(code)))
);


--
-- Name: batch_sync_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batch_sync_log (
    id bigint NOT NULL,
    batch_name text NOT NULL,
    last_run_at timestamp with time zone DEFAULT now() NOT NULL,
    status text NOT NULL,
    rows_affected integer
);


--
-- Name: batch_sync_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.batch_sync_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: batch_sync_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.batch_sync_log_id_seq OWNED BY public.batch_sync_log.id;


--
-- Name: battlepass_milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.battlepass_milestones (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    season_id uuid NOT NULL,
    milestone_number integer NOT NULL,
    cab_required integer NOT NULL,
    reward_type text NOT NULL,
    reward_value integer NOT NULL,
    subscriber_only boolean DEFAULT false NOT NULL,
    CONSTRAINT battlepass_milestones_reward_type_check CHECK ((reward_type = ANY (ARRAY['cab'::text, 'gift_card'::text, 'skin'::text])))
);


--
-- Name: battlepass_seasons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.battlepass_seasons (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    season_number integer NOT NULL,
    name text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    is_active boolean DEFAULT false NOT NULL
);


--
-- Name: brands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.brands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cabecoin_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cabecoin_transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    direction text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    amount integer NOT NULL,
    reason text NOT NULL,
    reference_id uuid,
    reference_type text,
    context jsonb,
    CONSTRAINT cabecoin_transactions_amount_check CHECK ((amount > 0)),
    CONSTRAINT cabecoin_transactions_direction_check CHECK ((direction = ANY (ARRAY['credit'::text, 'debit'::text]))),
    CONSTRAINT cabecoin_transactions_reason_check CHECK ((reason = ANY (ARRAY['receipt_scan'::text, 'label_scan'::text, 'barcode_scan'::text, 'product_identification'::text, 'fill_product_field'::text, 'scan_distinct'::text, 'promo_found'::text, 'mission_reward'::text, 'battlepass_milestone'::text, 'referral'::text, 'cashback_boost_debit'::text, 'cashback_boost_refund'::text, 'shop_purchase'::text, 'stonks_boost'::text, 'mission_freeze'::text, 'food_reserve_purchase'::text, 'streak_repair'::text, 'challenge_milestone'::text, 'mystery_product'::text, 'admin_adjustment'::text, 'retro_scan'::text, 'gift_card_purchase'::text, 'achievement_unlock'::text]))),
    CONSTRAINT cabecoin_transactions_reference_consistency_check CHECK (((reference_id IS NULL) = (reference_type IS NULL))),
    CONSTRAINT cabecoin_transactions_reference_type_check CHECK (((reference_type IS NULL) OR (reference_type = ANY (ARRAY['scan'::text, 'mission'::text, 'battlepass_milestone'::text, 'referral'::text, 'user_mission'::text, 'community_challenge_milestone'::text, 'admin'::text, 'retro_scan'::text, 'achievement'::text]))))
);


--
-- Name: cashback_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cashback_transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    type text NOT NULL,
    amount integer NOT NULL,
    product_ean text,
    affiliate_offer_id uuid,
    boost_applied boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    distributed_at timestamp with time zone,
    scan_id uuid,
    parent_transaction_id uuid,
    parent_type text,
    CONSTRAINT amount_pos CHECK (((amount)::numeric > (0)::numeric)),
    CONSTRAINT cashback_transactions_amount_check CHECK ((amount > 0)),
    CONSTRAINT cashback_transactions_parent_type_check CHECK ((parent_type = ANY (ARRAY['boost_parent'::text, 'withdrawal_refund'::text]))),
    CONSTRAINT cashback_transactions_type_check CHECK ((type = ANY (ARRAY['CREDIT'::text, 'BOOST'::text, 'WITHDRAWAL'::text]))),
    CONSTRAINT ck_cashback_transactions_amount_nn CHECK ((amount >= 0)),
    CONSTRAINT ck_cashback_transactions_status CHECK ((status = ANY (ARRAY['pending'::text, 'confirmed'::text, 'refused'::text]))),
    CONSTRAINT credit_requires_offer CHECK (((type <> ALL (ARRAY['CREDIT'::text, 'BOOST'::text])) OR (affiliate_offer_id IS NOT NULL))),
    CONSTRAINT credit_requires_product CHECK (((type <> ALL (ARRAY['CREDIT'::text, 'BOOST'::text])) OR (product_ean IS NOT NULL)))
);


--
-- Name: cashback_withdrawals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cashback_withdrawals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    amount integer NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    cashback_transaction_id uuid,
    payment_provider_ref text,
    provider_initiated_at timestamp with time zone,
    last_reconciled_at timestamp with time zone,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    failure_reason text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT amount_pos CHECK (((amount)::numeric > (0)::numeric)),
    CONSTRAINT cashback_withdrawals_amount_check CHECK ((amount > 0)),
    CONSTRAINT failure_check CHECK ((((status = 'failed'::text) AND (failure_reason IS NOT NULL)) OR ((status <> 'failed'::text) AND (failure_reason IS NULL)))),
    CONSTRAINT processed_check CHECK ((((status = 'processed'::text) AND (processed_at IS NOT NULL)) OR ((status <> 'processed'::text) AND (processed_at IS NULL)))),
    CONSTRAINT provider_coherence CHECK ((((payment_provider_ref IS NOT NULL) AND (provider_initiated_at IS NOT NULL)) OR ((payment_provider_ref IS NULL) AND (provider_initiated_at IS NULL)))),
    CONSTRAINT status_check CHECK ((status = ANY (ARRAY['pending'::text, 'processed'::text, 'failed'::text, 'abandoned'::text]))),
    CONSTRAINT transaction_required CHECK (((status <> 'processed'::text) OR (cashback_transaction_id IS NOT NULL)))
);


--
-- Name: categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.categories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    parent_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT name_not_empty CHECK ((name <> ''::text))
);


--
-- Name: cities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cities (
    postal_code text NOT NULL,
    city_name text NOT NULL,
    department text,
    country_code text DEFAULT 'FR'::text NOT NULL
);


--
-- Name: community_challenge_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_challenge_claims (
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    milestone_id uuid NOT NULL,
    user_id uuid,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: community_challenge_milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_challenge_milestones (
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    threshold integer NOT NULL,
    reward_type text NOT NULL,
    reward_value jsonb NOT NULL,
    label text,
    sort_order integer NOT NULL,
    CONSTRAINT community_challenge_milestones_reward_type_check CHECK ((reward_type = ANY (ARRAY['cab'::text, 'xp'::text, 'skin'::text, 'multiplier'::text])))
);


--
-- Name: community_challenge_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_challenge_progress (
    challenge_id uuid NOT NULL,
    current_count integer DEFAULT 0 NOT NULL,
    last_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT community_challenge_progress_count_nn CHECK ((current_count >= 0))
);


--
-- Name: community_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_challenges (
    id uuid NOT NULL,
    title text NOT NULL,
    description text,
    action_type text NOT NULL,
    action_filter jsonb,
    objective integer NOT NULL,
    starts_at timestamp with time zone DEFAULT now() NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    grace_period_days integer DEFAULT 3 NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: community_multipliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.community_multipliers (
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    user_id uuid,
    multiplier numeric NOT NULL,
    applies_to text NOT NULL,
    active_from timestamp with time zone NOT NULL,
    active_until timestamp with time zone NOT NULL,
    CONSTRAINT community_multipliers_applies_to_check CHECK ((applies_to = ANY (ARRAY['cab'::text, 'xp'::text, 'both'::text])))
);


--
-- Name: discount_campaigns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.discount_campaigns (
    code text NOT NULL,
    label text NOT NULL,
    type text NOT NULL,
    value numeric(10,2) NOT NULL,
    valid_from timestamp with time zone,
    valid_until timestamp with time zone,
    max_uses integer,
    uses_count integer DEFAULT 0 NOT NULL,
    is_public boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT code_not_empty CHECK ((code <> ''::text)),
    CONSTRAINT code_uppercase CHECK ((code = upper(code))),
    CONSTRAINT label_not_empty CHECK ((label <> ''::text)),
    CONSTRAINT max_uses_pos CHECK (((max_uses IS NULL) OR (max_uses > 0))),
    CONSTRAINT percentage_max CHECK (((type <> 'percentage'::text) OR (value <= (100)::numeric))),
    CONSTRAINT type_check CHECK ((type = ANY (ARRAY['percentage'::text, 'fixed'::text]))),
    CONSTRAINT uses_count_nn CHECK ((uses_count >= 0)),
    CONSTRAINT uses_not_exceed_max CHECK (((max_uses IS NULL) OR (uses_count <= max_uses))),
    CONSTRAINT valid_range CHECK (((valid_from IS NULL) OR (valid_until IS NULL) OR (valid_until > valid_from))),
    CONSTRAINT value_pos CHECK ((value > (0)::numeric))
);


--
-- Name: gift_card_brands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gift_card_brands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    provider_brand_id text NOT NULL,
    logo_url text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: gift_card_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gift_card_orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    brand_id uuid NOT NULL,
    denomination integer NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    source_type text NOT NULL,
    source_ref_id text NOT NULL,
    provider_order_id text,
    code text,
    issued_at timestamp with time zone,
    failed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    eligible_at timestamp with time zone,
    CONSTRAINT ck_gift_card_orders_source_type CHECK ((source_type = ANY (ARRAY['annual_subscription'::text, 'battlepass_milestone'::text, 'shop_purchase'::text, 'referral_reward'::text]))),
    CONSTRAINT ck_gift_card_orders_status CHECK ((status = ANY (ARRAY['pending'::text, 'issued'::text, 'failed'::text, 'churned'::text])))
);


--
-- Name: label_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.label_sessions (
    id uuid NOT NULL,
    user_id uuid,
    store_id uuid,
    scan_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: leaderboard_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.leaderboard_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    period_year integer NOT NULL,
    period_month integer NOT NULL,
    cab_earned integer NOT NULL,
    rank integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cab_earned_nn CHECK ((cab_earned >= 0)),
    CONSTRAINT month_range CHECK (((period_month >= 1) AND (period_month <= 12))),
    CONSTRAINT rank_pos CHECK ((rank > 0)),
    CONSTRAINT year_range CHECK (((period_year >= 2024) AND (period_year <= 2100)))
);


--
-- Name: leaderboard_weekly; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.leaderboard_weekly AS
 SELECT user_id,
    sum(amount) AS cab_earned_week,
    rank() OVER (ORDER BY (sum(amount)) DESC) AS rank
   FROM public.cabecoin_transactions
  WHERE ((direction = 'credit'::text) AND (created_at >= (now() - '7 days'::interval)))
  GROUP BY user_id;


--
-- Name: level_tiers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.level_tiers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    level integer NOT NULL,
    label text NOT NULL,
    cab_threshold integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cab_threshold_nn CHECK ((cab_threshold >= 0)),
    CONSTRAINT label_not_empty CHECK ((label <> ''::text)),
    CONSTRAINT level_pos CHECK ((level > 0))
);


--
-- Name: mission_xp_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mission_xp_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    mission_id uuid NOT NULL,
    user_mission_id uuid NOT NULL,
    xp_earned numeric NOT NULL,
    burst_count integer NOT NULL,
    buffer_count integer DEFAULT 0 NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mxr_buffer_count_nn CHECK ((buffer_count >= 0)),
    CONSTRAINT mxr_burst_count_nn CHECK ((burst_count >= 0)),
    CONSTRAINT mxr_xp_earned_positive CHECK ((xp_earned > (0)::numeric))
);


--
-- Name: missions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.missions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_type text NOT NULL,
    frequency text NOT NULL,
    difficulty text NOT NULL,
    target_count integer NOT NULL,
    cab_reward integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_boostable boolean DEFAULT true NOT NULL,
    qualifier text,
    CONSTRAINT missions_action_type_check CHECK ((action_type = ANY (ARRAY['receipt_scan'::text, 'label_scan'::text, 'barcode_scan'::text, 'product_identification'::text, 'price_compared'::text, 'fill_product_field'::text, 'scan_distinct'::text, 'promo_found'::text]))),
    CONSTRAINT missions_difficulty_check CHECK ((difficulty = ANY (ARRAY['easy'::text, 'medium'::text, 'hard'::text]))),
    CONSTRAINT missions_frequency_check CHECK ((frequency = ANY (ARRAY['daily'::text, 'weekly'::text])))
);


--
-- Name: mystery_challenge_clues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mystery_challenge_clues (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    challenge_id uuid NOT NULL,
    reveal_day integer NOT NULL,
    clue_text text NOT NULL,
    revealed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mystery_challenge_clues_reveal_day_check CHECK (((reveal_day >= 1) AND (reveal_day <= 3)))
);


--
-- Name: mystery_challenge_exclusions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mystery_challenge_exclusions (
    product_ean text NOT NULL,
    excluded_until timestamp with time zone NOT NULL
);


--
-- Name: mystery_challenge_finds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mystery_challenge_finds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    challenge_id uuid NOT NULL,
    user_id uuid,
    scan_id uuid NOT NULL,
    rank integer NOT NULL,
    cab_awarded integer NOT NULL,
    found_at timestamp with time zone NOT NULL,
    announced_at timestamp with time zone
);


--
-- Name: mystery_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mystery_challenges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    product_ean text NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    status text DEFAULT 'scheduled'::text NOT NULL,
    reward_tiers jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT mystery_challenges_status_check CHECK ((status = ANY (ARRAY['scheduled'::text, 'active'::text, 'frozen'::text, 'revealed'::text])))
);


--
-- Name: notification_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    type text NOT NULL,
    payload jsonb,
    sent_at timestamp with time zone DEFAULT now() NOT NULL,
    read_at timestamp with time zone,
    status text DEFAULT 'sent'::text NOT NULL,
    expo_ticket_id text,
    CONSTRAINT type_check CHECK ((type = ANY (ARRAY['price_drop'::text, 'streak_reminder'::text, 'weekly_recap'::text, 'challenge_available'::text, 'cashback_credited'::text, 'level_up'::text])))
);


--
-- Name: notification_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    type text NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone
);


--
-- Name: ocr_knowledge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_knowledge (
    id uuid NOT NULL,
    raw_ocr text NOT NULL,
    corrected text,
    match_type text NOT NULL,
    source text NOT NULL,
    confidence double precision,
    seen_count integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    type text NOT NULL,
    entity_id uuid,
    dismissal_category text,
    CONSTRAINT ck_ocr_knowledge_confidence CHECK (((confidence IS NULL) OR ((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))),
    CONSTRAINT ck_ocr_knowledge_dismissal_category CHECK (((dismissal_category IS NULL) OR (dismissal_category = ANY (ARRAY['payment_method'::text, 'total'::text, 'tva_label'::text, 'footer'::text, 'header_meta'::text, 'fidelity'::text, 'other'::text])))),
    CONSTRAINT ck_ocr_knowledge_match_type CHECK ((match_type = ANY (ARRAY['sequence'::text, 'ngram'::text, 'token'::text]))),
    CONSTRAINT ck_ocr_knowledge_source CHECK ((source = ANY (ARRAY['ocr_arbitrage'::text, 'user_correction'::text, 'manual'::text, 'llm'::text]))),
    CONSTRAINT ck_ocr_knowledge_type CHECK ((type = ANY (ARRAY['product_name'::text, 'brand_name'::text, 'retailer_header'::text, 'address_token'::text, 'dismissal'::text])))
);


--
-- Name: optimized_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.optimized_routes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    list_id uuid NOT NULL,
    total_price numeric(10,2) NOT NULL,
    total_savings numeric(10,2) DEFAULT '0'::numeric NOT NULL,
    distance_km numeric(8,2),
    steps jsonb NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    status text DEFAULT 'ready'::text NOT NULL,
    CONSTRAINT ck_optimized_routes_status CHECK ((status = ANY (ARRAY['ready'::text, 'computing'::text, 'updating'::text, 'failed'::text]))),
    CONSTRAINT distance_pos CHECK (((distance_km IS NULL) OR (distance_km >= (0)::numeric))),
    CONSTRAINT expires_after_computed CHECK ((expires_at > computed_at)),
    CONSTRAINT savings_lte_price CHECK ((total_savings <= total_price)),
    CONSTRAINT total_price_pos CHECK ((total_price > (0)::numeric)),
    CONSTRAINT total_savings_pos CHECK ((total_savings >= (0)::numeric))
);


--
-- Name: parsed_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parsed_tickets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    receipt_id uuid,
    parsed_jsonb jsonb NOT NULL,
    parsed_jsonb_hash text NOT NULL,
    raw_ticket_image_hash text NOT NULL,
    ocr_engine_version text NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parsed_ticket_id uuid,
    scan_id uuid,
    phase text NOT NULL,
    level text NOT NULL,
    event text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pipeline_audit_log_level CHECK ((level = ANY (ARRAY['verbose'::text, 'normal'::text, 'production'::text]))),
    CONSTRAINT ck_pipeline_audit_log_phase CHECK ((phase = ANY (ARRAY['extract'::text, 'comprehend'::text, 'match'::text, 'persist'::text, 'manual'::text])))
);


--
-- Name: price_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    product_ean text NOT NULL,
    store_id uuid,
    target_price numeric(10,2) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    triggered_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT target_price_pos CHECK ((target_price > (0)::numeric)),
    CONSTRAINT triggered_check CHECK (((triggered_at IS NULL) OR (active = false)))
);


--
-- Name: price_challenge_responses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_challenge_responses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    challenge_id uuid NOT NULL,
    user_id uuid,
    price numeric(10,2) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT price_pos CHECK ((price > (0)::numeric))
);


--
-- Name: price_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_challenges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scan_id uuid NOT NULL,
    store_id uuid NOT NULL,
    product_ean text,
    image_crop_url text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    validated_price numeric(10,2),
    trust_score numeric(5,2) DEFAULT '0'::numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT status_check CHECK ((status = ANY (ARRAY['pending'::text, 'validated'::text, 'rejected'::text]))),
    CONSTRAINT trust_range CHECK (((trust_score >= (0)::numeric) AND (trust_score <= (100)::numeric))),
    CONSTRAINT validated_coherence CHECK ((((status = 'validated'::text) AND (validated_price IS NOT NULL)) OR ((status <> 'validated'::text) AND (validated_price IS NULL)))),
    CONSTRAINT validated_price_pos CHECK (((validated_price IS NULL) OR (validated_price > (0)::numeric)))
);


--
-- Name: price_consensus; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_consensus (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    store_id uuid NOT NULL,
    product_ean text NOT NULL,
    price integer NOT NULL,
    trust_score numeric(5,2) NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    frozen_until timestamp with time zone,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT price_pos CHECK ((price > 0)),
    CONSTRAINT seen_order CHECK ((first_seen_at <= last_seen_at)),
    CONSTRAINT trust_range CHECK (((trust_score >= (0)::numeric) AND (trust_score <= (100)::numeric)))
);


--
-- Name: price_consensus_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_consensus_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    consensus_id uuid NOT NULL,
    store_id uuid NOT NULL,
    product_ean text NOT NULL,
    price integer NOT NULL,
    trust_score numeric(5,2) NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    frozen_until timestamp with time zone,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT price_pos CHECK ((price > 0)),
    CONSTRAINT seen_order CHECK ((first_seen_at <= last_seen_at)),
    CONSTRAINT trust_range CHECK (((trust_score >= (0)::numeric) AND (trust_score <= (100)::numeric)))
);


--
-- Name: price_consensus_scans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_consensus_scans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    consensus_id uuid NOT NULL,
    scan_id uuid NOT NULL
);


--
-- Name: scans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    store_id uuid,
    product_ean text,
    scanned_name text,
    price integer NOT NULL,
    quantity numeric(10,3) DEFAULT '1'::numeric NOT NULL,
    tva_amount integer,
    scan_type text NOT NULL,
    receipt_id uuid,
    status text DEFAULT 'pending'::text NOT NULL,
    rejected_reason text,
    scanned_at timestamp with time zone DEFAULT now() NOT NULL,
    status_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    image_url text,
    match_method text,
    label_session_id uuid,
    label_r2_key text,
    user_verified_at timestamp with time zone,
    photo_hash character(64),
    label_image_expires_at timestamp with time zone,
    store_status text DEFAULT 'confirmed'::text NOT NULL,
    user_lat numeric(9,6),
    user_lng numeric(9,6),
    match_confidence double precision,
    parsed_ticket_id uuid,
    CONSTRAINT ck_scans_match_confidence_range CHECK (((match_confidence IS NULL) OR ((match_confidence >= (0.0)::double precision) AND (match_confidence <= (1.0)::double precision)))),
    CONSTRAINT ck_scans_match_method_v3 CHECK (((match_method IS NULL) OR (match_method = ANY (ARRAY['barcode'::text, 'knowledge'::text, 'consensus_match'::text, 'fuzzy_strict'::text, 'manual_admin'::text, 'observed_name'::text, 'fuzzy'::text, 'fuzzy_confirmed'::text, 'manual'::text, 'barcode_ean'::text])))),
    CONSTRAINT ck_scans_matched_requires_ean_method CHECK (((status <> 'matched'::text) OR ((product_ean IS NOT NULL) AND (match_method IS NOT NULL)))),
    CONSTRAINT ck_scans_non_matched_requires_reason CHECK (((status <> ALL (ARRAY['unresolved'::text, 'rejected'::text])) OR (rejected_reason IS NOT NULL))),
    CONSTRAINT ck_scans_store_status CHECK ((store_status = ANY (ARRAY['confirmed'::text, 'pending'::text, 'unknown'::text]))),
    CONSTRAINT ck_scans_store_status_consistency CHECK ((((store_status = 'unknown'::text) AND (store_id IS NULL)) OR ((store_status <> 'unknown'::text) AND (store_id IS NOT NULL)))),
    CONSTRAINT manual_no_scanned_name CHECK (((scan_type <> 'manual'::text) OR ((product_ean IS NOT NULL) AND (scanned_name IS NULL)))),
    CONSTRAINT price_pos CHECK ((price >= 0)),
    CONSTRAINT quantity_pos CHECK ((quantity > (0)::numeric)),
    CONSTRAINT receipt_required CHECK ((((scan_type = 'receipt'::text) AND (receipt_id IS NOT NULL)) OR ((scan_type <> 'receipt'::text) AND (receipt_id IS NULL)))),
    CONSTRAINT scan_type_check CHECK ((scan_type = ANY (ARRAY['receipt'::text, 'electronic_label'::text, 'manual'::text]))),
    CONSTRAINT scans_status_check_v3 CHECK ((status = ANY (ARRAY['pending'::text, 'matched'::text, 'unresolved'::text, 'rejected'::text, 'accepted'::text, 'unmatched'::text, 'failed'::text]))),
    CONSTRAINT tva_pos CHECK (((tva_amount IS NULL) OR (tva_amount >= 0))),
    CONSTRAINT tva_receipt_only CHECK (((tva_amount IS NULL) OR (scan_type = 'receipt'::text)))
);


--
-- Name: price_history; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.price_history AS
 SELECT id AS observation_id,
    store_id,
    product_ean,
    price,
    quantity,
    scan_type,
    scanned_name,
    scanned_at AS recorded_at
   FROM public.scans
  WHERE (status = 'accepted'::text);


--
-- Name: product_favorites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_favorites (
    user_id uuid NOT NULL,
    product_ean text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: product_name_resolutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_name_resolutions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scan_id uuid NOT NULL,
    store_id uuid NOT NULL,
    normalized_label text NOT NULL,
    product_ean text NOT NULL,
    user_id uuid NOT NULL,
    match_method text NOT NULL,
    resolved_at timestamp with time zone DEFAULT now() NOT NULL,
    weight_override integer,
    source_type text DEFAULT 'receipt'::text NOT NULL,
    retailer_id uuid,
    CONSTRAINT pnr_match_method_check CHECK ((match_method = ANY (ARRAY['barcode'::text, 'manual_admin'::text, 'fuzzy_pending'::text, 'observed_name'::text, 'esl'::text, 'cross_source_esl_exact'::text]))),
    CONSTRAINT pnr_source_type_check CHECK ((source_type = ANY (ARRAY['receipt'::text, 'esl'::text])))
);


--
-- Name: product_observed_names; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.product_observed_names AS
 SELECT store_id,
    product_ean,
    scanned_name,
    count(*) AS frequency
   FROM public.scans s
  WHERE ((status = 'accepted'::text) AND (product_ean IS NOT NULL) AND (scanned_name IS NOT NULL))
  GROUP BY store_id, product_ean, scanned_name;


--
-- Name: VIEW product_observed_names; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.product_observed_names IS 'DEPRECATED — use product_name_resolutions (NRC bloc A+). Read-only after 2026-05-02 ; physical drop scheduled for V2 post-bêta.';


--
-- Name: product_tracking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_tracking (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    product_ean text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deactivated_at timestamp with time zone,
    avg_quantity numeric(10,3),
    avg_frequency_days integer,
    CONSTRAINT avg_frequency_pos CHECK (((avg_frequency_days IS NULL) OR (avg_frequency_days > 0))),
    CONSTRAINT avg_quantity_pos CHECK (((avg_quantity IS NULL) OR (avg_quantity > (0)::numeric))),
    CONSTRAINT deactivated_check CHECK ((((active = false) AND (deactivated_at IS NOT NULL)) OR ((active = true) AND (deactivated_at IS NULL))))
);


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    ean text NOT NULL,
    name text NOT NULL,
    photo_url text,
    category_id uuid,
    source text DEFAULT 'off'::text NOT NULL,
    unit text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    product_quantity numeric,
    product_quantity_unit text,
    quantity_raw text,
    storage_type text,
    allergens_tags text[],
    ingredients_tags text[],
    brands text,
    photo_url_small text,
    labels_tags text[],
    categories_tags text[],
    brand_id uuid,
    name_normalized text GENERATED ALWAYS AS (upper(public.immutable_unaccent(name))) STORED,
    product_name_fr text,
    generic_name_fr text,
    brands_text text,
    quantity_text text,
    CONSTRAINT ck_products_storage_type CHECK ((storage_type = ANY (ARRAY['frozen'::text, 'fresh'::text, 'ambient'::text, 'unmatched'::text]))),
    CONSTRAINT ean_format CHECK ((ean ~ '^\d{8,14}$'::text)),
    CONSTRAINT internal_ean_prefix CHECK (((source <> 'internal'::text) OR (ean ~~ '2%'::text))),
    CONSTRAINT internal_has_unit CHECK (((source <> 'internal'::text) OR (unit IS NOT NULL))),
    CONSTRAINT name_not_empty CHECK ((name <> ''::text)),
    CONSTRAINT off_no_unit CHECK (((source <> 'off'::text) OR (unit IS NULL))),
    CONSTRAINT source_check CHECK ((source = ANY (ARRAY['off'::text, 'internal'::text]))),
    CONSTRAINT unit_check CHECK (((unit = ANY (ARRAY['kg'::text, 'l'::text, 'unit'::text])) OR (unit IS NULL)))
);


--
-- Name: receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.receipts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    store_id uuid,
    purchased_at date NOT NULL,
    tva_total integer,
    total_amount integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    image_r2_key text,
    image_uploaded_at timestamp with time zone,
    image_deleted_at timestamp with time zone,
    total_lines_detected integer,
    photo_hash character(64),
    purchased_at_with_time timestamp(0) without time zone,
    receipt_barcode text,
    barcode_fields jsonb,
    store_status text DEFAULT 'confirmed'::text NOT NULL,
    pending_items jsonb,
    user_store_hint text,
    parsed_ticket_id uuid,
    CONSTRAINT ck_receipts_store_status CHECK ((store_status = ANY (ARRAY['confirmed'::text, 'pending'::text, 'unknown'::text]))),
    CONSTRAINT purchased_not_future CHECK ((purchased_at <= CURRENT_DATE)),
    CONSTRAINT total_amount_pos CHECK (((total_amount IS NULL) OR (total_amount > 0))),
    CONSTRAINT tva_pos CHECK (((tva_total IS NULL) OR (tva_total >= 0)))
);


--
-- Name: referral_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.referral_codes (
    id uuid NOT NULL,
    user_id uuid,
    code text NOT NULL,
    type text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT referral_codes_code_upper_check CHECK ((code = upper(code))),
    CONSTRAINT referral_codes_type_check CHECK ((type = ANY (ARRAY['user'::text, 'influencer'::text])))
);


--
-- Name: referral_uses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.referral_uses (
    id uuid NOT NULL,
    referral_id uuid NOT NULL,
    referred_user_id uuid,
    plan text,
    rewarded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT referral_uses_plan_check CHECK ((plan = ANY (ARRAY['monthly'::text, 'annual'::text])))
);


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    id uuid NOT NULL,
    jti text NOT NULL,
    user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: retailer_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retailer_aliases (
    retailer_id uuid NOT NULL,
    alias text NOT NULL,
    source text NOT NULL,
    CONSTRAINT ck_retailer_aliases_source CHECK ((source = ANY (ARRAY['osm'::text, 'receipt_header'::text, 'manual'::text])))
);


--
-- Name: retailer_receipt_formats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retailer_receipt_formats (
    retailer_key text NOT NULL,
    length integer NOT NULL,
    fields jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: retailers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retailers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_name text NOT NULL,
    slug text NOT NULL,
    parent_id uuid,
    logo_url text,
    color_hex text,
    website text,
    country_code character(2) DEFAULT 'FR'::bpchar NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_retailers_color_hex CHECK (((color_hex IS NULL) OR (color_hex ~ '^#[0-9A-Fa-f]{6}$'::text)))
);


--
-- Name: reward_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reward_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_type text NOT NULL,
    base_amount integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT action_type_check CHECK ((action_type = ANY (ARRAY['DAILY_LOGIN'::text, 'SCAN_RECEIPT'::text, 'VIDEO_SCAN'::text, 'PRICE_CHALLENGE'::text]))),
    CONSTRAINT base_amount_pos CHECK ((base_amount > 0))
);


--
-- Name: reward_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reward_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    action_type text NOT NULL,
    qualifier text,
    quantity integer NOT NULL,
    idempotency_key text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    payload jsonb,
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reward_events_quantity_positive CHECK ((quantity > 0)),
    CONSTRAINT reward_events_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'processed'::text, 'duplicate'::text, 'failed'::text])))
);


--
-- Name: scan_debug; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scan_debug (
    scan_id uuid,
    rich_blocks jsonb,
    llm_output jsonb,
    final_receipt_data jsonb,
    ocr_passes_summary jsonb,
    processed_image_r2_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    purge_after timestamp with time zone NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    receipt_id uuid NOT NULL,
    processed_images_r2_keys jsonb,
    legacy_parser_output jsonb
);


--
-- Name: shopping_list_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shopping_list_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    list_id uuid NOT NULL,
    product_ean text NOT NULL,
    quantity numeric(10,3) DEFAULT '1'::numeric NOT NULL,
    checked boolean DEFAULT false NOT NULL,
    checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT checked_at_check CHECK ((((checked = true) AND (checked_at IS NOT NULL)) OR ((checked = false) AND (checked_at IS NULL)))),
    CONSTRAINT quantity_pos CHECK ((quantity > (0)::numeric))
);


--
-- Name: shopping_lists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shopping_lists (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    has_default_name boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_template boolean DEFAULT false NOT NULL
);


--
-- Name: sirene_geocode_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sirene_geocode_cache (
    siret character(14) NOT NULL,
    address_hash text NOT NULL,
    lat numeric(9,6),
    lng numeric(9,6),
    score numeric(3,2),
    geocoded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: store_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.store_candidates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    raw_header text NOT NULL,
    retailer_guess text,
    address_guess text,
    postal_code text,
    phone text,
    occurrence_count integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    matched_store_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    receipt_id uuid,
    CONSTRAINT ck_store_candidates_status CHECK ((status = ANY (ARRAY['pending'::text, 'matched'::text, 'ignored'::text])))
);


--
-- Name: store_fingerprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.store_fingerprints (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    store_id uuid NOT NULL,
    signal_type text NOT NULL,
    signal_value text NOT NULL,
    confirmed_count integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_store_fingerprints_signal_type CHECK ((signal_type = ANY (ARRAY['phone'::text, 'store_code'::text, 'barcode_prefix'::text, 'retailer_postal'::text, 'retailer_postal_num'::text])))
);


--
-- Name: store_validation_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.store_validation_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    store_id uuid NOT NULL,
    from_status text,
    to_status text NOT NULL,
    reason text NOT NULL,
    triggered_by text NOT NULL,
    meta jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    retailer text,
    address text,
    city text,
    postal_code text,
    lat numeric(9,6) NOT NULL,
    lng numeric(9,6) NOT NULL,
    is_disabled boolean DEFAULT false NOT NULL,
    disabled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    phone text,
    siret character(14),
    osm_id bigint,
    store_code text,
    opening_hours text,
    source text DEFAULT 'osm'::text NOT NULL,
    retailer_id uuid,
    validation_status text DEFAULT 'confirmed'::text NOT NULL,
    suggested_by_user_id uuid,
    name_normalized text GENERATED ALWAYS AS (upper(public.immutable_unaccent(name))) STORED,
    CONSTRAINT address_not_empty CHECK (((address IS NULL) OR (address <> ''::text))),
    CONSTRAINT city_not_empty CHECK (((city IS NULL) OR (city <> ''::text))),
    CONSTRAINT ck_stores_source CHECK ((source = ANY (ARRAY['osm'::text, 'sirene'::text, 'overture'::text, 'admin'::text, 'user_suggested'::text]))),
    CONSTRAINT ck_stores_validation_status CHECK ((validation_status = ANY (ARRAY['pending'::text, 'confirmed'::text, 'suspicious'::text]))),
    CONSTRAINT disabled_at_check CHECK ((((is_disabled = true) AND (disabled_at IS NOT NULL)) OR ((is_disabled = false) AND (disabled_at IS NULL)))),
    CONSTRAINT lat_range CHECK (((lat >= ('-90'::integer)::numeric) AND (lat <= (90)::numeric))),
    CONSTRAINT lng_range CHECK (((lng >= ('-180'::integer)::numeric) AND (lng <= (180)::numeric))),
    CONSTRAINT name_not_empty CHECK ((name <> ''::text)),
    CONSTRAINT postal_not_empty CHECK (((postal_code IS NULL) OR (postal_code <> ''::text))),
    CONSTRAINT retailer_not_empty CHECK (((retailer IS NULL) OR (retailer <> ''::text)))
);


--
-- Name: streak_tiers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.streak_tiers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    days integer NOT NULL,
    multiplier numeric(4,2) NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT days_pos CHECK ((days > 0)),
    CONSTRAINT label_not_empty CHECK ((label <> ''::text)),
    CONSTRAINT multiplier_gt_1 CHECK ((multiplier > (1)::numeric))
);


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    price numeric(10,2) DEFAULT 11.99 NOT NULL,
    paid_with text DEFAULT 'stripe'::text NOT NULL,
    discount_campaign_code text,
    discount_amount numeric(10,2),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    cancelled_at timestamp with time zone,
    payment_ref text,
    plan text,
    stripe_session_id text,
    CONSTRAINT cancelled_check CHECK ((((status = 'cancelled'::text) AND (cancelled_at IS NOT NULL)) OR ((status <> 'cancelled'::text) AND (cancelled_at IS NULL)))),
    CONSTRAINT discount_amount_pos CHECK (((discount_amount IS NULL) OR (discount_amount > (0)::numeric))),
    CONSTRAINT discount_coherence CHECK ((((discount_campaign_code IS NOT NULL) AND (discount_amount IS NOT NULL)) OR ((discount_campaign_code IS NULL) AND (discount_amount IS NULL)))),
    CONSTRAINT discount_not_exceed_price CHECK (((discount_amount IS NULL) OR (discount_amount < price))),
    CONSTRAINT expires_after_start CHECK ((expires_at > started_at)),
    CONSTRAINT payment_ref_coherence CHECK (((paid_with = 'cashback'::text) OR (payment_ref IS NOT NULL) OR (status <> ALL (ARRAY['active'::text, 'expired'::text])))),
    CONSTRAINT price_pos CHECK ((price > (0)::numeric)),
    CONSTRAINT subscriptions_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'active'::text, 'cancelled'::text, 'expired'::text])))
);


--
-- Name: unknown_scans_weekly_aggregate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unknown_scans_weekly_aggregate (
    year_week text NOT NULL,
    scan_count integer DEFAULT 0 NOT NULL,
    count_per_scan_type jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_achievements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_achievements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    achievement_id uuid NOT NULL,
    unlocked_at timestamp with time zone DEFAULT now() NOT NULL,
    cab_granted integer NOT NULL,
    trigger_event jsonb
);


--
-- Name: user_badges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_badges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    badge_id uuid NOT NULL,
    unlocked_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_battlepass_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_battlepass_claims (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    milestone_id uuid NOT NULL,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_battlepass_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_battlepass_progress (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    season_id uuid NOT NULL,
    cab_earned_season integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_cab_balance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_cab_balance (
    user_id uuid NOT NULL,
    balance integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT balance_nn CHECK ((balance >= 0))
);


--
-- Name: user_cashback_balance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_cashback_balance (
    user_id uuid NOT NULL,
    balance integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT balance_nn CHECK (((balance)::numeric >= (0)::numeric)),
    CONSTRAINT user_cashback_balance_balance_check CHECK ((balance >= 0))
);


--
-- Name: user_missions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_missions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    mission_id uuid NOT NULL,
    period_start date NOT NULL,
    current_count integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    buffer_count integer DEFAULT 0 NOT NULL,
    cab_reward integer DEFAULT 0 NOT NULL,
    xp_reward numeric DEFAULT '0'::numeric NOT NULL,
    frozen_until timestamp with time zone,
    freeze_count integer DEFAULT 0 NOT NULL,
    target_count integer NOT NULL,
    tracked_values jsonb,
    burst_count integer DEFAULT 0 NOT NULL,
    period_extended_until timestamp with time zone,
    burst_locked boolean DEFAULT false NOT NULL,
    portions_claimed integer DEFAULT 0 NOT NULL,
    CONSTRAINT user_missions_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'completed'::text, 'claimed'::text])))
);


--
-- Name: user_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_preferences (
    user_id uuid NOT NULL,
    search_radius_km integer DEFAULT 5 NOT NULL,
    transport_mode text DEFAULT 'driving'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT radius_range CHECK (((search_radius_km > 0) AND (search_radius_km <= 50))),
    CONSTRAINT transport_check CHECK ((transport_mode = ANY (ARRAY['driving'::text, 'walking'::text, 'cycling'::text])))
);


--
-- Name: user_push_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_push_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token text NOT NULL,
    platform text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT platform_check CHECK ((platform = ANY (ARRAY['ios'::text, 'android'::text, 'web'::text])))
);


--
-- Name: user_savings_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_savings_snapshot (
    user_id uuid NOT NULL,
    lifetime_savings_cents bigint DEFAULT 0 NOT NULL,
    rings_consumed bigint DEFAULT 0 NOT NULL,
    last_computed_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_user_savings_snapshot_lifetime_nonneg CHECK ((lifetime_savings_cents >= 0)),
    CONSTRAINT ck_user_savings_snapshot_rings_nonneg CHECK ((rings_consumed >= 0))
);


--
-- Name: user_session_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_session_stats (
    user_id uuid NOT NULL,
    period_year integer NOT NULL,
    period_month integer NOT NULL,
    ios_count integer DEFAULT 0 NOT NULL,
    android_count integer DEFAULT 0 NOT NULL,
    web_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT android_nn CHECK ((android_count >= 0)),
    CONSTRAINT ios_nn CHECK ((ios_count >= 0)),
    CONSTRAINT month_range CHECK (((period_month >= 1) AND (period_month <= 12))),
    CONSTRAINT web_nn CHECK ((web_count >= 0)),
    CONSTRAINT year_range CHECK (((period_year >= 2024) AND (period_year <= 2100)))
);


--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    platform text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT platform_check CHECK ((platform = ANY (ARRAY['ios'::text, 'android'::text, 'web'::text])))
);


--
-- Name: user_store_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_store_preferences (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    store_id uuid NOT NULL,
    preference text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT preference_check CHECK ((preference = ANY (ARRAY['favourite'::text, 'excluded'::text])))
);


--
-- Name: user_streaks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_streaks (
    user_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    current_streak_days integer DEFAULT 0 NOT NULL,
    last_fed_at date,
    food_reserves integer DEFAULT 0 NOT NULL,
    timezone text DEFAULT 'Europe/Paris'::text NOT NULL,
    CONSTRAINT user_streaks_food_reserves_nn CHECK ((food_reserves >= 0)),
    CONSTRAINT user_streaks_streak_days_nn CHECK ((current_streak_days >= 0))
);


--
-- Name: user_xp_balance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_xp_balance (
    user_id uuid NOT NULL,
    balance numeric DEFAULT '0'::numeric NOT NULL,
    level integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_xp_balance_positive CHECK ((balance >= (0)::numeric))
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    provider text DEFAULT 'email'::text NOT NULL,
    provider_id text,
    password_hash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    display_name text,
    avatar_url text,
    current_level_id uuid,
    is_deleted boolean DEFAULT false NOT NULL,
    timezone text DEFAULT 'Europe/Paris'::text NOT NULL,
    password_changed_at timestamp with time zone,
    ref_lat numeric(9,3),
    ref_lng numeric(9,3),
    support_id text NOT NULL,
    trust_score integer DEFAULT 50 NOT NULL,
    total_resolved_scans integer DEFAULT 0 NOT NULL,
    is_shadow_banned boolean DEFAULT false NOT NULL,
    trust_score_updated_at timestamp with time zone,
    gift_card_redeemed_ytd_cents integer NOT NULL,
    CONSTRAINT auth_coherence CHECK ((((provider = 'email'::text) AND (password_hash IS NOT NULL) AND (provider_id IS NULL)) OR ((provider = ANY (ARRAY['google'::text, 'apple'::text])) AND (provider_id IS NOT NULL) AND (password_hash IS NULL)) OR ((provider = 'internal'::text) AND (provider_id IS NULL) AND (password_hash IS NULL)))),
    CONSTRAINT email_format CHECK ((email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'::text)),
    CONSTRAINT provider_check CHECK ((provider = ANY (ARRAY['google'::text, 'apple'::text, 'email'::text, 'internal'::text]))),
    CONSTRAINT users_trust_score_range_chk CHECK (((trust_score >= 0) AND (trust_score <= 100)))
);


--
-- Name: xp_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.xp_transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    amount numeric NOT NULL,
    reason text NOT NULL,
    reference_id uuid,
    reference_type text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT xp_amount_positive CHECK ((amount > (0)::numeric)),
    CONSTRAINT xp_reason_check CHECK ((reason = ANY (ARRAY['receipt_scan'::text, 'label_scan'::text, 'barcode_scan'::text, 'product_identification'::text, 'fill_product_field'::text, 'scan_distinct'::text, 'promo_found'::text, 'price_compared'::text, 'mission_completed'::text, 'battlepass_milestone'::text, 'referral'::text, 'feed_jack'::text, 'stonks_completion'::text, 'challenge_milestone'::text, 'mission_burst'::text])))
);


--
-- Name: batch_sync_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_sync_log ALTER COLUMN id SET DEFAULT nextval('public.batch_sync_log_id_seq'::regclass);


--
-- Name: achievements achievements_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements
    ADD CONSTRAINT achievements_code_key UNIQUE (code);


--
-- Name: achievements achievements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements
    ADD CONSTRAINT achievements_pkey PRIMARY KEY (id);


--
-- Name: admin_settings_audit admin_settings_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_settings_audit
    ADD CONSTRAINT admin_settings_audit_pkey PRIMARY KEY (id);


--
-- Name: affiliate_offers affiliate_offers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliate_offers
    ADD CONSTRAINT affiliate_offers_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (section);


--
-- Name: badges badges_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT badges_code_key UNIQUE (code);


--
-- Name: badges badges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT badges_pkey PRIMARY KEY (id);


--
-- Name: batch_sync_log batch_sync_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_sync_log
    ADD CONSTRAINT batch_sync_log_pkey PRIMARY KEY (id);


--
-- Name: battlepass_milestones battlepass_milestones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.battlepass_milestones
    ADD CONSTRAINT battlepass_milestones_pkey PRIMARY KEY (id);


--
-- Name: battlepass_seasons battlepass_seasons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.battlepass_seasons
    ADD CONSTRAINT battlepass_seasons_pkey PRIMARY KEY (id);


--
-- Name: battlepass_seasons battlepass_seasons_season_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.battlepass_seasons
    ADD CONSTRAINT battlepass_seasons_season_number_key UNIQUE (season_number);


--
-- Name: brands brands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands
    ADD CONSTRAINT brands_pkey PRIMARY KEY (id);


--
-- Name: cabecoin_transactions cabecoin_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cabecoin_transactions
    ADD CONSTRAINT cabecoin_transactions_pkey PRIMARY KEY (id);


--
-- Name: cashback_transactions cashback_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT cashback_transactions_pkey PRIMARY KEY (id);


--
-- Name: cashback_withdrawals cashback_withdrawals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_withdrawals
    ADD CONSTRAINT cashback_withdrawals_pkey PRIMARY KEY (id);


--
-- Name: categories categories_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_name_key UNIQUE (name);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);


--
-- Name: community_challenge_claims community_challenge_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_claims
    ADD CONSTRAINT community_challenge_claims_pkey PRIMARY KEY (id);


--
-- Name: community_challenge_milestones community_challenge_milestones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_milestones
    ADD CONSTRAINT community_challenge_milestones_pkey PRIMARY KEY (id);


--
-- Name: community_challenge_progress community_challenge_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_progress
    ADD CONSTRAINT community_challenge_progress_pkey PRIMARY KEY (challenge_id);


--
-- Name: community_challenges community_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenges
    ADD CONSTRAINT community_challenges_pkey PRIMARY KEY (id);


--
-- Name: community_multipliers community_multipliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_multipliers
    ADD CONSTRAINT community_multipliers_pkey PRIMARY KEY (id);


--
-- Name: discount_campaigns discount_campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discount_campaigns
    ADD CONSTRAINT discount_campaigns_pkey PRIMARY KEY (code);


--
-- Name: affiliate_offers external_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliate_offers
    ADD CONSTRAINT external_unique UNIQUE (provider, external_id);


--
-- Name: gift_card_brands gift_card_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_brands
    ADD CONSTRAINT gift_card_brands_pkey PRIMARY KEY (id);


--
-- Name: gift_card_orders gift_card_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_orders
    ADD CONSTRAINT gift_card_orders_pkey PRIMARY KEY (id);


--
-- Name: label_sessions label_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_sessions
    ADD CONSTRAINT label_sessions_pkey PRIMARY KEY (id);


--
-- Name: leaderboard_snapshots leaderboard_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leaderboard_snapshots
    ADD CONSTRAINT leaderboard_snapshots_pkey PRIMARY KEY (id);


--
-- Name: leaderboard_snapshots leaderboard_snapshots_user_id_period_year_period_month_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leaderboard_snapshots
    ADD CONSTRAINT leaderboard_snapshots_user_id_period_year_period_month_key UNIQUE (user_id, period_year, period_month);


--
-- Name: level_tiers level_tiers_cab_threshold_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.level_tiers
    ADD CONSTRAINT level_tiers_cab_threshold_key UNIQUE (cab_threshold);


--
-- Name: level_tiers level_tiers_level_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.level_tiers
    ADD CONSTRAINT level_tiers_level_key UNIQUE (level);


--
-- Name: level_tiers level_tiers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.level_tiers
    ADD CONSTRAINT level_tiers_pkey PRIMARY KEY (id);


--
-- Name: mission_xp_records mission_xp_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_xp_records
    ADD CONSTRAINT mission_xp_records_pkey PRIMARY KEY (id);


--
-- Name: missions missions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT missions_pkey PRIMARY KEY (id);


--
-- Name: mystery_challenge_clues mystery_challenge_clues_challenge_id_reveal_day_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_clues
    ADD CONSTRAINT mystery_challenge_clues_challenge_id_reveal_day_key UNIQUE (challenge_id, reveal_day);


--
-- Name: mystery_challenge_clues mystery_challenge_clues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_clues
    ADD CONSTRAINT mystery_challenge_clues_pkey PRIMARY KEY (id);


--
-- Name: mystery_challenge_exclusions mystery_challenge_exclusions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_exclusions
    ADD CONSTRAINT mystery_challenge_exclusions_pkey PRIMARY KEY (product_ean);


--
-- Name: mystery_challenge_finds mystery_challenge_finds_challenge_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_finds
    ADD CONSTRAINT mystery_challenge_finds_challenge_id_user_id_key UNIQUE (challenge_id, user_id);


--
-- Name: mystery_challenge_finds mystery_challenge_finds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_finds
    ADD CONSTRAINT mystery_challenge_finds_pkey PRIMARY KEY (id);


--
-- Name: mystery_challenges mystery_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenges
    ADD CONSTRAINT mystery_challenges_pkey PRIMARY KEY (id);


--
-- Name: notification_logs notification_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs
    ADD CONSTRAINT notification_logs_pkey PRIMARY KEY (id);


--
-- Name: notification_outbox notification_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT notification_outbox_pkey PRIMARY KEY (id);


--
-- Name: optimized_routes optimized_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.optimized_routes
    ADD CONSTRAINT optimized_routes_pkey PRIMARY KEY (id);


--
-- Name: parsed_tickets parsed_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_tickets
    ADD CONSTRAINT parsed_tickets_pkey PRIMARY KEY (id);


--
-- Name: pipeline_audit_log pipeline_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_audit_log
    ADD CONSTRAINT pipeline_audit_log_pkey PRIMARY KEY (id);


--
-- Name: cities pk_cities; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cities
    ADD CONSTRAINT pk_cities PRIMARY KEY (postal_code, city_name);


--
-- Name: product_favorites pk_product_favorites; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_favorites
    ADD CONSTRAINT pk_product_favorites PRIMARY KEY (user_id, product_ean);


--
-- Name: price_alerts price_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_alerts
    ADD CONSTRAINT price_alerts_pkey PRIMARY KEY (id);


--
-- Name: price_alerts price_alerts_user_id_product_ean_store_id_target_price_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_alerts
    ADD CONSTRAINT price_alerts_user_id_product_ean_store_id_target_price_key UNIQUE (user_id, product_ean, store_id, target_price);


--
-- Name: price_challenge_responses price_challenge_responses_challenge_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenge_responses
    ADD CONSTRAINT price_challenge_responses_challenge_id_user_id_key UNIQUE (challenge_id, user_id);


--
-- Name: price_challenge_responses price_challenge_responses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenge_responses
    ADD CONSTRAINT price_challenge_responses_pkey PRIMARY KEY (id);


--
-- Name: price_challenges price_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenges
    ADD CONSTRAINT price_challenges_pkey PRIMARY KEY (id);


--
-- Name: price_challenges price_challenges_scan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenges
    ADD CONSTRAINT price_challenges_scan_id_key UNIQUE (scan_id);


--
-- Name: price_consensus_history price_consensus_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_history
    ADD CONSTRAINT price_consensus_history_pkey PRIMARY KEY (id);


--
-- Name: price_consensus price_consensus_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus
    ADD CONSTRAINT price_consensus_pkey PRIMARY KEY (id);


--
-- Name: price_consensus_scans price_consensus_scans_consensus_id_scan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_scans
    ADD CONSTRAINT price_consensus_scans_consensus_id_scan_id_key UNIQUE (consensus_id, scan_id);


--
-- Name: price_consensus_scans price_consensus_scans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_scans
    ADD CONSTRAINT price_consensus_scans_pkey PRIMARY KEY (id);


--
-- Name: price_consensus price_consensus_store_id_product_ean_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus
    ADD CONSTRAINT price_consensus_store_id_product_ean_key UNIQUE (store_id, product_ean);


--
-- Name: ocr_knowledge product_knowledge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_knowledge
    ADD CONSTRAINT product_knowledge_pkey PRIMARY KEY (id);


--
-- Name: product_name_resolutions product_name_resolutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_name_resolutions
    ADD CONSTRAINT product_name_resolutions_pkey PRIMARY KEY (id);


--
-- Name: product_tracking product_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_tracking
    ADD CONSTRAINT product_tracking_pkey PRIMARY KEY (id);


--
-- Name: product_tracking product_tracking_user_id_product_ean_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_tracking
    ADD CONSTRAINT product_tracking_user_id_product_ean_key UNIQUE (user_id, product_ean);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (ean);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: referral_codes referral_codes_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_codes
    ADD CONSTRAINT referral_codes_code_key UNIQUE (code);


--
-- Name: referral_codes referral_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_codes
    ADD CONSTRAINT referral_codes_pkey PRIMARY KEY (id);


--
-- Name: referral_codes referral_codes_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_codes
    ADD CONSTRAINT referral_codes_user_id_key UNIQUE (user_id);


--
-- Name: referral_uses referral_uses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_uses
    ADD CONSTRAINT referral_uses_pkey PRIMARY KEY (id);


--
-- Name: referral_uses referral_uses_referred_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_uses
    ADD CONSTRAINT referral_uses_referred_user_id_key UNIQUE (referred_user_id);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: retailer_aliases retailer_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailer_aliases
    ADD CONSTRAINT retailer_aliases_pkey PRIMARY KEY (retailer_id, alias);


--
-- Name: retailer_receipt_formats retailer_receipt_formats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailer_receipt_formats
    ADD CONSTRAINT retailer_receipt_formats_pkey PRIMARY KEY (retailer_key);


--
-- Name: retailers retailers_canonical_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailers
    ADD CONSTRAINT retailers_canonical_name_key UNIQUE (canonical_name);


--
-- Name: retailers retailers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailers
    ADD CONSTRAINT retailers_pkey PRIMARY KEY (id);


--
-- Name: retailers retailers_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailers
    ADD CONSTRAINT retailers_slug_key UNIQUE (slug);


--
-- Name: reward_config reward_config_action_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reward_config
    ADD CONSTRAINT reward_config_action_type_key UNIQUE (action_type);


--
-- Name: reward_config reward_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reward_config
    ADD CONSTRAINT reward_config_pkey PRIMARY KEY (id);


--
-- Name: reward_events reward_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reward_events
    ADD CONSTRAINT reward_events_pkey PRIMARY KEY (id);


--
-- Name: scan_debug scan_debug_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_debug
    ADD CONSTRAINT scan_debug_pkey PRIMARY KEY (id);


--
-- Name: scans scans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT scans_pkey PRIMARY KEY (id);


--
-- Name: scans scans_user_id_store_id_product_ean_scanned_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT scans_user_id_store_id_product_ean_scanned_at_key UNIQUE (user_id, store_id, product_ean, scanned_at);


--
-- Name: shopping_list_items shopping_list_items_list_id_product_ean_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_list_items
    ADD CONSTRAINT shopping_list_items_list_id_product_ean_key UNIQUE (list_id, product_ean);


--
-- Name: shopping_list_items shopping_list_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_list_items
    ADD CONSTRAINT shopping_list_items_pkey PRIMARY KEY (id);


--
-- Name: shopping_lists shopping_lists_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_lists
    ADD CONSTRAINT shopping_lists_pkey PRIMARY KEY (id);


--
-- Name: sirene_geocode_cache sirene_geocode_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sirene_geocode_cache
    ADD CONSTRAINT sirene_geocode_cache_pkey PRIMARY KEY (siret);


--
-- Name: store_candidates store_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_candidates
    ADD CONSTRAINT store_candidates_pkey PRIMARY KEY (id);


--
-- Name: store_fingerprints store_fingerprints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_fingerprints
    ADD CONSTRAINT store_fingerprints_pkey PRIMARY KEY (id);


--
-- Name: store_validation_history store_validation_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_validation_history
    ADD CONSTRAINT store_validation_history_pkey PRIMARY KEY (id);


--
-- Name: stores stores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT stores_pkey PRIMARY KEY (id);


--
-- Name: streak_tiers streak_tiers_days_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.streak_tiers
    ADD CONSTRAINT streak_tiers_days_key UNIQUE (days);


--
-- Name: streak_tiers streak_tiers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.streak_tiers
    ADD CONSTRAINT streak_tiers_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: unknown_scans_weekly_aggregate unknown_scans_weekly_aggregate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unknown_scans_weekly_aggregate
    ADD CONSTRAINT unknown_scans_weekly_aggregate_pkey PRIMARY KEY (year_week);


--
-- Name: brands uq_brands_slug; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands
    ADD CONSTRAINT uq_brands_slug UNIQUE (slug);


--
-- Name: community_challenge_claims uq_challenge_claims_milestone_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_claims
    ADD CONSTRAINT uq_challenge_claims_milestone_user UNIQUE (milestone_id, user_id);


--
-- Name: community_multipliers uq_community_multipliers_challenge_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_multipliers
    ADD CONSTRAINT uq_community_multipliers_challenge_user UNIQUE (challenge_id, user_id);


--
-- Name: gift_card_brands uq_gift_card_brands_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_brands
    ADD CONSTRAINT uq_gift_card_brands_name UNIQUE (name);


--
-- Name: gift_card_orders uq_gift_card_orders_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_orders
    ADD CONSTRAINT uq_gift_card_orders_idempotency UNIQUE (source_type, source_ref_id);


--
-- Name: missions uq_mission; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missions
    ADD CONSTRAINT uq_mission UNIQUE NULLS NOT DISTINCT (action_type, qualifier, frequency, difficulty);


--
-- Name: mission_xp_records uq_mxr_user_mission; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_xp_records
    ADD CONSTRAINT uq_mxr_user_mission UNIQUE (user_mission_id);


--
-- Name: ocr_knowledge uq_ocr_knowledge_raw_ocr_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_knowledge
    ADD CONSTRAINT uq_ocr_knowledge_raw_ocr_type UNIQUE (raw_ocr, type);


--
-- Name: parsed_tickets uq_parsed_tickets_jsonb_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_tickets
    ADD CONSTRAINT uq_parsed_tickets_jsonb_hash UNIQUE (parsed_jsonb_hash);


--
-- Name: reward_events uq_reward_events_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reward_events
    ADD CONSTRAINT uq_reward_events_idempotency_key UNIQUE (idempotency_key);


--
-- Name: battlepass_milestones uq_season_milestone; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.battlepass_milestones
    ADD CONSTRAINT uq_season_milestone UNIQUE (season_id, milestone_number);


--
-- Name: store_fingerprints uq_store_fingerprints_signal; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_fingerprints
    ADD CONSTRAINT uq_store_fingerprints_signal UNIQUE (signal_type, signal_value);


--
-- Name: user_achievements uq_user_achievements_pair; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT uq_user_achievements_pair UNIQUE (user_id, achievement_id);


--
-- Name: user_battlepass_claims uq_user_milestone; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_claims
    ADD CONSTRAINT uq_user_milestone UNIQUE (user_id, milestone_id);


--
-- Name: user_missions uq_user_mission_period; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_missions
    ADD CONSTRAINT uq_user_mission_period UNIQUE (user_id, mission_id, period_start);


--
-- Name: user_battlepass_progress uq_user_season; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_progress
    ADD CONSTRAINT uq_user_season UNIQUE (user_id, season_id);


--
-- Name: user_achievements user_achievements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_pkey PRIMARY KEY (id);


--
-- Name: user_badges user_badges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badges
    ADD CONSTRAINT user_badges_pkey PRIMARY KEY (id);


--
-- Name: user_badges user_badges_user_id_badge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badges
    ADD CONSTRAINT user_badges_user_id_badge_id_key UNIQUE (user_id, badge_id);


--
-- Name: user_battlepass_claims user_battlepass_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_claims
    ADD CONSTRAINT user_battlepass_claims_pkey PRIMARY KEY (id);


--
-- Name: user_battlepass_progress user_battlepass_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_progress
    ADD CONSTRAINT user_battlepass_progress_pkey PRIMARY KEY (id);


--
-- Name: user_cab_balance user_cab_balance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cab_balance
    ADD CONSTRAINT user_cab_balance_pkey PRIMARY KEY (user_id);


--
-- Name: user_cashback_balance user_cashback_balance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cashback_balance
    ADD CONSTRAINT user_cashback_balance_pkey PRIMARY KEY (user_id);


--
-- Name: user_missions user_missions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_missions
    ADD CONSTRAINT user_missions_pkey PRIMARY KEY (id);


--
-- Name: user_preferences user_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT user_preferences_pkey PRIMARY KEY (user_id);


--
-- Name: user_push_tokens user_push_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_push_tokens
    ADD CONSTRAINT user_push_tokens_pkey PRIMARY KEY (id);


--
-- Name: user_push_tokens user_push_tokens_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_push_tokens
    ADD CONSTRAINT user_push_tokens_token_key UNIQUE (token);


--
-- Name: user_savings_snapshot user_savings_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_savings_snapshot
    ADD CONSTRAINT user_savings_snapshot_pkey PRIMARY KEY (user_id);


--
-- Name: user_session_stats user_session_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_session_stats
    ADD CONSTRAINT user_session_stats_pkey PRIMARY KEY (user_id, period_year, period_month);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_store_preferences user_store_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_store_preferences
    ADD CONSTRAINT user_store_preferences_pkey PRIMARY KEY (id);


--
-- Name: user_store_preferences user_store_preferences_user_id_store_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_store_preferences
    ADD CONSTRAINT user_store_preferences_user_id_store_id_key UNIQUE (user_id, store_id);


--
-- Name: user_streaks user_streaks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_streaks
    ADD CONSTRAINT user_streaks_pkey PRIMARY KEY (user_id);


--
-- Name: user_xp_balance user_xp_balance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_xp_balance
    ADD CONSTRAINT user_xp_balance_pkey PRIMARY KEY (user_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_provider_provider_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_provider_provider_id_key UNIQUE (provider, provider_id);


--
-- Name: xp_transactions xp_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.xp_transactions
    ADD CONSTRAINT xp_transactions_pkey PRIMARY KEY (id);


--
-- Name: community_challenges_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX community_challenges_one_active ON public.community_challenges USING btree (is_active) WHERE (is_active = true);


--
-- Name: gin_products_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX gin_products_name ON public.products USING gin (name public.gin_trgm_ops);


--
-- Name: idx_achievements_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_achievements_category ON public.achievements USING btree (category);


--
-- Name: idx_achievements_trigger_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_achievements_trigger_type ON public.achievements USING btree (trigger_type);


--
-- Name: idx_achievements_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_achievements_window ON public.achievements USING btree (available_from, available_until) WHERE ((available_from IS NOT NULL) OR (available_until IS NOT NULL));


--
-- Name: idx_admin_settings_audit_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_settings_audit_pending ON public.admin_settings_audit USING btree (expires_at) WHERE (status = 'pending_2fa'::public.admin_settings_audit_status);


--
-- Name: idx_admin_settings_audit_section_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_settings_audit_section_ts ON public.admin_settings_audit USING btree (section, "timestamp" DESC);


--
-- Name: idx_admin_settings_audit_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_settings_audit_ts ON public.admin_settings_audit USING btree ("timestamp" DESC);


--
-- Name: idx_affiliate_offers_brand; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_affiliate_offers_brand ON public.affiliate_offers USING btree (brand_id);


--
-- Name: idx_affiliate_offers_ean; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_affiliate_offers_ean ON public.affiliate_offers USING btree (product_ean) WHERE (product_ean IS NOT NULL);


--
-- Name: idx_affiliate_offers_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_affiliate_offers_valid ON public.affiliate_offers USING btree (valid_until) WHERE (valid_until IS NOT NULL);


--
-- Name: idx_brands_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_brands_slug ON public.brands USING btree (slug);


--
-- Name: idx_cashback_tx_scan_ean; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cashback_tx_scan_ean ON public.cashback_transactions USING btree (scan_id, product_ean);


--
-- Name: idx_challenge_responses; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_challenge_responses ON public.price_challenge_responses USING btree (challenge_id);


--
-- Name: idx_discount_campaigns_public; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_discount_campaigns_public ON public.discount_campaigns USING btree (is_public) WHERE (is_public = true);


--
-- Name: idx_discount_campaigns_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_discount_campaigns_valid ON public.discount_campaigns USING btree (valid_until) WHERE (valid_until IS NOT NULL);


--
-- Name: idx_leaderboard_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leaderboard_period ON public.leaderboard_snapshots USING btree (period_year, period_month, rank);


--
-- Name: idx_leaderboard_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leaderboard_user ON public.leaderboard_snapshots USING btree (user_id, period_year DESC, period_month DESC);


--
-- Name: idx_notif_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notif_type ON public.notification_logs USING btree (type, sent_at DESC);


--
-- Name: idx_notif_user_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notif_user_unread ON public.notification_logs USING btree (user_id, sent_at DESC) WHERE (read_at IS NULL);


--
-- Name: idx_ocr_knowledge_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ocr_knowledge_entity_id ON public.ocr_knowledge USING btree (entity_id);


--
-- Name: idx_one_active_subscription; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_one_active_subscription ON public.subscriptions USING btree (user_id) WHERE (status = 'active'::text);


--
-- Name: idx_one_pending_subscription; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_one_pending_subscription ON public.subscriptions USING btree (user_id) WHERE (status = 'pending'::text);


--
-- Name: idx_pal_consensus_state_changed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pal_consensus_state_changed ON public.pipeline_audit_log USING btree (event, ((payload ->> 'store_id'::text)), ((payload ->> 'normalized_label'::text))) WHERE (event = 'consensus_state_changed'::text);


--
-- Name: idx_pnr_consensus; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pnr_consensus ON public.product_name_resolutions USING btree (store_id, normalized_label);


--
-- Name: idx_pnr_norm_label_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pnr_norm_label_trgm ON public.product_name_resolutions USING gin (normalized_label public.gin_trgm_ops) WHERE (retailer_id IS NOT NULL);


--
-- Name: idx_pnr_retailer_source_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pnr_retailer_source_label ON public.product_name_resolutions USING btree (retailer_id, source_type, normalized_label) WHERE (retailer_id IS NOT NULL);


--
-- Name: idx_pnr_scan_source_label; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_pnr_scan_source_label ON public.product_name_resolutions USING btree (scan_id, source_type, normalized_label);


--
-- Name: idx_pnr_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pnr_user ON public.product_name_resolutions USING btree (user_id);


--
-- Name: idx_price_alerts_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_alerts_active ON public.price_alerts USING btree (product_ean, store_id) WHERE (active = true);


--
-- Name: idx_price_alerts_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_alerts_user ON public.price_alerts USING btree (user_id);


--
-- Name: idx_price_challenges_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_challenges_pending ON public.price_challenges USING btree (store_id, created_at) WHERE (status = 'pending'::text);


--
-- Name: idx_products_brand_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_brand_id ON public.products USING btree (brand_id);


--
-- Name: idx_products_brands_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_products_brands_trgm ON public.products USING gin (brands public.gin_trgm_ops);


--
-- Name: idx_push_tokens_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_push_tokens_user ON public.user_push_tokens USING btree (user_id);


--
-- Name: idx_retailer_aliases_alias; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_retailer_aliases_alias ON public.retailer_aliases USING btree (alias);


--
-- Name: idx_retailer_aliases_alias_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_retailer_aliases_alias_trgm ON public.retailer_aliases USING gin (alias public.gin_trgm_ops);


--
-- Name: idx_retailers_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_retailers_parent ON public.retailers USING btree (parent_id);


--
-- Name: idx_retailers_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_retailers_slug ON public.retailers USING btree (slug);


--
-- Name: idx_routes_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_expires ON public.optimized_routes USING btree (expires_at);


--
-- Name: idx_routes_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_user ON public.optimized_routes USING btree (user_id, computed_at DESC);


--
-- Name: idx_scan_debug_purge_after; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scan_debug_purge_after ON public.scan_debug USING btree (purge_after);


--
-- Name: idx_scan_debug_receipt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scan_debug_receipt_id ON public.scan_debug USING btree (receipt_id);


--
-- Name: idx_session_stats_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_stats_period ON public.user_session_stats USING btree (period_year, period_month);


--
-- Name: idx_sessions_daily; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_daily ON public.user_sessions USING btree (started_at DESC);


--
-- Name: idx_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_user ON public.user_sessions USING btree (user_id, started_at DESC);


--
-- Name: idx_store_prefs_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_store_prefs_user ON public.user_store_preferences USING btree (user_id, preference);


--
-- Name: idx_store_validation_history_store_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_store_validation_history_store_id ON public.store_validation_history USING btree (store_id);


--
-- Name: idx_stores_retailer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stores_retailer_id ON public.stores USING btree (retailer_id);


--
-- Name: idx_stores_validation_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stores_validation_pending ON public.stores USING btree (validation_status) WHERE (validation_status = 'pending'::text);


--
-- Name: idx_subscriptions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_user ON public.subscriptions USING btree (user_id, started_at DESC);


--
-- Name: idx_user_achievements_achievement; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_achievements_achievement ON public.user_achievements USING btree (achievement_id);


--
-- Name: idx_user_achievements_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_achievements_user ON public.user_achievements USING btree (user_id, unlocked_at DESC);


--
-- Name: idx_user_badges_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_badges_user ON public.user_badges USING btree (user_id);


--
-- Name: idx_users_trust_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_trust_score ON public.users USING btree (trust_score) WHERE ((trust_score < 75) AND (total_resolved_scans >= 100));


--
-- Name: idx_withdrawals_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdrawals_pending ON public.cashback_withdrawals USING btree (status, requested_at) WHERE (status = 'pending'::text);


--
-- Name: idx_withdrawals_reconcile; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdrawals_reconcile ON public.cashback_withdrawals USING btree (last_reconciled_at) WHERE ((status = 'pending'::text) AND (payment_provider_ref IS NOT NULL));


--
-- Name: idx_withdrawals_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdrawals_user ON public.cashback_withdrawals USING btree (user_id, requested_at DESC);


--
-- Name: ix_batch_sync_log_batch_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_batch_sync_log_batch_name ON public.batch_sync_log USING btree (batch_name);


--
-- Name: ix_cities_postal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cities_postal ON public.cities USING btree (postal_code);


--
-- Name: ix_mxr_user_month; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mxr_user_month ON public.mission_xp_records USING btree (user_id, date_trunc('month'::text, (recorded_at AT TIME ZONE 'UTC'::text)));


--
-- Name: ix_mxr_xp_alltime; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mxr_xp_alltime ON public.mission_xp_records USING btree (xp_earned DESC);


--
-- Name: ix_mystery_challenge_finds_challenge_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mystery_challenge_finds_challenge_id ON public.mystery_challenge_finds USING btree (challenge_id);


--
-- Name: ix_notification_logs_dedup_sent; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_notification_logs_dedup_sent ON public.notification_logs USING btree (user_id, type, date_trunc('minute'::text, (sent_at AT TIME ZONE 'UTC'::text))) WHERE (status = 'sent'::text);


--
-- Name: ix_notification_outbox_unsent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_outbox_unsent ON public.notification_outbox USING btree (sent_at) WHERE (sent_at IS NULL);


--
-- Name: ix_parsed_tickets_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parsed_tickets_created_at ON public.parsed_tickets USING btree (created_at);


--
-- Name: ix_parsed_tickets_image_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parsed_tickets_image_hash ON public.parsed_tickets USING btree (raw_ticket_image_hash);


--
-- Name: ix_parsed_tickets_receipt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parsed_tickets_receipt_id ON public.parsed_tickets USING btree (receipt_id);


--
-- Name: ix_pipeline_audit_log_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_audit_log_created_at ON public.pipeline_audit_log USING btree (created_at);


--
-- Name: ix_pipeline_audit_log_parsed_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_audit_log_parsed_ticket_id ON public.pipeline_audit_log USING btree (parsed_ticket_id);


--
-- Name: ix_pipeline_audit_log_phase_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_audit_log_phase_event ON public.pipeline_audit_log USING btree (phase, event);


--
-- Name: ix_pipeline_audit_log_scan_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_audit_log_scan_id ON public.pipeline_audit_log USING btree (scan_id);


--
-- Name: ix_product_favorites_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_product_favorites_user_id ON public.product_favorites USING btree (user_id);


--
-- Name: ix_products_brands_text_normalized_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_products_brands_text_normalized_trgm ON public.products USING gin ((upper(public.immutable_unaccent(COALESCE(brands_text, ''::text)))) public.gin_trgm_ops);


--
-- Name: ix_products_name_normalized_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_products_name_normalized_trgm ON public.products USING gin (name_normalized public.gin_trgm_ops);


--
-- Name: ix_receipts_parsed_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_receipts_parsed_ticket_id ON public.receipts USING btree (parsed_ticket_id);


--
-- Name: ix_refresh_tokens_jti; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_refresh_tokens_jti ON public.refresh_tokens USING btree (jti);


--
-- Name: ix_refresh_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_refresh_tokens_user_id ON public.refresh_tokens USING btree (user_id);


--
-- Name: ix_reward_events_user_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reward_events_user_action ON public.reward_events USING btree (user_id, action_type, created_at);


--
-- Name: ix_scans_label_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scans_label_session_id ON public.scans USING btree (label_session_id) WHERE (label_session_id IS NOT NULL);


--
-- Name: ix_scans_parsed_ticket_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scans_parsed_ticket_id ON public.scans USING btree (parsed_ticket_id);


--
-- Name: ix_sirene_geocode_cache_address_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sirene_geocode_cache_address_hash ON public.sirene_geocode_cache USING btree (address_hash);


--
-- Name: ix_stores_name_normalized_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_name_normalized_trgm ON public.stores USING gin (name_normalized public.gin_trgm_ops);


--
-- Name: ix_stores_postal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_postal ON public.stores USING btree (postal_code);


--
-- Name: ix_stores_retailer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_retailer ON public.stores USING btree (retailer);


--
-- Name: ix_stores_retailer_store_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_retailer_store_code ON public.stores USING btree (retailer, store_code) WHERE ((store_code IS NOT NULL) AND (retailer IS NOT NULL));


--
-- Name: ix_stores_siret_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_siret_lookup ON public.stores USING btree (siret) WHERE (siret IS NOT NULL);


--
-- Name: ix_stores_store_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stores_store_code ON public.stores USING btree (store_code) WHERE (store_code IS NOT NULL);


--
-- Name: ix_xp_transactions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_xp_transactions_user_id ON public.xp_transactions USING btree (user_id);


--
-- Name: receipts_photo_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX receipts_photo_hash_unique ON public.receipts USING btree (photo_hash) WHERE (photo_hash IS NOT NULL);


--
-- Name: receipts_semantic_dedup_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX receipts_semantic_dedup_key ON public.receipts USING btree (store_id, purchased_at_with_time, total_amount) WHERE ((purchased_at_with_time IS NOT NULL) AND (total_amount IS NOT NULL));


--
-- Name: scans_photo_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX scans_photo_hash_unique ON public.scans USING btree (photo_hash) WHERE ((photo_hash IS NOT NULL) AND (scan_type = 'electronic_label'::text));


--
-- Name: unique_store; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX unique_store ON public.stores USING btree (COALESCE(retailer, ''::text), COALESCE(address, ''::text), COALESCE(postal_code, ''::text)) WHERE ((retailer IS NOT NULL) AND (address IS NOT NULL) AND (NOT is_disabled));


--
-- Name: uq_admin_settings_audit_one_pending_per_section; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_admin_settings_audit_one_pending_per_section ON public.admin_settings_audit USING btree (section) WHERE (status = 'pending_2fa'::public.admin_settings_audit_status);


--
-- Name: uq_cabtx_retro_scan_credit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cabtx_retro_scan_credit ON public.cabecoin_transactions USING btree (reference_id) WHERE ((direction = 'credit'::text) AND (reference_type = 'retro_scan'::text));


--
-- Name: uq_cabtx_scan_credit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cabtx_scan_credit ON public.cabecoin_transactions USING btree (reference_id) WHERE ((direction = 'credit'::text) AND (reference_type = 'scan'::text));


--
-- Name: uq_cashbacktx_scan_ean_credit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cashbacktx_scan_ean_credit ON public.cashback_transactions USING btree (scan_id, product_ean) WHERE (type = 'CREDIT'::text);


--
-- Name: uq_mystery_challenges_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_mystery_challenges_active ON public.mystery_challenges USING btree (status) WHERE (status = 'active'::text);


--
-- Name: uq_one_active_season; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_active_season ON public.battlepass_seasons USING btree (is_active) WHERE (is_active = true);


--
-- Name: uq_receipts_receipt_barcode; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_receipts_receipt_barcode ON public.receipts USING btree (receipt_barcode) WHERE (receipt_barcode IS NOT NULL);


--
-- Name: uq_stores_osm_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_stores_osm_id ON public.stores USING btree (osm_id) WHERE (osm_id IS NOT NULL);


--
-- Name: uq_stores_siret; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_stores_siret ON public.stores USING btree (siret) WHERE (siret IS NOT NULL);


--
-- Name: uq_users_support_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_users_support_id ON public.users USING btree (support_id);


--
-- Name: affiliate_offers trg_affiliate_offers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_affiliate_offers_updated_at BEFORE UPDATE ON public.affiliate_offers FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: cashback_withdrawals trg_cashback_withdrawals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cashback_withdrawals_updated_at BEFORE UPDATE ON public.cashback_withdrawals FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: categories trg_categories_no_cycle; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_categories_no_cycle BEFORE INSERT OR UPDATE OF parent_id ON public.categories FOR EACH ROW WHEN ((new.parent_id IS NOT NULL)) EXECUTE FUNCTION public.fn_check_category_cycle();


--
-- Name: categories trg_categories_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_categories_updated_at BEFORE UPDATE ON public.categories FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: discount_campaigns trg_discount_campaigns_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_discount_campaigns_updated_at BEFORE UPDATE ON public.discount_campaigns FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: subscriptions trg_increment_discount_uses; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_increment_discount_uses BEFORE INSERT OR UPDATE ON public.subscriptions FOR EACH ROW EXECUTE FUNCTION public.fn_increment_discount_uses();


--
-- Name: level_tiers trg_level_tiers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_level_tiers_updated_at BEFORE UPDATE ON public.level_tiers FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: pipeline_audit_log trg_pipeline_audit_log_no_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_pipeline_audit_log_no_update BEFORE UPDATE ON public.pipeline_audit_log FOR EACH ROW EXECUTE FUNCTION public.fn_pipeline_audit_log_no_update();


--
-- Name: product_name_resolutions trg_pnr_sync_retailer_id; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_pnr_sync_retailer_id BEFORE INSERT OR UPDATE OF store_id ON public.product_name_resolutions FOR EACH ROW EXECUTE FUNCTION public.fn_sync_pnr_retailer_id();


--
-- Name: price_alerts trg_price_alerts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_price_alerts_updated_at BEFORE UPDATE ON public.price_alerts FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: price_challenges trg_price_challenges_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_price_challenges_updated_at BEFORE UPDATE ON public.price_challenges FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: product_tracking trg_product_tracking_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_product_tracking_updated_at BEFORE UPDATE ON public.product_tracking FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: products trg_products_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON public.products FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: receipts trg_receipts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_receipts_updated_at BEFORE UPDATE ON public.receipts FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: retailer_receipt_formats trg_retailer_receipt_formats_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_retailer_receipt_formats_updated_at BEFORE UPDATE ON public.retailer_receipt_formats FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: retailers trg_retailers_cascade_name_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_retailers_cascade_name_change AFTER UPDATE OF canonical_name ON public.retailers FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_retailer_canonical_name_change();


--
-- Name: retailers trg_retailers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_retailers_updated_at BEFORE UPDATE ON public.retailers FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: reward_config trg_reward_config_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reward_config_updated_at BEFORE UPDATE ON public.reward_config FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: scans trg_scan_status_transition; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scan_status_transition BEFORE UPDATE OF status ON public.scans FOR EACH ROW EXECUTE FUNCTION public.fn_check_scan_status_transition();


--
-- Name: shopping_list_items trg_shopping_list_items_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_shopping_list_items_updated_at BEFORE UPDATE ON public.shopping_list_items FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: shopping_lists trg_shopping_list_name; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_shopping_list_name BEFORE INSERT OR UPDATE OF name, has_default_name ON public.shopping_lists FOR EACH ROW EXECUTE FUNCTION public.fn_shopping_list_name();


--
-- Name: shopping_lists trg_shopping_lists_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_shopping_lists_updated_at BEFORE UPDATE ON public.shopping_lists FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: store_candidates trg_store_candidates_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_store_candidates_updated_at BEFORE UPDATE ON public.store_candidates FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: store_fingerprints trg_store_fingerprints_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_store_fingerprints_updated_at BEFORE UPDATE ON public.store_fingerprints FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: stores trg_stores_sync_retailer_text; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_stores_sync_retailer_text BEFORE INSERT OR UPDATE OF retailer_id ON public.stores FOR EACH ROW EXECUTE FUNCTION public.fn_sync_store_retailer_text();


--
-- Name: stores trg_stores_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_stores_updated_at BEFORE UPDATE ON public.stores FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: streak_tiers trg_streak_tiers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_streak_tiers_updated_at BEFORE UPDATE ON public.streak_tiers FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: subscriptions trg_subscription_discount_uses; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_subscription_discount_uses AFTER INSERT ON public.subscriptions FOR EACH ROW EXECUTE FUNCTION public.fn_increment_discount_uses();


--
-- Name: user_battlepass_progress trg_ubp_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ubp_updated_at BEFORE UPDATE ON public.user_battlepass_progress FOR EACH ROW EXECUTE FUNCTION public.fn_set_ubp_updated_at();


--
-- Name: user_cab_balance trg_user_cab_balance_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_cab_balance_updated_at BEFORE UPDATE ON public.user_cab_balance FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: user_cashback_balance trg_user_cashback_balance_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_cashback_balance_updated_at BEFORE UPDATE ON public.user_cashback_balance FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: user_preferences trg_user_preferences_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_preferences_updated_at BEFORE UPDATE ON public.user_preferences FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: user_savings_snapshot trg_user_savings_snapshot_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_savings_snapshot_updated_at BEFORE UPDATE ON public.user_savings_snapshot FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: user_streaks trg_user_streaks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_streaks_updated_at BEFORE UPDATE ON public.user_streaks FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at();


--
-- Name: cabecoin_transactions cabecoin_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cabecoin_transactions
    ADD CONSTRAINT cabecoin_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: community_challenge_claims community_challenge_claims_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_claims
    ADD CONSTRAINT community_challenge_claims_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.community_challenges(id) ON DELETE CASCADE;


--
-- Name: community_challenge_claims community_challenge_claims_milestone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_claims
    ADD CONSTRAINT community_challenge_claims_milestone_id_fkey FOREIGN KEY (milestone_id) REFERENCES public.community_challenge_milestones(id) ON DELETE CASCADE;


--
-- Name: community_challenge_claims community_challenge_claims_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_claims
    ADD CONSTRAINT community_challenge_claims_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: community_challenge_milestones community_challenge_milestones_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_milestones
    ADD CONSTRAINT community_challenge_milestones_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.community_challenges(id) ON DELETE CASCADE;


--
-- Name: community_challenge_progress community_challenge_progress_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_challenge_progress
    ADD CONSTRAINT community_challenge_progress_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.community_challenges(id) ON DELETE CASCADE;


--
-- Name: community_multipliers community_multipliers_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_multipliers
    ADD CONSTRAINT community_multipliers_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.community_challenges(id) ON DELETE RESTRICT;


--
-- Name: community_multipliers community_multipliers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.community_multipliers
    ADD CONSTRAINT community_multipliers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: cashback_transactions fk_affiliate_offer; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT fk_affiliate_offer FOREIGN KEY (affiliate_offer_id) REFERENCES public.affiliate_offers(id) ON DELETE SET NULL;


--
-- Name: affiliate_offers fk_affiliate_offers_brand; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliate_offers
    ADD CONSTRAINT fk_affiliate_offers_brand FOREIGN KEY (brand_id) REFERENCES public.brands(id) ON DELETE RESTRICT;


--
-- Name: user_badges fk_badge; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badges
    ADD CONSTRAINT fk_badge FOREIGN KEY (badge_id) REFERENCES public.badges(id) ON DELETE CASCADE;


--
-- Name: cashback_transactions fk_cashback_tx_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT fk_cashback_tx_parent FOREIGN KEY (parent_transaction_id) REFERENCES public.cashback_transactions(id) ON DELETE SET NULL;


--
-- Name: cashback_transactions fk_cashback_tx_scan; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT fk_cashback_tx_scan FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE SET NULL;


--
-- Name: products fk_category; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT fk_category FOREIGN KEY (category_id) REFERENCES public.categories(id) ON DELETE SET NULL;


--
-- Name: price_challenge_responses fk_challenge; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenge_responses
    ADD CONSTRAINT fk_challenge FOREIGN KEY (challenge_id) REFERENCES public.price_challenges(id) ON DELETE CASCADE;


--
-- Name: price_consensus_history fk_consensus; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_history
    ADD CONSTRAINT fk_consensus FOREIGN KEY (consensus_id) REFERENCES public.price_consensus(id) ON DELETE CASCADE;


--
-- Name: price_consensus_scans fk_consensus; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_scans
    ADD CONSTRAINT fk_consensus FOREIGN KEY (consensus_id) REFERENCES public.price_consensus(id) ON DELETE CASCADE;


--
-- Name: users fk_current_level; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_current_level FOREIGN KEY (current_level_id) REFERENCES public.level_tiers(id) ON DELETE SET NULL;


--
-- Name: subscriptions fk_discount; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT fk_discount FOREIGN KEY (discount_campaign_code) REFERENCES public.discount_campaigns(code) ON DELETE RESTRICT;


--
-- Name: optimized_routes fk_list; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.optimized_routes
    ADD CONSTRAINT fk_list FOREIGN KEY (list_id) REFERENCES public.shopping_lists(id) ON DELETE CASCADE;


--
-- Name: shopping_list_items fk_list; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_list_items
    ADD CONSTRAINT fk_list FOREIGN KEY (list_id) REFERENCES public.shopping_lists(id) ON DELETE CASCADE;


--
-- Name: user_battlepass_claims fk_milestone; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_claims
    ADD CONSTRAINT fk_milestone FOREIGN KEY (milestone_id) REFERENCES public.battlepass_milestones(id) ON DELETE RESTRICT;


--
-- Name: user_missions fk_mission; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_missions
    ADD CONSTRAINT fk_mission FOREIGN KEY (mission_id) REFERENCES public.missions(id) ON DELETE RESTRICT;


--
-- Name: categories fk_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES public.categories(id) ON DELETE SET NULL;


--
-- Name: affiliate_offers fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliate_offers
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: cashback_transactions fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: price_alerts fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_alerts
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE CASCADE;


--
-- Name: price_challenges fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenges
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE SET NULL;


--
-- Name: price_consensus fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: price_consensus_history fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_history
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: product_tracking fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_tracking
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: scans fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE SET NULL;


--
-- Name: shopping_list_items fk_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_list_items
    ADD CONSTRAINT fk_product FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: products fk_products_brand; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT fk_products_brand FOREIGN KEY (brand_id) REFERENCES public.brands(id) ON DELETE SET NULL;


--
-- Name: scans fk_receipt; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT fk_receipt FOREIGN KEY (receipt_id) REFERENCES public.receipts(id) ON DELETE SET NULL;


--
-- Name: receipts fk_receipts_parsed_ticket; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT fk_receipts_parsed_ticket FOREIGN KEY (parsed_ticket_id) REFERENCES public.parsed_tickets(id) ON DELETE SET NULL;


--
-- Name: price_challenges fk_scan; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenges
    ADD CONSTRAINT fk_scan FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE RESTRICT;


--
-- Name: price_consensus_scans fk_scan; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_scans
    ADD CONSTRAINT fk_scan FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE RESTRICT;


--
-- Name: scans fk_scans_parsed_ticket; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT fk_scans_parsed_ticket FOREIGN KEY (parsed_ticket_id) REFERENCES public.parsed_tickets(id) ON DELETE SET NULL;


--
-- Name: battlepass_milestones fk_season; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.battlepass_milestones
    ADD CONSTRAINT fk_season FOREIGN KEY (season_id) REFERENCES public.battlepass_seasons(id) ON DELETE RESTRICT;


--
-- Name: user_battlepass_progress fk_season; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_progress
    ADD CONSTRAINT fk_season FOREIGN KEY (season_id) REFERENCES public.battlepass_seasons(id) ON DELETE RESTRICT;


--
-- Name: price_alerts fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_alerts
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: price_challenges fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenges
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE RESTRICT;


--
-- Name: price_consensus fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE RESTRICT;


--
-- Name: price_consensus_history fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_consensus_history
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE RESTRICT;


--
-- Name: receipts fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE SET NULL;


--
-- Name: scans fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE RESTRICT;


--
-- Name: user_store_preferences fk_store; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_store_preferences
    ADD CONSTRAINT fk_store FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: store_candidates fk_store_candidates_matched_store_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_candidates
    ADD CONSTRAINT fk_store_candidates_matched_store_id FOREIGN KEY (matched_store_id) REFERENCES public.stores(id) ON DELETE SET NULL;


--
-- Name: store_fingerprints fk_store_fingerprints_store_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_fingerprints
    ADD CONSTRAINT fk_store_fingerprints_store_id FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: cashback_withdrawals fk_transaction; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_withdrawals
    ADD CONSTRAINT fk_transaction FOREIGN KEY (cashback_transaction_id) REFERENCES public.cashback_transactions(id) ON DELETE RESTRICT;


--
-- Name: cashback_transactions fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_transactions
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: cashback_withdrawals fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cashback_withdrawals
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: leaderboard_snapshots fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leaderboard_snapshots
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: notification_logs fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: optimized_routes fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.optimized_routes
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: price_alerts fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_alerts
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: price_challenge_responses fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_challenge_responses
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: product_tracking fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_tracking
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: receipts fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipts
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: scans fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shopping_lists fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shopping_lists
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: subscriptions fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_badges fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_badges
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_battlepass_claims fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_claims
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: user_battlepass_progress fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_battlepass_progress
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: user_cab_balance fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cab_balance
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: user_cashback_balance fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cashback_balance
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_missions fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_missions
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: user_preferences fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_push_tokens fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_push_tokens
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_session_stats fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_session_stats
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_store_preferences fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_store_preferences
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_streaks fk_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_streaks
    ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: gift_card_orders gift_card_orders_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_orders
    ADD CONSTRAINT gift_card_orders_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.gift_card_brands(id) ON DELETE RESTRICT;


--
-- Name: gift_card_orders gift_card_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gift_card_orders
    ADD CONSTRAINT gift_card_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: label_sessions label_sessions_store_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_sessions
    ADD CONSTRAINT label_sessions_store_id_fkey FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE RESTRICT;


--
-- Name: label_sessions label_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.label_sessions
    ADD CONSTRAINT label_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: mission_xp_records mission_xp_records_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_xp_records
    ADD CONSTRAINT mission_xp_records_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES public.missions(id) ON DELETE RESTRICT;


--
-- Name: mission_xp_records mission_xp_records_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_xp_records
    ADD CONSTRAINT mission_xp_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: mission_xp_records mission_xp_records_user_mission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mission_xp_records
    ADD CONSTRAINT mission_xp_records_user_mission_id_fkey FOREIGN KEY (user_mission_id) REFERENCES public.user_missions(id) ON DELETE RESTRICT;


--
-- Name: mystery_challenge_clues mystery_challenge_clues_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_clues
    ADD CONSTRAINT mystery_challenge_clues_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.mystery_challenges(id) ON DELETE CASCADE;


--
-- Name: mystery_challenge_exclusions mystery_challenge_exclusions_product_ean_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_exclusions
    ADD CONSTRAINT mystery_challenge_exclusions_product_ean_fkey FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE CASCADE;


--
-- Name: mystery_challenge_finds mystery_challenge_finds_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_finds
    ADD CONSTRAINT mystery_challenge_finds_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.mystery_challenges(id) ON DELETE RESTRICT;


--
-- Name: mystery_challenge_finds mystery_challenge_finds_scan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_finds
    ADD CONSTRAINT mystery_challenge_finds_scan_id_fkey FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE RESTRICT;


--
-- Name: mystery_challenge_finds mystery_challenge_finds_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenge_finds
    ADD CONSTRAINT mystery_challenge_finds_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: mystery_challenges mystery_challenges_product_ean_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mystery_challenges
    ADD CONSTRAINT mystery_challenges_product_ean_fkey FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: notification_outbox notification_outbox_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT notification_outbox_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: parsed_tickets parsed_tickets_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_tickets
    ADD CONSTRAINT parsed_tickets_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.receipts(id) ON DELETE CASCADE;


--
-- Name: pipeline_audit_log pipeline_audit_log_parsed_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_audit_log
    ADD CONSTRAINT pipeline_audit_log_parsed_ticket_id_fkey FOREIGN KEY (parsed_ticket_id) REFERENCES public.parsed_tickets(id) ON DELETE SET NULL;


--
-- Name: pipeline_audit_log pipeline_audit_log_scan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_audit_log
    ADD CONSTRAINT pipeline_audit_log_scan_id_fkey FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE SET NULL;


--
-- Name: product_favorites product_favorites_product_ean_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_favorites
    ADD CONSTRAINT product_favorites_product_ean_fkey FOREIGN KEY (product_ean) REFERENCES public.products(ean) ON DELETE RESTRICT;


--
-- Name: product_favorites product_favorites_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_favorites
    ADD CONSTRAINT product_favorites_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: product_name_resolutions product_name_resolutions_retailer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_name_resolutions
    ADD CONSTRAINT product_name_resolutions_retailer_id_fkey FOREIGN KEY (retailer_id) REFERENCES public.retailers(id) ON DELETE RESTRICT;


--
-- Name: product_name_resolutions product_name_resolutions_scan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_name_resolutions
    ADD CONSTRAINT product_name_resolutions_scan_id_fkey FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE CASCADE;


--
-- Name: product_name_resolutions product_name_resolutions_store_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_name_resolutions
    ADD CONSTRAINT product_name_resolutions_store_id_fkey FOREIGN KEY (store_id) REFERENCES public.stores(id);


--
-- Name: product_name_resolutions product_name_resolutions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_name_resolutions
    ADD CONSTRAINT product_name_resolutions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: referral_codes referral_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_codes
    ADD CONSTRAINT referral_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: referral_uses referral_uses_referral_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_uses
    ADD CONSTRAINT referral_uses_referral_id_fkey FOREIGN KEY (referral_id) REFERENCES public.referral_codes(id) ON DELETE RESTRICT;


--
-- Name: referral_uses referral_uses_referred_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.referral_uses
    ADD CONSTRAINT referral_uses_referred_user_id_fkey FOREIGN KEY (referred_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: refresh_tokens refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: retailer_aliases retailer_aliases_retailer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailer_aliases
    ADD CONSTRAINT retailer_aliases_retailer_id_fkey FOREIGN KEY (retailer_id) REFERENCES public.retailers(id) ON DELETE CASCADE;


--
-- Name: retailers retailers_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retailers
    ADD CONSTRAINT retailers_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.retailers(id) ON DELETE SET NULL;


--
-- Name: reward_events reward_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reward_events
    ADD CONSTRAINT reward_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: scan_debug scan_debug_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_debug
    ADD CONSTRAINT scan_debug_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.receipts(id) ON DELETE CASCADE;


--
-- Name: scan_debug scan_debug_scan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_debug
    ADD CONSTRAINT scan_debug_scan_id_fkey FOREIGN KEY (scan_id) REFERENCES public.scans(id) ON DELETE SET NULL;


--
-- Name: scans scans_label_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scans
    ADD CONSTRAINT scans_label_session_id_fkey FOREIGN KEY (label_session_id) REFERENCES public.label_sessions(id) ON DELETE SET NULL;


--
-- Name: store_candidates store_candidates_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_candidates
    ADD CONSTRAINT store_candidates_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.receipts(id) ON DELETE SET NULL;


--
-- Name: store_validation_history store_validation_history_store_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.store_validation_history
    ADD CONSTRAINT store_validation_history_store_id_fkey FOREIGN KEY (store_id) REFERENCES public.stores(id) ON DELETE CASCADE;


--
-- Name: stores stores_retailer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT stores_retailer_id_fkey FOREIGN KEY (retailer_id) REFERENCES public.retailers(id) ON DELETE SET NULL;


--
-- Name: stores stores_suggested_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stores
    ADD CONSTRAINT stores_suggested_by_user_id_fkey FOREIGN KEY (suggested_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: user_achievements user_achievements_achievement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_achievement_id_fkey FOREIGN KEY (achievement_id) REFERENCES public.achievements(id) ON DELETE RESTRICT;


--
-- Name: user_achievements user_achievements_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_savings_snapshot user_savings_snapshot_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_savings_snapshot
    ADD CONSTRAINT user_savings_snapshot_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_xp_balance user_xp_balance_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_xp_balance
    ADD CONSTRAINT user_xp_balance_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: xp_transactions xp_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.xp_transactions
    ADD CONSTRAINT xp_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

\unrestrict mLdh4BTVaFsCnaCpy45DyRPhFs6Js3qboUps6VLUNfKH4C9Ac9X0kWUzf5hn749

