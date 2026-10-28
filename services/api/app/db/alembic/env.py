from alembic import context
from sqlalchemy.engine import Connection


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    from services.api.app.config import Settings
    from services.api.app.db import create_database_engine

    engine = create_database_engine(Settings.from_env())
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
