"""Best-effort RQ notification; PostgreSQL remains the durable source of intent."""

from uuid import UUID, uuid4

from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer

from services.api.app.config import Settings


def enqueue_ids(settings: Settings, job_ids: list[UUID]) -> int:
    dispatched = 0
    try:
        with Redis.from_url(
            settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
        ) as connection:
            queue = Queue(settings.queue_name, connection=connection, serializer=JSONSerializer)
            for job_id in job_ids:
                # UUID only; no configuration, credentials, prompt, or retry policy in Redis.
                queue.enqueue(
                    "services.worker.app.runner.run_setup_job",
                    str(job_id),
                    job_id=f"setup-{job_id}-{uuid4().hex}",
                    job_timeout=7350,
                    result_ttl=0,
                    failure_ttl=86400,
                )
                dispatched += 1
    except Exception:
        # A failed notification cannot roll back the already committed job.
        pass
    return dispatched
