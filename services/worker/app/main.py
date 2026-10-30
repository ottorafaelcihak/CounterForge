"""Single-concurrency, JSON-only durable setup worker and recovery CLI."""

import argparse
import threading

from redis import Redis
from rq import Worker
from rq.serializers import JSONSerializer

from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory
from services.api.app.db.readiness import WORKER_HEARTBEAT_TTL_SECONDS, worker_heartbeat_key

from .experiments import recover_experiments
from .jobs import recover_jobs
from .queue import enqueue_experiments, enqueue_ids

HEARTBEAT_INTERVAL_SECONDS = 5


def publish_heartbeat(settings: Settings, connection: Redis | None = None) -> bool:
    """Refresh the exact queue heartbeat. Value is a liveness marker only."""
    try:
        if connection is None:
            with Redis.from_url(
                settings.redis_url.get_secret_value(),
                socket_connect_timeout=2,
                socket_timeout=2,
                retry_on_timeout=False,
            ) as client:
                return publish_heartbeat(settings, client)
        return bool(
            connection.set(
                worker_heartbeat_key(settings.queue_name), "alive", ex=WORKER_HEARTBEAT_TTL_SECONDS
            )
        )
    except Exception:
        return False


def _heartbeat_loop(settings: Settings, stop: threading.Event) -> None:
    with Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=False,
    ) as connection:
        while not stop.is_set():
            publish_heartbeat(settings, connection)
            stop.wait(HEARTBEAT_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    stop = threading.Event()
    try:
        enqueue_ids(settings, recover_jobs(create_uow_factory(engine), settings))
        enqueue_experiments(settings, recover_experiments(create_uow_factory(engine), settings))
        if not args.recover_only:
            # Idle workers heartbeat even with no claimed job; freshness is
            # bounded by the key TTL, so a dead worker stops reporting ready.
            publish_heartbeat(settings)
            thread = threading.Thread(
                target=_heartbeat_loop, args=(settings, stop), daemon=True, name="heartbeat"
            )
            thread.start()
            try:
                with Redis.from_url(settings.redis_url.get_secret_value()) as connection:
                    Worker(
                        [settings.queue_name], connection=connection, serializer=JSONSerializer
                    ).work()
            finally:
                stop.set()
                thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS)
                try:
                    with Redis.from_url(
                        settings.redis_url.get_secret_value(),
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    ) as connection:
                        connection.delete(worker_heartbeat_key(settings.queue_name))
                except Exception:
                    pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
