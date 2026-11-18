from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings, to_sqlalchemy_url
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    # Tests inject their own URL via config.attributes; otherwise MIGRATION_DATABASE_URL (owner),
    # falling back to DATABASE_URL.
    override = config.attributes.get("url")
    return to_sqlalchemy_url(override) if override else get_settings().migration_sqlalchemy_url


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
