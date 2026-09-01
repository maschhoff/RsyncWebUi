"""Cron scheduling for stored tasks."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from .rsync_runner import manager

log = logging.getLogger("rsyncweb.scheduler")
TZ = os.environ.get("TZ", "Europe/Berlin")

scheduler = BackgroundScheduler(timezone=TZ, job_defaults={"coalesce": True, "max_instances": 1})


def validate_cron(expression: str) -> tuple[bool, str]:
    expression = (expression or "").strip()
    if not expression:
        return False, "No schedule given."
    if len(expression.split()) != 5:
        return False, "A cron expression has five fields: minute hour day month weekday."
    try:
        CronTrigger.from_crontab(expression, timezone=TZ)
    except ValueError as exc:
        return False, f"Invalid cron expression: {exc}"
    return True, ""


def _job_id(task_id: int) -> str:
    return f"task-{task_id}"


def _run_task(task_id: int) -> None:
    task = db.get_task(task_id)
    if not task:
        return
    ok, result = manager.start(task, trigger="schedule")
    if not ok:
        log.warning("Scheduled run for task %s skipped: %s", task_id, result)


def sync_task(task: dict) -> None:
    """Add, update or remove the cron job for one task."""
    job_id = _job_id(task["id"])
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()
    if not task.get("schedule_on") or not task.get("schedule"):
        return
    ok, _ = validate_cron(task["schedule"])
    if not ok:
        return
    scheduler.add_job(
        _run_task,
        CronTrigger.from_crontab(task["schedule"], timezone=TZ),
        id=job_id,
        args=[task["id"]],
        replace_existing=True,
    )


def remove_task(task_id: int) -> None:
    job = scheduler.get_job(_job_id(task_id))
    if job:
        job.remove()


def next_run_times() -> dict[int, str]:
    result: dict[int, str] = {}
    for job in scheduler.get_jobs():
        if job.id.startswith("task-") and job.next_run_time:
            result[int(job.id.split("-", 1)[1])] = job.next_run_time.isoformat(timespec="seconds")
    return result


def start() -> None:
    if not scheduler.running:
        scheduler.start()
    for task in db.list_tasks():
        sync_task(task)
    log.info("Scheduler started (%s active schedules)", len(scheduler.get_jobs()))
