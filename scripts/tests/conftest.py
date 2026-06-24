"""Shared env setup for ``scripts/tests/`` that spin up a migrated DB.

``spin_up_migrated_db`` runs ``alembic upgrade heads`` in a subprocess that
inherits ``os.environ``. The HSP4 migration
(``apply_hsp4_agent_confinement``) requires ``AGENT_READ_PASSWORD`` to create
the ``agent_read`` role. Mirror ``ratis_core/tests/conftest.py`` so script
tests can reuse the same disposable-DB fixture standalone.
"""

from __future__ import annotations

import os

# pragma: allowlist secret
os.environ.setdefault("AGENT_READ_PASSWORD", "test-agent-read-password-32-chars!")
