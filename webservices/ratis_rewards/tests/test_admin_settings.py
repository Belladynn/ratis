"""Tests TDD pour app_settings — load_settings() DB-first + endpoints admin."""

from __future__ import annotations

import os

from ratis_core.settings import load_settings
from sqlalchemy import text

from tests.conftest import make_user  # noqa — pour les tests auth ultérieurs


class TestLoadSettingsDbFirst:
    def test_load_settings_returns_dict_with_known_sections(self, db):
        """load_settings() retourne au moins les sections connues du JSON."""
        cfg = load_settings()
        assert "rewards" in cfg
        assert "gamification" in cfg
        assert "xp" in cfg

    def test_load_settings_db_overrides_json(self, db):
        """Quand app_settings contient une section, elle écrase le JSON."""
        # Use a direct committed connection — NullPool in load_settings() needs
        # the data to be visible to external connections (PostgreSQL READ COMMITTED).
        # The test `db` fixture uses savepoints; db.commit() releases only the savepoint,
        # so the data is not committed to PostgreSQL. We use psycopg directly.
        import psycopg

        db_url = os.environ["DATABASE_URL"]
        # Convert SQLAlchemy URL (postgresql+psycopg://...) to psycopg DSN
        dsn = db_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute(
                "INSERT INTO app_settings (section, data) "
                "VALUES ('rewards', '{\"cab_per_receipt_scan\": 9999}'::jsonb) "
                "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
            )
            conn.commit()

        try:
            cfg = load_settings()
            assert cfg["rewards"]["cab_per_receipt_scan"] == 9999
        finally:
            # Restore the section to its original JSON value so subsequent
            # tests that rely on the seeded DB data still work.
            import json as _json

            from ratis_core.settings import _load_from_json

            original_rewards = _load_from_json()["rewards"]
            with psycopg.connect(dsn, autocommit=False) as conn:
                conn.execute(
                    "INSERT INTO app_settings (section, data) "
                    "VALUES ('rewards', CAST(%s AS jsonb)) "
                    "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data",
                    (_json.dumps(original_rewards),),
                )
                conn.commit()

    def test_load_settings_fallback_to_json_when_db_empty(self, db):
        """Si app_settings est vide, load_settings retourne les valeurs JSON."""
        # L'ensemble de la table doit être vide pour déclencher le fallback JSON.
        # Supprimer toutes les sections via psycopg pour bypasser les savepoints.
        import psycopg

        db_url = os.environ["DATABASE_URL"]
        dsn = db_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute("DELETE FROM app_settings")
            conn.commit()

        try:
            cfg = load_settings()
            # La valeur JSON post-recalibration V1.x est 20
            assert cfg["rewards"]["cab_per_receipt_scan"] == 20
        finally:
            # Re-seed so that subsequent tests (incl. lifespan) see all sections.
            import json as _json

            from ratis_core.settings import _load_from_json

            all_cfg = _load_from_json()
            with psycopg.connect(dsn, autocommit=False) as conn:
                for section, data in all_cfg.items():
                    conn.execute(
                        "INSERT INTO app_settings (section, data) "
                        "VALUES (%s, CAST(%s AS jsonb)) "
                        "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data",
                        (section, _json.dumps(data)),
                    )
                conn.commit()


class TestAdminSettingsEndpoints:
    def test_get_all_sections_empty_db(self, admin_client, db):
        """GET /admin/settings retourne dict vide si table vide."""
        db.execute(text("DELETE FROM app_settings"))
        db.commit()

        resp = admin_client.get("/api/v1/admin/settings")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_seed_populates_all_sections(self, admin_client, db):
        """POST /admin/settings/seed insère toutes les sections JSON."""
        db.execute(text("DELETE FROM app_settings"))
        db.commit()

        resp = admin_client.post("/api/v1/admin/settings/seed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["seeded"] > 0

        # Vérifier que rewards est maintenant en DB
        row = db.execute(text("SELECT data FROM app_settings WHERE section = 'rewards'")).first()
        assert row is not None
        assert "cab_per_receipt_scan" in row.data

    def test_get_section(self, admin_client, db):
        """GET /admin/settings/{section} retourne la section."""
        import json

        db.execute(
            text(
                "INSERT INTO app_settings (section, data) "
                "VALUES ('rewards', CAST(:d AS jsonb)) "
                "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
            ),
            {"d": json.dumps({"cab_per_receipt_scan": 42})},
        )
        db.commit()

        resp = admin_client.get("/api/v1/admin/settings/rewards")
        assert resp.status_code == 200
        assert resp.json()["cab_per_receipt_scan"] == 42

    def test_get_unknown_section_returns_404(self, admin_client, db):
        """GET /admin/settings/unknown → 404."""
        db.execute(text("DELETE FROM app_settings WHERE section = 'unknown_xyz'"))
        db.commit()

        resp = admin_client.get("/api/v1/admin/settings/unknown_xyz")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "settings_section_not_found"

    def test_put_section_creates_if_absent(self, admin_client, db):
        """PUT /admin/settings/rewards crée la section si absente.

        Bloc B contract : body = {data, reason}. ``reason`` ≥ 8 chars
        mandatory. Returns ``{audit_id, status}`` rather than the payload.
        """
        db.execute(text("DELETE FROM app_settings WHERE section = 'rewards'"))
        db.commit()

        resp = admin_client.put(
            "/api/v1/admin/settings/rewards",
            json={
                "data": {"cab_per_receipt_scan": 99, "cab_per_label_scan": 15},
                "reason": "alpha first-write rewards section",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        assert "audit_id" in body

        # Vérifie en DB
        row = db.execute(text("SELECT data FROM app_settings WHERE section = 'rewards'")).first()
        assert row.data["cab_per_receipt_scan"] == 99

    def test_put_section_updates_existing(self, admin_client, db):
        """PUT /admin/settings/rewards met à jour une section existante.

        Variation under the 50 % magnitude threshold (50 → 75 = +50 % is
        the boundary ; we use 50 → 60 here = +20 % to stay safely below).
        """
        import json

        db.execute(
            text(
                "INSERT INTO app_settings (section, data) "
                "VALUES ('rewards', CAST(:d AS jsonb)) "
                "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
            ),
            {"d": json.dumps({"cab_per_receipt_scan": 50})},
        )
        db.commit()

        resp = admin_client.put(
            "/api/v1/admin/settings/rewards",
            json={
                "data": {"cab_per_receipt_scan": 60},
                "reason": "alpha rewards bump 50 to 60",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

        row = db.execute(text("SELECT data FROM app_settings WHERE section = 'rewards'")).first()
        assert row.data["cab_per_receipt_scan"] == 60

    def test_requires_admin_key(self, raw_client, db):
        """Endpoints admin sans clé → 403."""
        resp = raw_client.get("/api/v1/admin/settings")
        assert resp.status_code == 403
