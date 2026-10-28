from collections.abc import Callable
from types import TracebackType

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine, RootTransaction

from ..config import Settings
from .repositories import BackgroundJobs
from .tables import Tables, load_tables


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )


class UnitOfWork:
    """One shared API/worker transaction, explicit commit, rollback on exit.

    Use ``with factory() as uow``; all repositories and direct Core queries use
    uow.connection. Do not commit through that connection; call uow.commit().
    A UOW instance is single-use and must not be shared between threads/tasks.
    """

    connection: Connection
    background_jobs: BackgroundJobs
    _transaction: RootTransaction

    def __init__(self, engine: Engine, tables: Tables) -> None:
        self.engine = engine
        self.tables = tables
        self._entered = False

    def __enter__(self) -> "UnitOfWork":
        if self._entered:
            raise RuntimeError("UnitOfWork is single-use")
        self._entered = True
        self.connection = self.engine.connect()
        self._transaction = self.connection.begin()
        self.background_jobs = BackgroundJobs(self.connection, self.tables.background_jobs)
        return self

    def commit(self) -> None:
        self._transaction.commit()

    def rollback(self) -> None:
        self._transaction.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._transaction.is_active:
                self._transaction.rollback()
        finally:
            self.connection.close()


def create_uow_factory(engine: Engine) -> Callable[[], UnitOfWork]:
    with engine.connect() as connection:
        tables = load_tables(connection)
    return lambda: UnitOfWork(engine, tables)
