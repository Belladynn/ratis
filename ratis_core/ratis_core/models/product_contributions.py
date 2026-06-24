"""``product_contributions`` ORM mirror.

Audit / forensics table for user-driven product field fills (Phase C-5
of the missions sprint). Each accepted POST to
``/api/v1/product/{ean}/contribute`` lands one row here :

* ``status='applied'`` — the products row was updated in place because
  the target field was NULL/empty. Mission credit fired
  (``trigger_action("fill_product_field", qualifier=None, ...)``).
* ``status='pending_review'`` — the target field already had a value ;
  the contribution is parked for admin review. No mission credit.
* ``status='rejected'`` — admin marked the contribution invalid
  (admin endpoints out of scope C-5, follow-up PR).

``value_text`` vs ``value_array`` :

* Scalar fields (``brands`` / ``name``) live in ``value_text``.
* OFF tag arrays (``categories_tags`` / ``labels_tags``) live in
  ``value_array``.

The ``ck_contributions_value_shape`` CHECK enforces exactly one of the
two columns is populated, matching the declared field family.

``user_id`` is ``ON DELETE SET NULL`` so the contribution row keeps
its audit trail value after a user is hard-deleted (RGPD anonymization
severs the link, not the row).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class ProductContribution(Base):
    __tablename__ = "product_contributions"
    __table_args__ = (
        CheckConstraint(
            "field IN ('brands', 'categories_tags', 'labels_tags', 'name')",
            name="ck_contributions_field",
        ),
        CheckConstraint(
            "("
            "    field IN ('brands', 'name')"
            "    AND value_text IS NOT NULL"
            "    AND value_array IS NULL"
            ") OR ("
            "    field IN ('categories_tags', 'labels_tags')"
            "    AND value_array IS NOT NULL"
            "    AND value_text IS NULL"
            ")",
            name="ck_contributions_value_shape",
        ),
        CheckConstraint(
            "status IN ('applied', 'rejected', 'pending_review')",
            name="ck_contributions_status",
        ),
        Index(
            "idx_product_contributions_user_ean",
            "user_id",
            "product_ean",
        ),
        Index(
            "idx_product_contributions_status_created",
            "status",
            "created_at",
            postgresql_where=text("status = 'pending_review'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_ean: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_array: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'applied'"))
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
