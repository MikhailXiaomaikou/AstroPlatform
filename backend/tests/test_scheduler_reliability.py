"""Scheduled dispatch failures must leave an explicit terminal record."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.database import Base
from app.models.schemas import PipelineRun, ScheduledRun, User


def test_broker_dispatch_failure_never_commits_permanent_pending_run(
    monkeypatch, tmp_path
):
    from app.pipeline.engine import execute_pipeline_task
    from app.scheduler_worker import check_and_dispatch_due_schedules

    url = f"sqlite:///{tmp_path / 'scheduler.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    owner_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(User(
            id=owner_id,
            username="scheduler-owner",
            email="scheduler@example.test",
            password_hash="not-used",
        ))
        db.add(ScheduledRun(
            id=uuid.uuid4(),
            user_id=owner_id,
            name="nightly cosmology",
            dag={"nodes": []},
            input_data_id="unused",
            cron_expr="0 0 * * *",
            enabled=True,
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        db.commit()
    engine.dispose()

    monkeypatch.setattr(settings, "database_url", url)

    def broker_down(*_args, **_kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(execute_pipeline_task, "delay", broker_down)
    check_and_dispatch_due_schedules()

    engine = create_engine(url)
    with Session(engine) as db:
        runs = db.execute(select(PipelineRun)).scalars().all()
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].completed_at is not None
        assert runs[0].results["error_class"] == "celery_dispatch_failed"
        assert not db.execute(
            select(PipelineRun).where(PipelineRun.status == "pending")
        ).scalars().all()
    engine.dispose()
