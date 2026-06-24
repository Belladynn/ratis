"""RetailerReceiptFormat — format de décodage du code-barres caisse par enseigne.

Stocke la longueur attendue et la liste des champs positionnels (JSONB) pour
parser un code-barres de ticket de caisse et en extraire date, heure,
numéro de transaction, caisse et code magasin.

retailer_key est une clé métier stable en minuscules (ex: "intermarche", "monoprix").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class RetailerReceiptFormat(Base):
    """Format de code-barres ticket par enseigne.

    Fields JSONB schema (list of objects):
      [{"name": str, "start": int, "end": int, "format"?: str}, ...]
    """

    __tablename__ = "retailer_receipt_formats"

    retailer_key: Mapped[str] = mapped_column(Text, primary_key=True)
    length: Mapped[int] = mapped_column(Integer, nullable=False)
    fields: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
