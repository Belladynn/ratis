"""Tests for the user-facing achievement endpoints.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § 7
"Endpoints API + admin + frontend integration".

Endpoints exercised here :

* ``GET  /api/v1/rewards/achievements``                — list catalogued
  achievements grouped by category, with the user's unlock state.
  Filters ``category=…`` and ``unlocked=true|false``.
* ``GET  /api/v1/rewards/achievements/{achievement_id}`` — single
  achievement detail (404 if hidden + not unlocked).
* ``POST /api/v1/rewards/achievements/secret-event``    — fire a secret
  event (``konami_code_entered`` / ``app_opened_at_3am``) which the
  achievement_service may turn into an unlock. Rate-limited 10/h/user.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.conftest import make_user


def _seed_user_unlock(db, user_id, achievement_id, *, cab_granted: int = 0):
    """Insert a user_achievements row directly (bypasses _unlock service)."""
    db.execute(
        text(
            "INSERT INTO user_achievements "
            "  (id, user_id, achievement_id, cab_granted, unlocked_at) "
            "VALUES (:id, :uid, :aid, :cab, now())"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "aid": achievement_id,
            "cab": cab_granted,
        },
    )
    db.commit()


# ===========================================================================
# GET /api/v1/rewards/achievements — listing
# ===========================================================================


class TestListAchievements:
    def test_returns_categories_with_items(self, db, user_client):
        """Listing groups the catalog (+ seed) achievements by category."""
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        # Seed catalog has at least "volume" + "savings" + "streak" entries.
        cat_keys = {c["key"] for c in data["categories"]}
        assert "volume" in cat_keys

        # Each category holds an items list with the expected dict shape.
        first = data["categories"][0]
        assert "key" in first
        assert "label" in first
        assert "items" in first
        assert first["items"], "expected at least one item per non-empty category"
        item = first["items"][0]
        for field in (
            "id",
            "code",
            "label",
            "description",
            "icon",
            "rarity",
            "category",
            "cab_reward",
            "target_value",
            "progress",
            "unlocked",
            "unlocked_at",
            "window_open",
        ):
            assert field in item, f"missing field {field!r} in item dict"

    def test_filters_by_category(self, db, user_client):
        """``?category=volume`` returns only the requested bucket."""
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements?category=volume")
        assert resp.status_code == 200
        data = resp.json()
        keys = {c["key"] for c in data["categories"]}
        assert keys == {"volume"}

    def test_filters_unlocked_true(self, db, user_client, achievement_factory):
        """``?unlocked=true`` returns only achievements the user has unlocked."""
        http, set_user = user_client
        uid = make_user(db)
        ach = achievement_factory(code="ep_only_unlocked", category="volume")
        _seed_user_unlock(db, uid, ach.id)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements?unlocked=true")
        assert resp.status_code == 200
        data = resp.json()
        ids = {item["id"] for cat in data["categories"] for item in cat["items"]}
        assert str(ach.id) in ids
        for cat in data["categories"]:
            for item in cat["items"]:
                assert item["unlocked"] is True

    def test_filters_unlocked_false(self, db, user_client, achievement_factory):
        """``?unlocked=false`` excludes achievements the user has unlocked."""
        http, set_user = user_client
        uid = make_user(db)
        ach_unlocked = achievement_factory(code="ep_unl_filter", category="volume")
        _seed_user_unlock(db, uid, ach_unlocked.id)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements?unlocked=false")
        assert resp.status_code == 200
        data = resp.json()
        ids = {item["id"] for cat in data["categories"] for item in cat["items"]}
        assert str(ach_unlocked.id) not in ids
        for cat in data["categories"]:
            for item in cat["items"]:
                assert item["unlocked"] is False

    def test_jyetais_category_only_when_user_has_unlocks(self, db, user_client, achievement_factory):
        """``j_y_etais`` only appears when at least one closed-window
        achievement was unlocked by this user — never as an empty bucket."""
        http, set_user = user_client
        uid = make_user(db)
        # Closed seasonal that the user did unlock.
        now = datetime.now(UTC)
        ach = achievement_factory(
            code="ep_closed_unlocked",
            category="seasonal",
            available_from=now - timedelta(days=200),
            available_until=now - timedelta(days=100),
        )
        _seed_user_unlock(db, uid, ach.id)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements")
        assert resp.status_code == 200
        keys = [c["key"] for c in resp.json()["categories"]]
        assert "j_y_etais" in keys

        # Other user (no closed-unlock) → no j_y_etais bucket.
        uid2 = make_user(db)
        set_user(uid2)
        resp2 = http.get("/api/v1/rewards/achievements")
        keys2 = [c["key"] for c in resp2.json()["categories"]]
        assert "j_y_etais" not in keys2

    def test_requires_auth(self, raw_client):
        """Anonymous request → 401/403."""
        resp = raw_client.get("/api/v1/rewards/achievements")
        assert resp.status_code in (401, 403)

    def test_progress_field_populated_for_non_unlocked(self, db, user_client, achievement_factory):
        """V1.1 (KP-76) — ``progress`` is the live X/Y bar value (not null)
        for non-unlocked, non-secret, non-hidden achievements that have a
        registered computer.

        This is the regression guard against the original V1 bug where
        ``progress: null`` was hardcoded for every achievement.
        """
        http, set_user = user_client
        uid = make_user(db)
        # Create a scan_count achievement target=5 + insert 2 accepted scans
        # for the user → expected progress = 2.
        ach = achievement_factory(
            code="ep_progress_live",
            category="volume",
            trigger_type="scan_count",
            target_value=5,
            cab_reward=20,
        )
        # Insert 2 accepted scans directly via the DB to avoid the scan
        # factory dependency cycle in this fixture.
        store_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO stores (id, name, lat, lng, is_disabled, "
                "                    created_at, updated_at) "
                "VALUES (:id, 'Test Store', 0, 0, false, now(), now())"
            ),
            {"id": store_id},
        )
        for _ in range(2):
            # Seed the sibling receipt row to satisfy ``receipt_required``.
            receipt_id = uuid.uuid4()
            db.execute(
                text(
                    "INSERT INTO receipts "
                    "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
                    "VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())"
                ),
                {"id": receipt_id, "uid": uid, "sid": store_id},
            )
            db.execute(
                text(
                    "INSERT INTO scans "
                    "    (id, user_id, store_id, price, quantity, scan_type, "
                    "     status, receipt_id, scanned_at, status_updated_at) "
                    "VALUES (:id, :uid, :sid, 0, 1, 'receipt', 'accepted', "
                    "        :rid, now(), now())"
                ),
                {"id": uuid.uuid4(), "uid": uid, "sid": store_id, "rid": receipt_id},
            )
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements")
        assert resp.status_code == 200
        # Find our achievement in the response by id.
        item = None
        for cat in resp.json()["categories"]:
            for it in cat["items"]:
                if it["id"] == str(ach.id):
                    item = it
                    break
        assert item is not None, "achievement missing from listing"
        # KP-76 fix : progress is the live count (2), not null.
        assert item["progress"] == 2
        assert item["target_value"] == 5.0
        assert item["unlocked"] is False

    def test_progress_field_equals_target_when_unlocked(self, db, user_client, achievement_factory):
        """Unlocked achievements report progress = target_value (full bar)."""
        http, set_user = user_client
        uid = make_user(db)
        ach = achievement_factory(
            code="ep_progress_unlocked",
            category="volume",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )
        _seed_user_unlock(db, uid, ach.id, cab_granted=20)
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements")
        item = None
        for cat in resp.json()["categories"]:
            for it in cat["items"]:
                if it["id"] == str(ach.id):
                    item = it
                    break
        assert item is not None
        assert item["unlocked"] is True
        assert item["progress"] == 10.0
        assert item["target_value"] == 10.0

    def test_progress_field_null_for_secret_not_unlocked(self, db, user_client, achievement_factory):
        """Secret unrevealed → progress always null (no leak of how close)."""
        http, set_user = user_client
        uid = make_user(db)
        achievement_factory(
            code="ep_secret_progress",
            category="secret",
            trigger_type="scan_count",
            target_value=100,
            cab_reward=500,
            is_secret=True,
        )
        set_user(uid)

        resp = http.get("/api/v1/rewards/achievements?category=secret")
        # Find our masked dict (label="???").
        secret_item = None
        for cat in resp.json()["categories"]:
            for it in cat["items"]:
                if it["label"] == "???":
                    secret_item = it
                    break
        assert secret_item is not None
        assert secret_item["progress"] is None


# ===========================================================================
# GET /api/v1/rewards/achievements/{achievement_id} — detail
# ===========================================================================


class TestGetAchievementDetail:
    def test_returns_full_dict_when_visible(self, db, user_client, achievement_factory):
        http, set_user = user_client
        uid = make_user(db)
        ach = achievement_factory(code="ep_detail_one")
        set_user(uid)

        resp = http.get(f"/api/v1/rewards/achievements/{ach.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(ach.id)
        assert data["code"] == "ep_detail_one"
        assert data["unlocked"] is False

    def test_returns_404_when_unknown(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = http.get(f"/api/v1/rewards/achievements/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "achievement_not_found"

    def test_returns_404_when_hidden_and_not_unlocked(self, db, user_client, achievement_factory):
        """Hidden achievements behave as if non-existent until unlocked."""
        http, set_user = user_client
        uid = make_user(db)
        ach = achievement_factory(code="ep_detail_hidden", is_hidden=True)
        set_user(uid)

        resp = http.get(f"/api/v1/rewards/achievements/{ach.id}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "achievement_not_found"


# ===========================================================================
# POST /api/v1/rewards/achievements/secret-event
# ===========================================================================


class TestPostSecretEvent:
    def test_konami_event_returns_200_and_dispatches(self, db, user_client, monkeypatch):
        """Valid event_type → 200 + dispatcher invoked with that event_type."""
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        captured: list[tuple] = []

        def _fake_check(db_arg, user_id, event_type, payload):
            captured.append((user_id, event_type, payload))
            return []

        monkeypatch.setattr("routes.rewards.achievements.check_achievements", _fake_check)
        resp = http.post(
            "/api/v1/rewards/achievements/secret-event",
            json={"event": "konami_code_entered"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "unlocked_count": 0}
        assert len(captured) == 1
        assert captured[0][1] == "konami_code_entered"

    def test_app_opened_at_3am_event_returns_200(self, db, user_client, monkeypatch):
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        monkeypatch.setattr(
            "routes.rewards.achievements.check_achievements",
            lambda *a, **kw: [],
        )
        resp = http.post(
            "/api/v1/rewards/achievements/secret-event",
            json={"event": "app_opened_at_3am"},
        )
        assert resp.status_code == 200

    def test_invalid_event_returns_422(self, db, user_client):
        """Unknown event_type → 422 from Pydantic Literal validation."""
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = http.post(
            "/api/v1/rewards/achievements/secret-event",
            json={"event": "spoofed_event_type"},
        )
        assert resp.status_code == 422

    def test_rate_limited_after_10_calls(self, db, user_client, monkeypatch):
        """11th call within the hour → 429 rate_limit_exceeded."""
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        monkeypatch.setattr(
            "routes.rewards.achievements.check_achievements",
            lambda *a, **kw: [],
        )
        # 10 OK calls.
        for _ in range(10):
            resp = http.post(
                "/api/v1/rewards/achievements/secret-event",
                json={"event": "konami_code_entered"},
            )
            assert resp.status_code == 200
        # 11th rejected.
        resp = http.post(
            "/api/v1/rewards/achievements/secret-event",
            json={"event": "konami_code_entered"},
        )
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"
