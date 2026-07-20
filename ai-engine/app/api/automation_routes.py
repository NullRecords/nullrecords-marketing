"""Automation monitor API for schedules, cron inventory, and reports."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.jobs.scheduler import get_scheduler_status
from app.services.marketing_reports import (
    build_marketing_report,
    get_cron_inventory,
    list_saved_reports,
    load_latest_report,
    save_marketing_report,
)

router = APIRouter(prefix="/admin/api/automation", tags=["automation"])


@router.get("/status")
def automation_status():
    """Combined scheduler and read-only crontab status for localhost dashboards."""
    return {
        "scheduler": get_scheduler_status(),
        "cron": get_cron_inventory(),
    }


@router.get("/cron")
def cron_inventory():
    """Read-only inventory of the current user crontab."""
    return get_cron_inventory()


@router.get("/reports/{cadence}")
def report_preview(cadence: str, db: Session = Depends(get_db)):
    """Build but do not save an hourly, daily, or weekly marketing report."""
    if cadence not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=404, detail="cadence must be hourly, daily, or weekly")
    return build_marketing_report(db, cadence, scheduler_status=get_scheduler_status())


@router.post("/reports/{cadence}")
def generate_report(cadence: str, db: Session = Depends(get_db)):
    """Build and save an hourly, daily, or weekly marketing report."""
    if cadence not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=404, detail="cadence must be hourly, daily, or weekly")
    report = build_marketing_report(db, cadence, scheduler_status=get_scheduler_status())
    paths = save_marketing_report(report)
    return {"status": "generated", "paths": paths, "report": report}


@router.get("/reports/{cadence}/latest")
def latest_report(cadence: str):
    """Return the latest saved report for a cadence."""
    if cadence not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=404, detail="cadence must be hourly, daily, or weekly")
    report = load_latest_report(cadence)
    if not report:
        raise HTTPException(status_code=404, detail="no saved report found")
    return report


@router.get("/reports/{cadence}/history")
def report_history(cadence: str, limit: int = 30):
    """List saved report artifacts for a cadence."""
    if cadence not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=404, detail="cadence must be hourly, daily, or weekly")
    return list_saved_reports(cadence, limit=limit)
