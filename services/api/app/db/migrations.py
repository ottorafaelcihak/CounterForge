"""Run the same Alembic history from the CLI or an existing transaction."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

MIGRATIONS = Path(__file__).with_name("alembic")


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def upgrade_database(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def main() -> None:
    from ..config import Settings
    from .uow import create_database_engine

    engine = create_database_engine(Settings.from_env())
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
