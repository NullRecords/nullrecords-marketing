"""AI-Engine — FastAPI application entry point."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import init_db
from app.api.media_routes import router as media_router
from app.api.outreach_routes import router as outreach_router
from app.api.system_routes import router as system_router
from app.api.admin_routes import router as admin_router
from app.api.marketing_routes import router as marketing_router
from app.api.video_routes import router as video_router
from app.api.scheduler_routes import router as scheduler_router
from app.api.campaign_routes import router as campaign_router
from app.api.tracking_routes import router as tracking_router
from app.api.press_routes import router as press_router
from app.api.crm_routes import router as crm_router
from app.api.automation_routes import router as automation_router
from app.jobs.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"
EXPORTS_DIR = Path(__file__).resolve().parent.parent / "exports"
MEDIA_DIR = Path(__file__).resolve().parent.parent / "media-library"

app = FastAPI(
    title="NullRecords AI Engine",
    description=(
        "Local-first AI-powered media sourcing, tagging, and outreach platform "
        "for the NullRecords music collective."
    ),
    version="0.1.0",
)

# Allow local services to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8100",
        "http://localhost:8300",
        "http://localhost:4001",
        "http://127.0.0.1:8100",
        "https://www.nullrecords.com",
        "https://nullrecords.com",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register routers ---
app.include_router(media_router)
app.include_router(outreach_router)
app.include_router(system_router)
app.include_router(admin_router)
app.include_router(marketing_router)
app.include_router(video_router)
app.include_router(scheduler_router)
app.include_router(campaign_router)
app.include_router(tracking_router)
app.include_router(press_router)
app.include_router(crm_router)
app.include_router(automation_router)

# --- Static files ---
app.mount("/exports", StaticFiles(directory=str(EXPORTS_DIR)), name="exports")
app.mount("/media-library", StaticFiles(directory=str(MEDIA_DIR)), name="media-library")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def on_startup():
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


@app.get("/admin")
def admin_page():
    """Serve the admin dashboard."""
    return FileResponse(str(STATIC_DIR / "admin.html"))


@app.get("/admin/news")
def admin_news_page():
    """Serve the news management page."""
    return FileResponse(str(STATIC_DIR / "news.html"))


@app.get("/admin/contacts")
def admin_contacts_page():
    """Serve the contacts report page."""
    return FileResponse(str(STATIC_DIR / "contacts.html"))


@app.get("/")
def root():
    """Serve the command-center landing page."""
    return FileResponse(str(STATIC_DIR / "index.html"))
