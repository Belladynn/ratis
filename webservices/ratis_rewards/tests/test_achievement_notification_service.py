"""Tests for ``services/achievement_notification_service.py``.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Notification & UI flow.

The service maps catalog ``rarity`` to a 4-tier UX gradient:

* ``terracotta``/``bronze``/``copper``/``silver``  → toast only (no modal, no push)
* ``gold``/``emerald``                              → toast + in-app modal (no push)
* ``sapphire``/``ruby``                             → toast + modal + push (rate-limited 1h)
* ``crystal``/``diamond``                           → toast + modal + push (NOT rate-limited)
* ``diamond``                                       → ``has_bespoke=True`` (frontend swaps in a custom animation)

The actual ``notifier_client.send`` call is patched out in every test so that
no HTTP round-trip happens — we only verify the kwargs the service passes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_send(monkeypatch):
    """Patch ``notifier_client.send`` and capture every call as a list of kwargs."""
    from services import achievement_notification_service as svc

    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(svc.notifier_client, "send", _capture)
    return captured


# ---------------------------------------------------------------------------
# Rarity → (modal, push, rate-limit) mapping
# ---------------------------------------------------------------------------


class TestRarityTierMapping:
    def test_terracotta_no_modal_no_push(self, captured_send, db, achievement_factory):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_ter", rarity="terracotta", cab_reward=20)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert len(captured_send) == 1
        sent = captured_send[0]
        assert sent["payload"]["show_modal"] is False
        assert sent["visible_push"] is False
        assert sent["push_rate_limit_seconds"] == 0

    def test_copper_no_modal_no_push(self, captured_send, db, achievement_factory):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_cop", rarity="copper", cab_reward=30)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert captured_send[0]["payload"]["show_modal"] is False
        assert captured_send[0]["visible_push"] is False

    def test_gold_modal_no_push(self, captured_send, db, achievement_factory):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_gold", rarity="gold", cab_reward=100)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert captured_send[0]["payload"]["show_modal"] is True
        assert captured_send[0]["visible_push"] is False

    def test_emerald_modal_no_push(self, captured_send, db, achievement_factory):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_em", rarity="emerald", cab_reward=150)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert captured_send[0]["payload"]["show_modal"] is True
        assert captured_send[0]["visible_push"] is False

    def test_sapphire_modal_and_push_rate_limited(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_sa", rarity="sapphire", cab_reward=250)
        svc.notify_achievement_unlocked(uuid4(), ach)

        sent = captured_send[0]
        assert sent["payload"]["show_modal"] is True
        assert sent["visible_push"] is True
        assert sent["push_rate_limit_seconds"] == 3600

    def test_ruby_modal_and_push_rate_limited(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_ru", rarity="ruby", cab_reward=500)
        svc.notify_achievement_unlocked(uuid4(), ach)

        sent = captured_send[0]
        assert sent["visible_push"] is True
        assert sent["push_rate_limit_seconds"] == 3600

    def test_crystal_modal_push_no_rate_limit(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_cr", rarity="crystal", cab_reward=800)
        svc.notify_achievement_unlocked(uuid4(), ach)

        sent = captured_send[0]
        assert sent["payload"]["show_modal"] is True
        assert sent["visible_push"] is True
        assert sent["push_rate_limit_seconds"] == 0
        # Crystal is NOT bespoke — only diamond is.
        assert sent["payload"]["has_bespoke"] is False

    def test_diamond_modal_push_no_rate_limit_has_bespoke(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_di", rarity="diamond", cab_reward=1200)
        svc.notify_achievement_unlocked(uuid4(), ach)

        sent = captured_send[0]
        assert sent["payload"]["show_modal"] is True
        assert sent["visible_push"] is True
        assert sent["push_rate_limit_seconds"] == 0
        assert sent["payload"]["has_bespoke"] is True


# ---------------------------------------------------------------------------
# Payload contract — frontend reads these fields
# ---------------------------------------------------------------------------


class TestPayloadShape:
    def test_payload_contains_all_essential_fields(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(
            code="t_full",
            label="Demo",
            description="Demo desc",
            icon="trophy",
            rarity="gold",
            category="volume",
            cab_reward=100,
        )
        svc.notify_achievement_unlocked(uuid4(), ach)

        payload = captured_send[0]["payload"]
        for key in (
            "achievement_id",
            "code",
            "label",
            "description",
            "rarity",
            "category",
            "icon",
            "cab_granted",
            "show_modal",
            "has_bespoke",
            "sound_intensity",
        ):
            assert key in payload, f"missing payload key {key!r}"

        assert payload["code"] == "t_full"
        assert payload["label"] == "Demo"
        assert payload["rarity"] == "gold"
        assert payload["cab_granted"] == 100
        # achievement_id is the catalog UUID stringified — frontend needs str.
        assert isinstance(payload["achievement_id"], str)

    def test_notif_type_is_achievement_unlocked(
        self,
        captured_send,
        db,
        achievement_factory,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code="t_nt", rarity="bronze", cab_reward=20)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert captured_send[0]["notif_type"] == "achievement_unlocked"

    def test_push_title_and_body_set(self, captured_send, db, achievement_factory):
        from services import achievement_notification_service as svc

        ach = achievement_factory(
            code="t_push",
            label="Légende",
            rarity="diamond",
            cab_reward=1200,
        )
        svc.notify_achievement_unlocked(uuid4(), ach)

        sent = captured_send[0]
        assert "Diamant" in sent["push_title"]  # RARITY_LABELS['diamond'] = 'Diamant'
        assert "Légende" in sent["push_body"]
        assert "1200" in sent["push_body"] or "1200 CAB" in sent["push_body"]


# ---------------------------------------------------------------------------
# Sound intensity (0..3 mapped from rarity)
# ---------------------------------------------------------------------------


class TestSoundIntensity:
    @pytest.mark.parametrize(
        "rarity,expected",
        [
            ("terracotta", 0),
            ("bronze", 0),
            ("copper", 0),
            ("silver", 1),
            ("gold", 1),
            ("emerald", 2),
            ("sapphire", 2),
            ("ruby", 3),
            ("crystal", 3),
            ("diamond", 3),
        ],
    )
    def test_sound_intensity_per_rarity(
        self,
        captured_send,
        db,
        achievement_factory,
        rarity,
        expected,
    ):
        from services import achievement_notification_service as svc

        ach = achievement_factory(code=f"t_si_{rarity}", rarity=rarity, cab_reward=50)
        svc.notify_achievement_unlocked(uuid4(), ach)

        assert captured_send[0]["payload"]["sound_intensity"] == expected


# ---------------------------------------------------------------------------
# Fire-and-forget — never raises
# ---------------------------------------------------------------------------


class TestFireAndForget:
    def test_swallows_send_exception(self, monkeypatch, db, achievement_factory):
        from services import achievement_notification_service as svc

        def _boom(**kwargs):
            raise RuntimeError("notifier exploded")

        monkeypatch.setattr(svc.notifier_client, "send", _boom)
        ach = achievement_factory(code="t_boom", rarity="gold", cab_reward=100)

        # Must NOT raise — the unlock path can never crash because of a notif fail.
        svc.notify_achievement_unlocked(uuid4(), ach)
