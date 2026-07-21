"""Maintenance-only reconciliation for durable Foundry validation attempts."""

from __future__ import annotations

import asyncio


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


async def _reconcile_validation_runs() -> int:
    from app.models.database import async_session
    from app.services.foundry_catalog import reconcile_expired_validation_runs

    async with async_session() as db:
        return await reconcile_expired_validation_runs(db)


@_get_celery_app().task(
    name="maintenance.reconcile_foundry_validations",
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def reconcile_foundry_validations_task() -> int:
    return asyncio.run(_reconcile_validation_runs())

