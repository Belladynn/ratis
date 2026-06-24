"""AppSettings — table app_settings.

Une ligne par section de configuration (ex: 'rewards', 'gamification').
Éditables via endpoints admin sans redémarrage des services.
Fallback : ratis_settings.json si table vide ou inaccessible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    section: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
