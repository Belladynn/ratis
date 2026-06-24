"""HSP3 — tests du module ``admin_ui/human_secret.py`` (M2).

Couvre : hash/verify argon2id, SECRET_STORE create + expiry + lookup,
HMAC de la décision, anti-replay timestamp.

Hermétique : aucune touche DB, aucun appel n8n, tout en mémoire.
"""

from __future__ import annotations

import hmac


def test_hash_and_verify_secret_roundtrip() -> None:
    """``hash_secret`` produit un hash argon2id ; ``verify_secret`` accepte
    le secret original et rejette toute variation."""
    from admin_ui.human_secret import hash_secret, verify_secret

    h = hash_secret("correct horse battery staple")
    # Le hash argon2id commence par "$argon2id$".
    assert h.startswith("$argon2id$"), f"unexpected hash prefix: {h[:30]}"
    assert verify_secret(h, "correct horse battery staple") is True
    assert verify_secret(h, "correct horse battery stapl") is False
    assert verify_secret(h, "") is False


def test_hash_secret_is_non_deterministic() -> None:
    """Deux hash du même secret diffèrent (salt aléatoire intégré)."""
    from admin_ui.human_secret import hash_secret

    a = hash_secret("same input")
    b = hash_secret("same input")
    assert a != b


def test_secret_store_create_and_get(monkeypatch) -> None:
    """Un appel à ``create_session(secret)`` retourne un session_id
    aléatoire de >=32 chars URL-safe ; ``get_session_secret`` retourne
    le secret tant que la session est valide."""
    import admin_ui.human_secret as hs

    hs._SECRET_STORE.clear()
    sid = hs.create_session("the-secret")
    assert isinstance(sid, str)
    assert len(sid) >= 32
    # url-safe : pas de '+' ni '/' ni '='
    assert all(c.isalnum() or c in "-_" for c in sid)
    assert hs.get_session_secret(sid) == "the-secret"


def test_secret_store_expiry(monkeypatch) -> None:
    """Une session créée puis « avancée dans le temps » au-delà du TTL
    n'est plus retrouvable."""
    import admin_ui.human_secret as hs

    hs._SECRET_STORE.clear()
    fake_now = [1000.0]
    monkeypatch.setattr(hs, "_monotonic", lambda: fake_now[0])
    sid = hs.create_session("s")
    fake_now[0] = 1000.0 + hs.SESSION_TTL_SECONDS + 1
    assert hs.get_session_secret(sid) is None


def test_compute_decision_hmac_matches_browser_payload() -> None:
    """``compute_decision_hmac(secret, body_bytes)`` produit le HMAC-SHA256
    en hex que le JS browser calculerait via WebCrypto."""
    import hashlib

    from admin_ui.human_secret import compute_decision_hmac

    secret = "browser-secret-32-chars-long-xxxx"
    body = b'{"submission_id":"abc","decision":"approve","challenge":"dit_cab 100","ts":1700000000}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert compute_decision_hmac(secret, body) == expected


def test_verify_decision_hmac_constant_time_match() -> None:
    """``verify_decision_hmac`` accepte un HMAC valide via constant-time."""
    from admin_ui.human_secret import compute_decision_hmac, verify_decision_hmac

    secret = "browser-secret"
    body = b'{"x":1}'
    mac = compute_decision_hmac(secret, body)
    assert verify_decision_hmac(secret, body, mac) is True
    assert verify_decision_hmac(secret, body, mac[:-1] + "0") is False
    assert verify_decision_hmac(secret, b'{"x":2}', mac) is False


def test_verify_decision_ts_within_window_accepts(monkeypatch) -> None:
    """Un ts dans la fenêtre ±60s est accepté ; hors fenêtre rejeté."""
    import admin_ui.human_secret as hs

    monkeypatch.setattr(hs.time, "time", lambda: 1_700_000_000.0)
    assert hs.verify_decision_ts(1_700_000_000) is True
    assert hs.verify_decision_ts(1_699_999_945) is True  # -55s
    assert hs.verify_decision_ts(1_700_000_055) is True  # +55s


def test_verify_decision_ts_stale_rejects(monkeypatch) -> None:
    """Hors fenêtre ±60s, le ts est rejeté (anti-replay)."""
    import admin_ui.human_secret as hs

    monkeypatch.setattr(hs.time, "time", lambda: 1_700_000_000.0)
    assert hs.verify_decision_ts(1_699_999_900) is False  # -100s
    assert hs.verify_decision_ts(1_700_000_100) is False  # +100s
