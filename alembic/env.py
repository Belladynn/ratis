import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

# Import Base AND all models so they are registered in the metadata.
# Without this import, autogenerate sees no tables.
from ratis_core.database import Base, make_engine
import ratis_core.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables non gérées par des modèles SQLAlchemy :
# - datafix infra (présentes dans schema.sql)
# - spatial_ref_sys : table système créée par l'extension PostGIS
_EXCLUDED_TABLES = {"datafix_logs", "datafix_backup", "spatial_ref_sys"}


def include_object(object, name, type_, reflected, compare_to):
    """
    Exclude from autogenerate:
    - Tables not managed by SQLAlchemy models (datafix infra).
    - Indexes: declared in schema.sql for performance, not in models.
      Alembic would otherwise try to drop every index it doesn't see in metadata.
    """
    if type_ == "table" and name in _EXCLUDED_TABLES:
        return False
    if type_ == "index":
        return False
    return True


def get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Normalise to psycopg v3 scheme — postgresql:// → postgresql+psycopg://
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg" + url[len("postgresql"):]
    return url


def run_migrations_offline() -> None:
    """Offline mode: generates SQL without a real connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: real connection to PostgreSQL."""
    connectable = make_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
