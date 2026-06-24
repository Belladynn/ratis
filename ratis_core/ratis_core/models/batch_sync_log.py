from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class BatchSyncLog(Base):
    """One row per batch run — shared across off_sync, prices_sync, osm_sync.

    Batch processes insert a row after each run (success or failure).
    The delta mode reads the last successful run to compute its since_ts.
    """

    __tablename__ = "batch_sync_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(Text, nullable=False)  # 'success' | 'failed'
    rows_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
