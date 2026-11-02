from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from qka_shared.models import Base

FORBIDDEN = {
    "qka_014_frontend_it",
    "qka_ad_sync_test",
}
ALLOWED_PREFIX = "qka_next"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = os.environ.get("QKA_DATABASE_URL", config.get_main_option("sqlalchemy.url") or "")
if any(name in url for name in FORBIDDEN) or ALLOWED_PREFIX not in url.lower():
    raise RuntimeError("Refusing to run Alembic against a non qka_next copy database.")

config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
