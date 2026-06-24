"""mitmproxy addon — capture des réponses d'une session de navigation manuelle.

Phase 1 de l'outil de capture de prix « drive ». L'opérateur navigue
lui-même, manuellement, sur les sites drive ; cet addon enregistre chaque
réponse porteuse de données — JSON (API modernes) comme HTML/XML (sites
server-rendered, ex. Leclerc Drive en .aspx) — que le site renvoie au
navigateur. Aucune automatisation, aucun crawl, aucun contournement
d'anti-bot — simple enregistrement passif du trafic de l'opérateur.

Phase 1 = capture brute, zéro interprétation (« on prend tout »). Les
parsers par enseigne viendront en Phase 2 et pourront se brancher ici
via le hook `response` (cf README § Phase 2).

Usage :
    uv tool install mitmproxy            # une fois
    cd tools/drive-capture
    mitmdump -s capture_addon.py         # écoute sur localhost:8080

Voir README.md pour le setup complet (certificat CA, proxy navigateur).
"""
from __future__ import annotations

import collections
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

# Répertoire de l'addon — sert d'ancrage pour `captures/` et `domains.txt`.
_ADDON_DIR = pathlib.Path(__file__).resolve().parent

# Une session = un run de `mitmdump`. Horodatée à la charge de l'addon.
_SESSION = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
_OUT_DIR = _ADDON_DIR / "captures" / _SESSION

# Allowlist optionnelle d'hôtes. Si `domains.txt` existe et contient des
# entrées, seules les réponses dont l'hôte contient l'une d'elles sont
# capturées. Absent ou vide → on capture TOUT (défaut Phase 1).
_DOMAINS_FILE = _ADDON_DIR / "domains.txt"


def _load_allowlist() -> list[str]:
    if not _DOMAINS_FILE.exists():
        return []
    entries: list[str] = []
    for line in _DOMAINS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line.lower())
    return entries


_ALLOWLIST = _load_allowlist()

# Décompte des réponses capturées par hôte — alimente le résumé de fin
# de session (hook `done`).
_HOST_COUNTS: collections.Counter[str] = collections.Counter()


# Types de contenu capturés : données structurées renvoyées par les
# drives. JSON (API modernes) + HTML/XML (sites server-rendered, ex.
# Leclerc Drive en .aspx). On ignore le binaire et les assets (images,
# css, js, fonts) qui ne portent pas de prix.
_CAPTURABLE_TYPES = ("json", "html", "xml")


def _content_type(headers: Any) -> str:
    return headers.get("content-type", "").lower()


def _is_capturable(content_type: str) -> bool:
    """True si le content-type porte des données exploitables (vs asset)."""
    return any(t in content_type for t in _CAPTURABLE_TYPES)


def _safe_filename(host: str) -> str:
    """Nom de fichier sûr dérivé d'un hôte (un .ndjson par hôte)."""
    return "".join(c if (c.isalnum() or c in ".-_") else "_" for c in host)


def _parse_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def running() -> None:
    """Hook mitmproxy — le proxy est démarré. Affiche l'état du filtre.

    Rend visible l'allowlist active : évite le piège « domains.txt resté
    sur la mauvaise enseigne → 0 capture, en silence ».
    """
    if _ALLOWLIST:
        print(f"[drive-capture] Allowlist domains.txt : {', '.join(_ALLOWLIST)}")
        print("[drive-capture] → seuls les hôtes contenant ces fragments "
              "sont capturés.")
    else:
        print("[drive-capture] Pas de domains.txt — capture de TOUS les hôtes.")
    print(f"[drive-capture] Session : {_SESSION}")


def response(flow) -> None:
    """Hook mitmproxy — appelé pour chaque réponse HTTP reçue.

    Phase 2 : c'est ici qu'un parser par enseigne pourra être branché
    (dispatch sur `host` → extraction structurée) en plus du dump brut.

    Capture les requêtes ET les réponses :
    - Réponses JSON/HTML/XML (données exploitables)
    - Corps des requêtes POST/PUT/PATCH (JSON, form-encoded, ou brut)
      → permet de voir les payloads envoyés au site (ex. Auchan /journey/update)
    - En-têtes clés de la requête (Cookie, Content-Type, …)
    """
    import urllib.parse as _urlparse

    resp = flow.response
    if resp is None:
        return

    host = flow.request.pretty_host
    if _ALLOWLIST and not any(d in host.lower() for d in _ALLOWLIST):
        return

    resp_content_type = _content_type(resp.headers)
    req_method = flow.request.method.upper()
    has_req_body = bool(flow.request.content) and req_method in ("POST", "PUT", "PATCH")

    # Capturer si : réponse exploitable OU requête avec corps
    if not _is_capturable(resp_content_type) and not has_req_body:
        return

    # --- Corps de la réponse ------------------------------------------------
    body_text: str | None = None
    body_json: Any = None
    if _is_capturable(resp_content_type):
        # get_text() décode automatiquement gzip/brotli (content-encoding).
        body_text = resp.get_text(strict=False)
        # On tente le parse JSON quel que soit le content-type : certains
        # endpoints (ex. les .aspx de Leclerc Drive) renvoient du JSON parfois
        # étiqueté text/html. Si ça parse → response_json ; un vrai HTML/XML
        # ne parsera pas et retombera dans response_text.
        body_json = _parse_json(body_text)

    # --- Corps de la requête ------------------------------------------------
    req_json: Any = None
    req_form: dict | None = None
    req_text: str | None = None
    req_ct = _content_type(flow.request.headers)
    if has_req_body:
        raw = flow.request.get_text(strict=False)
        if "json" in req_ct:
            req_json = _parse_json(raw)
            if req_json is None:
                req_text = raw  # fallback si JSON malformé
        elif "application/x-www-form-urlencoded" in req_ct:
            try:
                req_form = dict(_urlparse.parse_qsl(raw or "", keep_blank_values=True))
            except Exception:
                req_text = raw
        else:
            req_text = raw  # texte brut (multipart, autre)

    # --- En-têtes de la requête (utiles pour debug de session) --------------
    _REQ_HEADERS_OF_INTEREST = (
        "content-type", "authorization", "cookie",
        "x-requested-with", "origin", "referer",
    )
    req_headers: dict[str, str] = {
        h: flow.request.headers[h]
        for h in _REQ_HEADERS_OF_INTEREST
        if h in flow.request.headers
    }

    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "host": host,
        "method": req_method,
        "url": flow.request.pretty_url,
        "status": resp.status_code,
        "content_type": resp_content_type,
        "request_headers": req_headers or None,
        "request_json": req_json,
        "request_form": req_form,
        "request_text": req_text,
        # JSON parsé en objet si possible ; sinon (HTML/XML, ou JSON non
        # parsable) le corps brut est conservé dans response_text.
        "response_json": body_json,
        "response_text": None if body_json is not None else body_text,
    }

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OUT_DIR / f"{_safe_filename(host)}.ndjson"
    with out_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _HOST_COUNTS[host] += 1


def done() -> None:
    """Hook mitmproxy — arrêt du proxy (Ctrl-C). Résumé de la session.

    Affiche le décompte des réponses capturées par hôte. Si rien n'a été
    capturé, le signale explicitement (allowlist trop stricte, ou proxy
    navigateur inactif) — au lieu d'un échec silencieux.
    """
    print(f"\n[drive-capture] === Résumé session {_SESSION} ===")
    if not _HOST_COUNTS:
        print("[drive-capture] ⚠ AUCUNE réponse capturée — domains.txt "
              "pointe-t-il la bonne enseigne ? le proxy navigateur "
              "est-il actif ?")
        return
    for host, count in _HOST_COUNTS.most_common():
        print(f"[drive-capture]   {count:6}  {host}")
    print(f"[drive-capture] → {_OUT_DIR}")
