#!/usr/bin/env python3
"""HMAC-signing + payload-formatting proxy for GlitchTip → Hermes.

Why both : GlitchTip POSTs raw nested JSON (text+attachments[]+fields[]).
Hermes templating (`{dot.notation}`) seems not to substitute on nested arrays
when --deliver-only is set, so the user sees raw `{attachments.0.title}` in
Telegram. Solution : flatten + format here, send to Hermes as a single
top-level `text` key, keep Hermes template trivial : `{text}`.

Env:
  HERMES_WEBHOOK_SECRET — shared secret matching the Hermes subscription
  HERMES_UPSTREAM       — e.g. http://ratis-hermes:8644
  PORT                  — listen port (default 8645)
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ["HERMES_WEBHOOK_SECRET"].encode()
UPSTREAM = os.environ.get("HERMES_UPSTREAM", "http://ratis-hermes:8644").rstrip("/")


def format_glitchtip(payload: dict) -> str:
    """Turn GlitchTip's text+attachments[] structure into a one-line Markdown
    blob suitable for Telegram delivery."""
    attachments = payload.get("attachments") or [{}]
    att = attachments[0]
    fields = {f.get("title"): f.get("value") for f in (att.get("fields") or [])}

    title = att.get("title") or payload.get("text") or "(no title)"
    link = att.get("title_link") or ""
    project = fields.get("Project", "unknown")
    env = fields.get("Environment", "?")
    release = fields.get("Release", "?")
    host = fields.get("Server Name", "?")

    lines = [
        f"🚨 *GlitchTip — {project}*",
        "",
        title,
    ]
    if link:
        lines += ["", f"🔗 {link}"]
    lines += ["", f"`env={env}` · `release={release}` · `host={host}`"]
    return "\n".join(lines)


class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        # Try to flatten GlitchTip payload → {"text": "<formatted>"}.
        # On parse failure, passthrough (Hermes templating handles it as raw).
        try:
            parsed = json.loads(raw)
            formatted = format_glitchtip(parsed)
            body = json.dumps({"text": formatted, "_raw": parsed}).encode()
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            sys.stderr.write(f"[proxy] passthrough (parse error: {e})\n")
            body = raw

        sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"{UPSTREAM}{self.path}",
            data=body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.send_response(resp.status); self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code); self.end_headers(); self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502); self.end_headers(); self.wfile.write(str(e).encode())

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8645"))
    print(f"[proxy] HMAC signer + formatter on :{port} → {UPSTREAM}", flush=True)
    HTTPServer(("0.0.0.0", port), ProxyHandler).serve_forever()
