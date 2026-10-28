"""Single-concurrency, JSON-only durable setup worker and recovery CLI."""

import argparse

from redis import Redis
from rq import Worker
from rq.serializers import JSONSerializer

from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory

from .jobs import recover_jobs
from .queue import enqueue_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        enqueue_ids(settings, recover_jobs(create_uow_factory(engine), settings))
        if not args.recover_only:
            with Redis.from_url(settings.redis_url.get_secret_value()) as connection:
                Worker(
                    [settings.queue_name], connection=connection, serializer=JSONSerializer
                ).work()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
