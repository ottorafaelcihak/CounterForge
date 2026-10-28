from uuid import uuid4

from rq.serializers import JSONSerializer

from services.api.app.config import Settings
from services.worker.app import queue


def test_rq_payload_is_uuid_only_json_with_unique_deliveries_and_no_retry(monkeypatch):
    queued = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Redis:
        @classmethod
        def from_url(cls, *args, **kwargs):
            return Connection()

    class Queue:
        def __init__(self, name, *, connection, serializer):
            assert name == "counterforge" and serializer is JSONSerializer

        def enqueue(self, *args, **kwargs):
            queued.append((args, kwargs))

    monkeypatch.setattr(queue, "Redis", Redis)
    monkeypatch.setattr(queue, "Queue", Queue)
    job_id = uuid4()
    assert queue.enqueue_ids(Settings(), [job_id, job_id]) == 2
    assert all(
        args == ("services.worker.app.runner.run_setup_job", str(job_id)) for args, _ in queued
    )
    assert queued[0][1]["job_id"] != queued[1][1]["job_id"]
    assert all("retry" not in kwargs for _, kwargs in queued)


def test_redis_failure_is_best_effort_without_logging_configuration(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("must not log connection secret")

    monkeypatch.setattr(queue.Redis, "from_url", unavailable)
    assert queue.enqueue_ids(Settings(), [uuid4()]) == 0
