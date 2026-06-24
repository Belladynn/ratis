"""HSP3 — M1 : challenge inverted decision flow.

Le challenge est la *seule* action d'approbation côté UI (M1 design) —
remplace le bouton « Approuver » de SP6. Forme :

    challenge = "<6 derniers chars du nom procedure> <primary>"

où ``<primary>`` est déterminé par ``direction`` du manifest HSP1 :

| direction         | primary                                            |
|-------------------|----------------------------------------------------|
| credit, debit     | args.amount_cents                                  |
| link, unlink      | args.<entity>_id (short = 8 derniers chars UUID)   |
| fix, set          | args.row_id                                        |

Validation : insensible casse + espaces, constant-time
(``hmac.compare_digest`` sur la forme normalisée).

Pure module — aucune I/O, testable en isolation. L'intégration UI vit
dans ``webservices/ratis_product_analyser/admin_ui/db_approvals.py``
(quota d'essais, lockout, incrément ``payload.failed_confirms``).
"""

from __future__ import annotations

import hmac

from ratis_core.db_procedure_manifest import ProcedureManifest


class ChallengeError(ValueError):
    """Raised when the manifest+args don't provide a computable primary."""


# Combien des derniers caractères du nom de procédure on utilise.
LAST_CHARS = 6

# Pour les UUIDs : on raccourcit aux 8 derniers chars (lisible humain,
# unique en pratique sur le volume d'approbations en parallèle).
UUID_SHORT_CHARS = 8


def _short_uuid(s: str) -> str:
    """UUID-string short form : remove dashes, keep last UUID_SHORT_CHARS."""
    stripped = str(s).replace("-", "")
    return stripped[-UUID_SHORT_CHARS:]


def _primary_for(manifest: ProcedureManifest, args: dict) -> str:
    """Détermine la primary string à inclure dans le challenge.

    Raises ChallengeError si l'arg attendu manque (l'UI flash un message
    'challenge non calculable, contact ops').
    """
    direction = manifest.direction
    if direction in ("credit", "debit"):
        if "amount_cents" not in args:
            raise ChallengeError(f"direction={direction} requires args.amount_cents")
        return str(args["amount_cents"])
    if direction in ("link", "unlink"):
        # Heuristique : premier *_id qui correspond à une table de affects.
        # On scanne affects en ordre déclaré : la première colonne *_id
        # de args qui match la singular form (table='scans' → 'scan_id')
        # est l'entité touchée principale.
        for affect in manifest.affects:
            singular = affect.table.rstrip("s")
            key = f"{singular}_id"
            if key in args:
                return _short_uuid(args[key])
        # Fallback : premier *_id de args.
        for k, v in args.items():
            if k.endswith("_id"):
                return _short_uuid(v)
        raise ChallengeError(f"direction={direction} requires at least one *_id in args")
    if direction in ("fix", "set"):
        if "row_id" not in args:
            raise ChallengeError(f"direction={direction} requires args.row_id")
        return str(args["row_id"])
    raise ChallengeError(f"unsupported direction: {direction}")  # pragma: no cover


def compute_challenge(
    procedure_name: str,
    manifest: ProcedureManifest,
    args: dict,
) -> str:
    """Compute the deterministic challenge string for a proposal.

    Raises ChallengeError if the primary cannot be derived (manifest+args
    mismatch).
    """
    suffix = procedure_name[-LAST_CHARS:] if len(procedure_name) > LAST_CHARS else procedure_name
    primary = _primary_for(manifest, args)
    return f"{suffix} {primary}"


def normalise(s: str) -> str:
    """Strip + lowercase + remove all spaces — used both server-side
    (validation) and reflected in the UI hint."""
    return (s or "").strip().lower().replace(" ", "")


def verify_challenge(submitted: str, expected: str) -> bool:
    """Constant-time comparison of normalised submitted vs expected."""
    return hmac.compare_digest(normalise(submitted), normalise(expected))
