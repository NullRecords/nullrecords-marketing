"""Marketing automation reports and cron inventory helpers."""

from __future__ import annotations

import html
import json
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.influencer import Influencer
from app.models.media import MediaAsset
from app.models.outreach import OutreachLog
from app.models.playlist import Playlist
from app.services.ai_provider import ai_status
from app.services.press.press_campaign import list_campaigns
from app.services.press.press_discovery import load_press_contacts


def _exports_dir() -> Path:
    path = Path(get_settings().exports_dir) / "marketing_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _count_recent(db: Session, model: Any, since: datetime) -> int:
    return db.query(func.count(model.id)).filter(model.created_at >= since).scalar() or 0


def _file_status_counts(items: list[dict], key: str = "status") -> dict[str, int]:
    return dict(Counter((item.get(key) or "unknown") for item in items))


def get_cron_inventory() -> dict[str, Any]:
    """Return the user's crontab as parsed, read-only inventory."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "error": str(exc),
            "jobs": [],
            "summary": {"total": 0, "nullrecords": 0, "stale_old_cms": 0, "duplicates": 0},
        }

    if result.returncode != 0:
        return {
            "available": True,
            "error": result.stderr.strip(),
            "jobs": [],
            "summary": {"total": 0, "nullrecords": 0, "stale_old_cms": 0, "duplicates": 0},
        }

    jobs = []
    command_counts: Counter[str] = Counter()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and not line.split("=", 1)[0].strip().startswith(("*", "@")):
            continue

        parts = line.split(None, 5)
        if line.startswith("@"):
            schedule = parts[0]
            command = parts[1] if len(parts) > 1 else ""
        elif len(parts) >= 6:
            schedule = " ".join(parts[:5])
            command = parts[5]
        else:
            schedule = ""
            command = line

        command_only, _, comment = command.partition("#")
        command_key = command_only.strip()
        command_counts[command_key] += 1
        is_stale_old_cms = "/NullRecords/ob-cms" in command_key
        jobs.append({
            "schedule": schedule,
            "command": command_key,
            "comment": comment.strip(),
            "is_nullrecords": "nullrecords" in command_key.lower(),
            "is_stale_old_cms": is_stale_old_cms,
            "is_dashboard_related": "dashboard" in command_key.lower() or "daily_report" in command_key,
        })

    duplicates = {
        command: count
        for command, count in command_counts.items()
        if command and count > 1
    }
    return {
        "available": True,
        "error": "",
        "jobs": jobs,
        "duplicates": duplicates,
        "summary": {
            "total": len(jobs),
            "nullrecords": sum(1 for job in jobs if job["is_nullrecords"]),
            "stale_old_cms": sum(1 for job in jobs if job["is_stale_old_cms"]),
            "dashboard_related": sum(1 for job in jobs if job["is_dashboard_related"]),
            "duplicates": len(duplicates),
        },
    }


def build_marketing_report(
    db: Session,
    cadence: str,
    scheduler_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a marketing automation report for hourly, daily, or weekly views."""
    now = datetime.now(timezone.utc)
    cadence_windows = {
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
    }
    window = cadence_windows.get(cadence, cadence_windows["daily"])
    since = now - window

    press_contacts = load_press_contacts()
    campaigns = list_campaigns()
    exports = Path(get_settings().exports_dir)
    shorts_queue = _load_json(exports / "daily_shorts_queue.json", [])
    posting_history = _load_json(exports / "posting_history.json", [])

    outreach_status = dict(
        db.query(OutreachLog.status, func.count(OutreachLog.id))
        .group_by(OutreachLog.status)
        .all()
    )
    press_by_vertical = dict(Counter(c.get("vertical") or "unknown" for c in press_contacts))
    press_needs_email = sum(1 for c in press_contacts if not c.get("email"))

    return {
        "cadence": cadence,
        "generated_at": now.isoformat(),
        "window": {
            "since": since.isoformat(),
            "until": now.isoformat(),
            "hours": round(window.total_seconds() / 3600, 2),
        },
        "ai": ai_status(),
        "scheduler": scheduler_status or {},
        "cron": get_cron_inventory(),
        "media": {
            "total": db.query(func.count(MediaAsset.id)).scalar() or 0,
            "new_in_window": _count_recent(db, MediaAsset, since),
            "downloaded": db.query(func.count(MediaAsset.id)).filter(MediaAsset.downloaded.is_(True)).scalar() or 0,
        },
        "outreach": {
            "logs_total": db.query(func.count(OutreachLog.id)).scalar() or 0,
            "new_in_window": _count_recent(db, OutreachLog, since),
            "by_status": outreach_status,
            "followups_due": db.query(func.count(OutreachLog.id)).filter(
                OutreachLog.follow_up_date.isnot(None),
                OutreachLog.follow_up_date <= now,
                OutreachLog.status != "followed_up",
            ).scalar() or 0,
            "playlists": db.query(func.count(Playlist.id)).scalar() or 0,
            "influencers": db.query(func.count(Influencer.id)).scalar() or 0,
        },
        "press": {
            "contacts_total": len(press_contacts),
            "contacts_by_vertical": press_by_vertical,
            "needs_email": press_needs_email,
            "campaigns_total": len(campaigns),
            "campaigns_by_status": dict(Counter(c.get("status") or "unknown" for c in campaigns)),
            "draft_campaigns": [
                {"id": c.get("id"), "name": c.get("name"), "vertical": c.get("vertical")}
                for c in campaigns
                if c.get("status") == "draft"
            ][:10],
        },
        "daily_shorts": {
            "queue_total": len(shorts_queue),
            "queue_by_status": _file_status_counts(shorts_queue),
            "posting_history_total": len(posting_history),
        },
        "action_items": _build_action_items(press_contacts, campaigns, shorts_queue),
    }


def _build_action_items(
    press_contacts: list[dict],
    campaigns: list[dict],
    shorts_queue: list[dict],
) -> list[str]:
    items = []
    missing_email = sum(1 for c in press_contacts if not c.get("email"))
    if missing_email:
        items.append(f"Research or enrich {missing_email} press contacts that are missing email addresses.")

    draft_campaigns = [c for c in campaigns if c.get("status") == "draft"]
    if draft_campaigns:
        items.append(f"Review {len(draft_campaigns)} draft press campaigns before distribution.")

    pending_shorts = sum(1 for s in shorts_queue if s.get("status") == "pending")
    if pending_shorts:
        items.append(f"Approve or reject {pending_shorts} pending daily shorts.")

    cron = get_cron_inventory()
    stale = cron.get("summary", {}).get("stale_old_cms", 0)
    if stale:
        items.append(f"Migrate or remove {stale} stale cron jobs pointing at the old CMS path.")

    return items


def save_marketing_report(report: dict[str, Any]) -> dict[str, str]:
    """Save report as JSON and HTML, including latest aliases."""
    cadence = report["cadence"]
    out_dir = _exports_dir() / cadence
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"{cadence}_marketing_report_{stamp}.json"
    html_path = out_dir / f"{cadence}_marketing_report_{stamp}.html"
    latest_json = out_dir / f"{cadence}_marketing_report_latest.json"
    latest_html = out_dir / f"{cadence}_marketing_report_latest.html"

    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    html_text = render_report_html(report)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    latest_html.write_text(html_text, encoding="utf-8")
    rel_base = f"/exports/marketing_reports/{cadence}"
    return {
        "json": str(json_path),
        "html": str(html_path),
        "latest_json": str(latest_json),
        "latest_html": str(latest_html),
        "json_url": f"{rel_base}/{json_path.name}",
        "html_url": f"{rel_base}/{html_path.name}",
        "latest_json_url": f"{rel_base}/{latest_json.name}",
        "latest_html_url": f"{rel_base}/{latest_html.name}",
    }


def list_saved_reports(cadence: str, limit: int = 30) -> dict[str, Any]:
    """List saved reports for a cadence."""
    out_dir = _exports_dir() / cadence
    files = sorted(out_dir.glob(f"{cadence}_marketing_report_*.json"), reverse=True)
    reports = []
    for path in files:
        if path.name.endswith("_latest.json"):
            continue
        html_path = path.with_suffix(".html")
        reports.append({
            "filename": path.name,
            "json_url": f"/exports/marketing_reports/{cadence}/{path.name}",
            "html_url": f"/exports/marketing_reports/{cadence}/{html_path.name}",
            "size_kb": round(path.stat().st_size / 1024, 1),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        })
        if len(reports) >= limit:
            break
    return {
        "cadence": cadence,
        "total": len([p for p in files if not p.name.endswith("_latest.json")]),
        "latest_json_url": f"/exports/marketing_reports/{cadence}/{cadence}_marketing_report_latest.json",
        "latest_html_url": f"/exports/marketing_reports/{cadence}/{cadence}_marketing_report_latest.html",
        "reports": reports,
    }


def load_latest_report(cadence: str) -> dict[str, Any] | None:
    """Load latest saved report for a cadence."""
    path = _exports_dir() / cadence / f"{cadence}_marketing_report_latest.json"
    if not path.exists():
        return None
    return _load_json(path, None)


def render_report_html(report: dict[str, Any]) -> str:
    """Render a compact self-contained HTML report."""
    esc = html.escape
    action_items = "".join(f"<li>{esc(item)}</li>" for item in report.get("action_items", []))
    cron = report.get("cron", {}).get("summary", {})
    body = f"""
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NullRecords {esc(report['cadence'].title())} Marketing Report</title>
<style>
body {{ margin: 0; background: #10131c; color: #ece7d8; font: 15px/1.5 system-ui, sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 32px 18px; }}
h1, h2 {{ color: #f0bd69; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }}
.card {{ border: 1px solid #2c3141; background: #171b27; border-radius: 8px; padding: 14px; }}
.num {{ font-size: 28px; color: #9ed6c2; font-weight: 700; }}
code, pre {{ background: #0b0d13; color: #d9e5ff; border-radius: 6px; }}
pre {{ padding: 12px; overflow: auto; }}
a {{ color: #9ed6c2; }}
</style>
<main>
<h1>{esc(report['cadence'].title())} Marketing Report</h1>
<p>Generated {esc(report['generated_at'])}</p>
<section class="grid">
<div class="card"><h2>Media</h2><div class="num">{report['media']['new_in_window']}</div><p>new in window / {report['media']['total']} total</p></div>
<div class="card"><h2>Outreach</h2><div class="num">{report['outreach']['new_in_window']}</div><p>new logs / {report['outreach']['followups_due']} follow-ups due</p></div>
<div class="card"><h2>Press</h2><div class="num">{report['press']['contacts_total']}</div><p>{report['press']['needs_email']} contacts need email</p></div>
<div class="card"><h2>Shorts</h2><div class="num">{report['daily_shorts']['queue_total']}</div><p>queued videos</p></div>
<div class="card"><h2>Cron</h2><div class="num">{cron.get('total', 0)}</div><p>{cron.get('stale_old_cms', 0)} stale old CMS path jobs</p></div>
<div class="card"><h2>AI</h2><div class="num">{esc(str(report['ai'].get('provider', '')))}</div><p>{esc(str(report['ai'].get('local_ai_model', '')))}</p></div>
</section>
<h2>Action Items</h2>
<ul>{action_items or '<li>No immediate action items.</li>'}</ul>
<h2>Details</h2>
<pre>{esc(json.dumps(report, indent=2, ensure_ascii=False))}</pre>
</main>
</html>
"""
    return body.strip()
