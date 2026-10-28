"""Single local RQ worker. Durable operation dispatch belongs to later tasks."""

from redis import Redis
from rq import Worker

from services.api.app.config import Settings


def main() -> None:
    settings = Settings.from_env()
    with Redis.from_url(settings.redis_url.get_secret_value()) as connection:
        Worker(["counterforge"], connection=connection).work()


if __name__ == "__main__":
    main()
