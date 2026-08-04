"""Alembic environment for KG_20260727."""

import asyncio

from alembic import context
from app.core.database import Base, is_sqlalchemy_available
from app.modules.member.infrastructure.models import MemberOrmModel  # noqa: F401
from app.modules.models import import_core_models
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config


if is_sqlalchemy_available():
    from app.core.sqlalchemy_mapping import map_core_model_classes

    map_core_model_classes()
else:
    import_core_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        context.config.get_section(context.config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


try:
    context.config
except (AttributeError, NameError):
    pass
else:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())
