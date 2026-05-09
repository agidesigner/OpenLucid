import asyncio
import uuid
from datetime import datetime, timezone

from app.application import task_service
from app.models.task_run import TaskRun


class _ScalarResult:
    def __init__(self, value=None, rowcount=0):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, *, existing=None, task=None, rowcount=0):
        self.existing = existing
        self.task = task
        self.rowcount = rowcount
        self.added = None
        self.flushed = False
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        return _ScalarResult(self.existing, self.rowcount)

    def add(self, obj):
        self.added = obj

    async def flush(self):
        self.flushed = True

    async def rollback(self):
        self.rollbacks += 1

    async def commit(self):
        self.commits += 1

    async def get(self, _model, _id):
        return self.task


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


def _task(task_type="test.ok", *, attempts=1, max_attempts=3):
    task = TaskRun(
        task_type=task_type,
        status=task_service.STATUS_RUNNING,
        attempts=attempts,
        max_attempts=max_attempts,
        locked_by="worker-a",
        locked_at=datetime.now(timezone.utc),
    )
    task.id = uuid.uuid4()
    return task


def test_enqueue_task_dedupes_active_unique_key():
    existing = _task("asset.parse")
    session = _FakeSession(existing=existing)

    result = asyncio.run(
        task_service.enqueue_task(
            session,
            task_type="asset.parse",
            unique_key="asset.parse:1",
        )
    )

    assert result is existing
    assert session.added is None
    assert session.flushed is False


def test_run_task_failure_requeues_when_attempts_remain(monkeypatch):
    task = _task("test.fail", attempts=1, max_attempts=3)
    session = _FakeSession(task=task)

    async def _handler(_session, _task):
        raise RuntimeError("temporary outage")

    monkeypatch.setitem(task_service._HANDLERS, "test.fail", _handler)
    monkeypatch.setattr(task_service, "async_session_factory", lambda: _SessionContext(session))

    asyncio.run(task_service._run_task(task.id))

    assert task.status == task_service.STATUS_PENDING
    assert task.error_message == "temporary outage"
    assert task.locked_by is None
    assert task.locked_at is None
    assert task.finished_at is None
    assert session.commits == 1


def test_run_task_failure_marks_failed_after_max_attempts(monkeypatch):
    task = _task("test.fail.max", attempts=3, max_attempts=3)
    session = _FakeSession(task=task)

    async def _handler(_session, _task):
        raise RuntimeError("permanent outage")

    monkeypatch.setitem(task_service._HANDLERS, "test.fail.max", _handler)
    monkeypatch.setattr(task_service, "async_session_factory", lambda: _SessionContext(session))

    asyncio.run(task_service._run_task(task.id))

    assert task.status == task_service.STATUS_FAILED
    assert task.error_message == "permanent outage"
    assert task.locked_by is None
    assert task.locked_at is None
    assert task.finished_at is not None


def test_run_task_unknown_handler_marks_failed_with_clear_error(monkeypatch):
    task = _task("missing.handler")
    session = _FakeSession(task=task)
    monkeypatch.setattr(task_service, "async_session_factory", lambda: _SessionContext(session))

    asyncio.run(task_service._run_task(task.id))

    assert task.status == task_service.STATUS_FAILED
    assert "No handler registered for task_type=missing.handler" == task.error_message
    assert task.locked_by is None
    assert task.locked_at is None
    assert task.finished_at is not None


def test_requeue_stale_running_tasks_returns_updated_count():
    session = _FakeSession(rowcount=2)

    count = asyncio.run(task_service.requeue_stale_running_tasks(session))

    assert count == 2
