"""HSP3 — M2 : secret de session humain (argon2id + HMAC anti-replay).

Le secret ``HUMAN_APPROVAL_SECRET`` est saisi par l'opérateur dans une
textarea de la page ``/admin/ui/db-approvals/unlock``. Il vit :

- en sessionStorage du browser de l'opérateur, le temps de la session ;
- en RAM process PA, dans ``_SECRET_STORE`` keyé par session_id (cookie
  HttpOnly ``human_approval_session``) ;
- en hash argon2id dans ``app_settings.human_approval.argon2_hash`` — la
  base ne stocke jamais le secret en clair.

Le store en RAM s'efface au redémarrage du process : l'opérateur doit
re-unlock après chaque restart PA (rare en prod). Le hash en DB peut
être rotaté sans redéploiement (acte humain via script).

Chaque décision (approve/reject) est HMAC-signée côté browser
(WebCrypto) avec ce secret et incluse en header ``X-Human-Mac``. Le
serveur recompute le HMAC depuis le secret en RAM (jamais re-lu en
DB en chemin chaud) et compare en constant-time.

Anti-replay : le body POSTé inclut un ``ts`` epoch-seconds qui doit être
dans ±60s de ``time.time()`` ; un attaquant qui rejouerait un body
intercepté est borné à 60s.

Source de vérité : design §M2.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from time import monotonic as _monotonic

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, HTTPException, status

# Cookie SP6 reste celui d'``admin_session`` ; HSP3 ajoute un cookie distinct.
HUMAN_COOKIE_NAME = "human_approval_session"

# 12h fenêtre — assez pour une journée de travail, pas plus.
SESSION_TTL_SECONDS = 12 * 60 * 60

# Anti-replay : ±60s sur le ts du body POSTé.
TS_TOLERANCE_SECONDS = 60

# Argon2id paramètres — argon2-cffi defaults sont raisonnables (time_cost=3,
# memory_cost=64 MiB, parallelism=1). Le hash est saisi une fois par
# l'opérateur au unlock — coût ~50ms acceptable.
_HASHER = PasswordHasher()

# Store en RAM process. Keyé par session_id (random URL-safe 256-bit).
# Valeur = (secret_clair, expiry_monotonic). Restart process = perte des
# sessions = re-unlock.
_SECRET_STORE: dict[str, tuple[str, float]] = {}


def hash_secret(secret: str) -> str:
    """Hash argon2id du secret. Salt aléatoire interne — non déterministe."""
    return _HASHER.hash(secret)


def verify_secret(stored_hash: str, presented: str) -> bool:
    """Vérifie ``presented`` contre ``stored_hash``. Constant-time.

    Renvoie False sur tout échec (mismatch ou hash invalide). Jamais
    raise — l'appelant route un échec en 401.
    """
    if not stored_hash or not presented:
        return False
    try:
        _HASHER.verify(stored_hash, presented)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_session(secret: str) -> str:
    """Crée une session : génère un session_id URL-safe, stocke
    ``(secret, expiry)`` en RAM, renvoie le session_id."""
    sid = secrets.token_urlsafe(32)
    _SECRET_STORE[sid] = (secret, _monotonic() + SESSION_TTL_SECONDS)
    return sid


def get_session_secret(session_id: str | None) -> str | None:
    """Renvoie le secret en RAM si la session est valide, sinon None.

    Le ``None`` est silencieux côté caller — la route mappe à 401.
    """
    if not session_id:
        return None
    entry = _SECRET_STORE.get(session_id)
    if entry is None:
        return None
    secret, expiry = entry
    if _monotonic() > expiry:
        _SECRET_STORE.pop(session_id, None)
        return None
    return secret


def compute_decision_hmac(secret: str, body_bytes: bytes) -> str:
    """HMAC-SHA256 hex sur les bytes du body. Identique au calcul JS
    browser (``crypto.subtle.sign('HMAC', key, body)``)."""
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def verify_decision_hmac(secret: str, body_bytes: bytes, presented_mac: str) -> bool:
    """Constant-time : recompute + compare. Renvoie False sur tout échec."""
    if not secret or not presented_mac:
        return False
    expected = compute_decision_hmac(secret, body_bytes)
    return hmac.compare_digest(expected, presented_mac)


def verify_decision_ts(ts: int) -> bool:
    """Anti-replay : ``ts`` epoch-seconds doit être dans ±TS_TOLERANCE_SECONDS."""
    try:
        return abs(time.time() - int(ts)) <= TS_TOLERANCE_SECONDS
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class HumanSession:
    """Bound human-approval session pour une requête."""

    secret: str


def get_human_session(
    human_approval_session: str | None = Cookie(default=None),
) -> HumanSession:
    """FastAPI dep — extrait le cookie, valide la session, renvoie
    ``HumanSession(secret=...)``. 401 ``secret_session_expired`` si absent
    ou expiré.

    Le caller enchaîne ``get_admin_session`` + ``get_human_session`` :
    les deux sont requis pour ``/approve``.
    """
    secret = get_session_secret(human_approval_session)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="secret_session_expired",
        )
    return HumanSession(secret=secret)
