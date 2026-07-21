"""Video generation API routes.

POST /video/upload-audio — Upload an audio file via multipart form
GET  /video/audio-info   — Get duration/metadata of an uploaded audio file
POST /video/generate     — Generate a single video
POST /video/generate-batch — Auto-split audio into shorts + optional full-length
GET  /video/exports      — List exported videos
GET  /video/download/{filename} — Download an exported video file
POST /video/publish      — Publish to YouTube (auto) or get manual instructions for TikTok/IG
GET  /video/youtube/auth — Start YouTube OAuth2 flow
GET  /video/youtube/callback — OAuth2 callback
GET  /video/youtube/status — Check if YouTube is authenticated
"""

import datetime
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.media import MediaAsset
from app.services.media.audio import get_duration_ms
from app.services.media.overlays import (
    DEFAULT_PROVIDERS,
    ICON_UPLOAD_DIR,
    ProviderIconConfig,
    QRCodeConfig,
)
from app.services.media.video_engine import VideoConfig, VideoEngine

log = logging.getLogger(__name__)
router = APIRouter(prefix="/video", tags=["video"])

_engine: VideoEngine | None = None

ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALLOWED_CLIP_EXT = {".mp4", ".mov", ".mpeg", ".mpg", ".avi", ".webm"}


def _presets_path() -> Path:
    """JSON file storing named presets."""
    return Path(get_settings().exports_dir) / "presets.json"


def _history_path() -> Path:
    """JSON file storing per-video generation settings history."""
    return Path(get_settings().exports_dir) / "video_history.json"


def _load_json(path: Path) -> list[dict]:
    if path.exists():
        return json.loads(path.read_text())
    return []


def _save_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _get_engine() -> VideoEngine:
    global _engine
    if _engine is None:
        _engine = VideoEngine()
    return _engine


def _uploads_dir() -> Path:
    """Return (and create) the audio uploads directory."""
    d = Path(get_settings().exports_dir) / "audio_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_uploads_dir() -> Path:
    """Return (and create) the image uploads directory."""
    d = Path(get_settings().exports_dir) / "image_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Schemas ─────────────────────────────────────────────────────────────────

class AudioInfoResponse(BaseModel):
    filename: str
    path: str
    duration_ms: int
    size_kb: float


class VideoGenerateRequest(BaseModel):
    audio_path: str = Field(..., description="Absolute or relative path to the audio file")
    start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(15000, ge=1000, le=600000)
    mood: str = Field("")
    tags: list[str] = Field(default_factory=list)
    output_name: str = Field("")
    clip_count: int | None = Field(None, ge=2, le=20)
    fps: int = Field(24, ge=12, le=60)
    use_glitch_transitions: bool = Field(True)
    aspect: str = Field("vertical", description="'vertical' (9:16) or 'widescreen' (16:9)")

    # Image sourcing
    image_query: str = Field("", description="Pexels search term for background images (e.g. 'cyberpunk city')")
    image_urls: list[str] = Field(default_factory=list, description="Direct image URLs to use as backgrounds")
    image_paths: list[str] = Field(default_factory=list, description="Local image file paths")

    # Text overlays
    overlay_text: str = Field("", description="Main text overlay (track name, title)")
    overlay_subtitle: str = Field("", description="Subtitle overlay (artist, link)")
    overlay_position: str = Field("center", description="Text position: center, top, bottom, lower-third")

    # Effect configuration
    effect_opacity: float = Field(1.0, ge=0.0, le=1.0, description="Global effect layer opacity (0=transparent, 1=opaque)")
    glitch_opacity: float = Field(0.7, ge=0.0, le=1.0, description="Glitch transition opacity")
    color_shift_enabled: bool = Field(False, description="Enable chromatic aberration effect")
    scanline_enabled: bool = Field(False, description="Enable CRT scanline overlay")
    vhs_enabled: bool = Field(False, description="Enable VHS distortion effect")
    beat_flash_enabled: bool = Field(False, description="Flash on beat hits")

    # Visualizer configuration
    visualizer_enabled: bool = Field(False, description="Enable audio EQ visualizer overlay")
    visualizer_type: str = Field("bars", description="Visualizer type: bars, lines, waveform, circular")
    visualizer_color: str = Field("#00ffff", description="Primary visualizer color (hex)")
    visualizer_color2: str = Field("#ff5758", description="Secondary/gradient visualizer color (hex)")
    visualizer_opacity: float = Field(0.8, ge=0.0, le=1.0, description="Visualizer layer opacity")
    visualizer_intensity: float = Field(1.0, ge=0.1, le=3.0, description="Visualizer height/size multiplier")
    visualizer_position: str = Field("bottom", description="Visualizer position: bottom, center, top, full")
    visualizer_bar_count: int = Field(32, ge=8, le=128, description="Number of bars/lines in the visualizer")
    visualizer_glow: bool = Field(True, description="Enable glow effect on visualizer")
    show_song_info: bool = Field(True, description="Show song name and artist on visualizer")

    # Font / style configuration
    font_family: str = Field("", description="Font family for overlays (empty = AI-selected)")
    title_color: str = Field("", description="Title text color hex (empty = AI-selected)")
    subtitle_color: str = Field("", description="Subtitle text color hex (empty = AI-selected)")
    font_size_title: int = Field(0, ge=0, le=200, description="Title font size (0 = auto)")
    font_size_subtitle: int = Field(0, ge=0, le=200, description="Subtitle font size (0 = auto)")
    text_spacing: int = Field(0, ge=0, le=100, description="Line spacing in pixels (0 = auto)")

    # Multi-artwork with per-image timing and animation
    artwork_entries: list[dict] = Field(
        default_factory=list,
        description="List of artwork dicts: {path, start_sec, duration_sec, animation, animation_speed, crossfade_sec}. "
                    "Enables artwork mode — images form persistent background with effects overlaid.",
    )

    # Clip selection control
    auto_select_clips: bool = Field(
        True,
        description="When False, skip auto-selecting clips/images from library — only use explicitly provided paths. "
                    "Automatically set to False in artwork mode.",
    )

    # Provider icon strip overlay
    provider_icons_enabled: bool = Field(False, description="Show provider/label icon strip on video")
    provider_icons_list: list[str] = Field(
        default_factory=list,
        description="Provider names to show (empty = all defaults). Options: " + ", ".join(DEFAULT_PROVIDERS.keys()),
    )
    provider_icons_position: str = Field("bottom", description="Icon strip position: top, bottom, middle")
    provider_icons_size: int = Field(36, ge=16, le=80, description="Icon size in pixels")
    provider_icons_opacity: float = Field(0.75, ge=0.0, le=1.0, description="Icon strip opacity")

    # QR code overlay
    qr_enabled: bool = Field(False, description="Show QR code on video")
    qr_url: str = Field("https://www.nullrecords.com", description="URL the QR code links to")
    qr_position: str = Field("bottom-right", description="QR position: top-left, top-right, bottom-left, bottom-right")
    qr_size: int = Field(80, ge=40, le=200, description="QR code size in pixels")
    qr_opacity: float = Field(0.85, ge=0.0, le=1.0, description="QR code opacity")


class VideoGenerateResponse(BaseModel):
    video_path: str
    filename: str
    size_kb: float
    duration_ms: int


class BatchGenerateRequest(BaseModel):
    audio_path: str = Field(..., description="Path to the audio file")
    segment_duration_ms: int = Field(15000, ge=5000, le=60000, description="Duration per short")
    mood: str = Field("")
    tags: list[str] = Field(default_factory=list)
    clip_count: int | None = Field(None, ge=2, le=20)
    fps: int = Field(24, ge=12, le=60)
    use_glitch_transitions: bool = Field(True)
    include_full_length: bool = Field(False, description="Also render one widescreen full-length video")


class BatchGenerateResponse(BaseModel):
    shorts: list[VideoGenerateResponse]
    full_length: VideoGenerateResponse | None = None


class VideoExport(BaseModel):
    filename: str
    path: str
    size_kb: float
    size_mb: float = 0
    modified_at: str = ""


class PublishRequest(BaseModel):
    video_path: str = Field(..., description="Path to the video file to publish")
    platforms: list[str] = Field(..., description="List of platforms: tiktok, instagram, youtube")
    title: str = Field("", description="Video title / caption")
    description: str = Field("", description="Video description")
    tags: list[str] = Field(default_factory=list, description="Hashtags / tags")
    youtube_privacy: str = Field("public", description="YouTube privacy: public, unlisted, private")


class PublishResult(BaseModel):
    platform: str
    status: str  # "uploaded", "manual", "error", "not_authenticated"
    message: str
    url: str | None = None
    download_url: str | None = None
    caption: str | None = None
    instructions: str | None = None


class PublishResponse(BaseModel):
    results: list[PublishResult]


# ── Routes ──────────────────────────────────────────────────────────────────

@router.post("/upload-audio", response_model=AudioInfoResponse)
async def upload_audio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload an audio file and return its path + duration."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXT))}",
        )

    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).stem}{ext}"
    dest = _uploads_dir() / safe_name

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    duration = get_duration_ms(dest)
    size_kb = round(dest.stat().st_size / 1024, 1)

    # Auto-register in media_assets
    asset = MediaAsset(
        source="upload",
        source_id=safe_name,
        title=file.filename,
        url=f"/video/audio-stream?path={dest}",
        local_path=str(dest),
        tags=json.dumps(["audio", ext.lstrip(".")]),
        duration=duration / 1000.0,
        downloaded=True,
    )
    db.add(asset)
    db.commit()

    log.info("Audio uploaded: %s (%d ms, %.1f KB)", safe_name, duration, size_kb)
    return AudioInfoResponse(filename=safe_name, path=str(dest), duration_ms=duration, size_kb=size_kb)


@router.get("/audio-info", response_model=AudioInfoResponse)
def audio_info(path: str):
    """Get duration + size of an audio file already on disk."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(get_settings().exports_dir).parent / p
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    return AudioInfoResponse(
        filename=p.name,
        path=str(p),
        duration_ms=get_duration_ms(p),
        size_kb=round(p.stat().st_size / 1024, 1),
    )


@router.get("/audio-stream")
def audio_stream(path: str):
    """Stream an audio file for browser playback."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(get_settings().exports_dir).parent / p
    # Security: only serve from within the project directory
    exports_root = Path(get_settings().exports_dir).parent.resolve()
    if not p.resolve().is_relative_to(exports_root):
        raise HTTPException(status_code=403, detail="Access denied")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(p), media_type="audio/mpeg")


class SuggestMediaRequest(BaseModel):
    mood: str = ""
    tags: list[str] = Field(default_factory=list)
    image_query: str = ""
    count: int = Field(8, ge=1, le=20)


@router.post("/suggest-media")
def suggest_media(req: SuggestMediaRequest, db: Session = Depends(get_db)):
    """Suggest media (images/clips) based on mood/tags — user must approve before they're used."""
    from app.models.media import MediaAsset
    suggestions = []
    settings = get_settings()
    exports_root = Path(settings.exports_dir).parent.resolve()  # ai-engine/

    def to_serve_url(raw_path: str) -> tuple[str, str]:
        """Convert raw path (absolute or relative) to (servable_url, relative_path)."""
        p = Path(raw_path)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(exports_root)
                return f"/{rel}", str(rel)
            except ValueError:
                return raw_path, raw_path
        elif raw_path.startswith(("http://", "https://")):
            return raw_path, raw_path
        elif raw_path.startswith("exports/"):
            return f"/{raw_path}", raw_path
        elif raw_path.startswith("media-library/"):
            return f"/{raw_path}", raw_path
        else:
            return f"/exports/{raw_path}", f"exports/{raw_path}"

    # 1. Search DB for matching assets
    query = db.query(MediaAsset)
    if req.mood:
        query = query.filter(MediaAsset.tags.contains(req.mood))
    assets = query.order_by(MediaAsset.created_at.desc()).limit(req.count * 2).all()

    for a in assets:
        path = a.local_path or a.url or ""
        if path and len(suggestions) < req.count:
            url, rel_path = to_serve_url(path)
            suggestions.append({
                "id": a.id,
                "source": a.source,
                "filename": Path(path).name,
                "url": url,
                "path": rel_path,
                "tags": a.tags or "",
                "type": "image" if any(path.lower().endswith(e) for e in (".png", ".jpg", ".jpeg", ".gif", ".webp")) else "video",
            })

    # 2. Search exports/image_uploads for untracked images
    img_dir = Path(get_settings().exports_dir) / "image_uploads"
    if img_dir.exists() and len(suggestions) < req.count:
        for f in sorted(img_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp") and len(suggestions) < req.count:
                if not any(s["filename"] == f.name for s in suggestions):
                    suggestions.append({
                        "id": None,
                        "source": "upload",
                        "filename": f.name,
                        "url": f"/exports/image_uploads/{f.name}",
                        "path": f"exports/image_uploads/{f.name}",
                        "tags": "",
                        "type": "image",
                    })

    # 3. Search media-library for clips
    ml_dir = Path(get_settings().exports_dir).parent / "media-library"
    if ml_dir.exists() and len(suggestions) < req.count:
        for sub in ["images", "pexels", "internet_archive"]:
            sd = ml_dir / sub
            if not sd.exists():
                continue
            for f in sorted(sd.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    if len(suggestions) < req.count and not any(s["filename"] == f.name for s in suggestions):
                        rel = f"media-library/{f.relative_to(ml_dir)}"
                        suggestions.append({
                            "id": None,
                            "source": sub,
                            "filename": f.name,
                            "url": f"/{rel}",
                            "path": rel,
                            "tags": "",
                            "type": "image" if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp") else "video",
                        })

    return {"suggestions": suggestions[:req.count], "total_available": len(suggestions)}


@router.post("/generate", response_model=VideoGenerateResponse)
def generate_video(req: VideoGenerateRequest, db: Session = Depends(get_db)):
    """Generate a single video from media assets + audio."""
    audio = _resolve_audio(req.audio_path)

    config = VideoConfig(
        audio_path=str(audio),
        start_ms=req.start_ms,
        duration_ms=req.duration_ms,
        mood=req.mood,
        tags=req.tags,
        output_name=req.output_name,
        clip_count=req.clip_count,
        fps=req.fps,
        use_glitch_transitions=req.use_glitch_transitions,
        aspect=req.aspect,
        image_query=req.image_query,
        image_urls=req.image_urls,
        image_paths=req.image_paths,
        overlay_text=req.overlay_text,
        overlay_subtitle=req.overlay_subtitle,
        overlay_position=req.overlay_position,
        # Effect config
        effect_opacity=req.effect_opacity,
        glitch_opacity=req.glitch_opacity,
        color_shift_enabled=req.color_shift_enabled,
        scanline_enabled=req.scanline_enabled,
        vhs_enabled=req.vhs_enabled,
        beat_flash_enabled=req.beat_flash_enabled,
        # Visualizer config
        visualizer_enabled=req.visualizer_enabled,
        visualizer_type=req.visualizer_type,
        visualizer_color=req.visualizer_color,
        visualizer_color2=req.visualizer_color2,
        visualizer_opacity=req.visualizer_opacity,
        visualizer_intensity=req.visualizer_intensity,
        visualizer_position=req.visualizer_position,
        visualizer_bar_count=req.visualizer_bar_count,
        visualizer_glow=req.visualizer_glow,
        show_song_info=req.show_song_info,
        # Font config
        font_family=req.font_family,
        title_color=req.title_color,
        subtitle_color=req.subtitle_color,
        font_size_title=req.font_size_title,
        font_size_subtitle=req.font_size_subtitle,
        text_spacing=req.text_spacing,
        # Multi-artwork
        artwork_entries=req.artwork_entries,
        # Clip selection: disable auto in artwork mode
        auto_select_clips=req.auto_select_clips if not req.artwork_entries else False,
        # Provider icon overlay
        provider_icon_config=ProviderIconConfig(
            providers=req.provider_icons_list,
            position=req.provider_icons_position,
            icon_size=req.provider_icons_size,
            opacity=req.provider_icons_opacity,
            custom_icons={
                f.stem: str(f) for f in _icon_dir().iterdir()
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
            },
        ) if req.provider_icons_enabled else None,
        # QR code overlay
        qr_code_config=QRCodeConfig(
            url=req.qr_url,
            position=req.qr_position,
            size=req.qr_size,
            opacity=req.qr_opacity,
        ) if req.qr_enabled else None,
    )

    return _run_generate(db, config, req.duration_ms, settings=req.model_dump())


@router.post("/generate-batch", response_model=BatchGenerateResponse)
def generate_batch(req: BatchGenerateRequest, db: Session = Depends(get_db)):
    """Auto-split audio into multiple shorts, optionally plus one full-length widescreen video."""
    audio = _resolve_audio(req.audio_path)
    duration = get_duration_ms(audio)

    seg = req.segment_duration_ms
    shorts: list[VideoGenerateResponse] = []

    # Generate shorts segments
    offset = 0
    idx = 1
    while offset < duration:
        chunk = min(seg, duration - offset)
        if chunk < 3000:
            break  # skip very short tail

        config = VideoConfig(
            audio_path=str(audio),
            start_ms=offset,
            duration_ms=chunk,
            mood=req.mood,
            tags=req.tags,
            output_name=f"short_{idx:02d}",
            clip_count=req.clip_count,
            fps=req.fps,
            use_glitch_transitions=req.use_glitch_transitions,
            aspect="vertical",
        )
        shorts.append(_run_generate(db, config, chunk))
        offset += seg
        idx += 1

    # Optional full-length widescreen
    full: VideoGenerateResponse | None = None
    if req.include_full_length:
        full_config = VideoConfig(
            audio_path=str(audio),
            start_ms=0,
            duration_ms=duration,
            mood=req.mood,
            tags=req.tags,
            output_name="full_length",
            clip_count=max((req.clip_count or 4), duration // 5000),
            fps=req.fps,
            use_glitch_transitions=req.use_glitch_transitions,
            aspect="widescreen",
        )
        full = _run_generate(db, full_config, duration)

    return BatchGenerateResponse(shorts=shorts, full_length=full)


@router.get("/exports", response_model=list[VideoExport])
def list_exports():
    """List all previously generated videos (newest first)."""
    import datetime
    engine = _get_engine()
    raw = engine.list_exports()
    enriched = []
    for v in raw:
        p = Path(v.path) if isinstance(v, VideoExport) else Path(v["path"])
        fname = v.filename if isinstance(v, VideoExport) else v["filename"]
        sk = v.size_kb if isinstance(v, VideoExport) else v["size_kb"]
        mtime = ""
        if p.exists():
            ts = p.stat().st_mtime
            mtime = datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
        enriched.append(VideoExport(
            filename=fname, path=str(p),
            size_kb=sk, size_mb=round(sk / 1024, 1),
            modified_at=mtime,
        ))
    enriched.sort(key=lambda x: x.modified_at, reverse=True)
    return enriched


@router.get("/download/{filename}")
def download_export(filename: str):
    """Download an exported video file."""
    exports_dir = Path(get_settings().exports_dir) / "videos"
    # Sanitize — only allow simple filenames, no path traversal
    safe = Path(filename).name
    filepath = exports_dir / safe
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {filename}")
    return FileResponse(
        path=str(filepath),
        media_type="video/mp4",
        filename=safe,
    )


@router.delete("/delete/{filename}")
def delete_export(filename: str):
    """Permanently delete an exported video file."""
    exports_dir = Path(get_settings().exports_dir) / "videos"
    safe = Path(filename).name
    filepath = exports_dir / safe
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {filename}")
    filepath.unlink()
    log.info("Deleted video: %s", safe)
    return {"status": "deleted", "filename": safe}


@router.post("/archive/{filename}")
def archive_export(filename: str):
    """Move an exported video to the archive directory."""
    exports_dir = Path(get_settings().exports_dir) / "videos"
    safe = Path(filename).name
    filepath = exports_dir / safe
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {filename}")

    archive_dir = exports_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / safe
    shutil.move(str(filepath), str(dest))
    log.info("Archived video: %s → %s", safe, dest)
    return {"status": "archived", "filename": safe, "archive_path": str(dest)}


# ── Audio analysis ──────────────────────────────────────────────────────────

class AudioAnalysisResponse(BaseModel):
    beats: list[float] = Field(description="Beat timestamps in seconds")
    bpm: float
    levels: list[float] = Field(description="Per-frame RMS levels (0-1)")
    spectrum: list[list[float]] = Field(description="Per-frame spectrum bands")
    duration: float
    sample_rate: int
    fps: int
    n_frames: int


@router.post("/analyze-audio", response_model=AudioAnalysisResponse)
def analyze_audio(
    audio_path: str = Query(..., description="Path to audio file"),
    fps: int = Query(24, ge=12, le=60),
):
    """Analyze audio for beats, spectrum, and levels — used for visualizer config."""
    from app.services.media.audio_analyzer import analyze_audio as _analyze

    path = _resolve_audio(audio_path)
    try:
        result = _analyze(str(path), fps=fps)
    except Exception as e:
        log.exception("Audio analysis failed")
        raise HTTPException(status_code=422, detail=f"Audio analysis failed: {e}")

    return AudioAnalysisResponse(**result)


# ── Preview ─────────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    """Generate a single preview frame showing layout, text, effects, and visualizer."""
    image_path: str = Field("", description="Path to a background image")
    clip_path: str = Field("", description="Path to a video clip (use frame at time_sec)")
    aspect: str = Field("vertical", description="'vertical' or 'widescreen'")
    time_sec: float = Field(2.0, ge=0.0, description="Timestamp to preview")
    overlay_text: str = Field("")
    overlay_subtitle: str = Field("")
    overlay_position: str = Field("center")
    title_color: str = Field("")
    subtitle_color: str = Field("")
    font_size_title: int = Field(0, ge=0, le=200)
    font_size_subtitle: int = Field(0, ge=0, le=200)
    font_family: str = Field("")
    text_spacing: int = Field(0, ge=0, le=100)
    mood: str = Field("")
    effect_opacity: float = Field(1.0, ge=0.0, le=1.0)
    glitch_opacity: float = Field(0.7, ge=0.0, le=1.0)
    color_shift_enabled: bool = Field(False)
    scanline_enabled: bool = Field(False)
    vhs_enabled: bool = Field(False)
    visualizer_enabled: bool = Field(False)
    visualizer_type: str = Field("bars")
    visualizer_color: str = Field("#00ffff")
    visualizer_color2: str = Field("#ff5758")
    visualizer_opacity: float = Field(0.8)
    visualizer_intensity: float = Field(1.0)
    visualizer_position: str = Field("bottom")
    visualizer_bar_count: int = Field(32)
    visualizer_glow: bool = Field(True)
    show_song_info: bool = Field(True)
    audio_path: str = Field("", description="Audio path for visualizer spectrum data")
    fps: int = Field(24, ge=12, le=60)


@router.post("/preview")
def generate_preview(req: PreviewRequest):
    """Render a single PNG frame showing what the video will look like.

    Returns a PNG image response — no video encoding needed.
    """
    import io
    from fastapi.responses import StreamingResponse
    from PIL import Image as PILImage
    from app.services.media.renderer import TextOverlay, render_preview_frame
    from app.services.media.effects import EffectConfig

    # Resolve paths
    image_path = None
    clip_path = None
    if req.image_path:
        p = Path(req.image_path)
        if p.exists():
            image_path = p
    if req.clip_path:
        p = Path(req.clip_path)
        if p.exists():
            clip_path = p

    # Build text overlays
    from app.services.media.video_engine import _mood_to_colors
    mood_colors = _mood_to_colors(req.mood)
    title_color = req.title_color or mood_colors["title"]
    subtitle_color = req.subtitle_color or mood_colors["subtitle"]

    text_overlays = []
    if req.overlay_text:
        text_overlays.append(TextOverlay(
            text=req.overlay_text,
            position=req.overlay_position,
            color=title_color,
            shadow=True,
            font_size=req.font_size_title,
            font_family=req.font_family,
            text_spacing=req.text_spacing,
        ))
    if req.overlay_subtitle:
        text_overlays.append(TextOverlay(
            text=req.overlay_subtitle,
            position="lower-third" if req.overlay_position == "center" else "bottom",
            color=subtitle_color,
            shadow=True,
            font_size=req.font_size_subtitle,
            font_family=req.font_family,
            text_spacing=req.text_spacing,
        ))

    # Build effect config
    effect_config = EffectConfig(
        global_opacity=req.effect_opacity,
        glitch_opacity=req.glitch_opacity,
        color_shift_enabled=req.color_shift_enabled,
        scanline_enabled=req.scanline_enabled,
        vhs_enabled=req.vhs_enabled,
    )

    # Build visualizer config (with synthetic spectrum data if no audio)
    visualizer_config = None
    if req.visualizer_enabled:
        import numpy as np
        n_frames = int(req.time_sec * req.fps) + 10
        frame_idx = int(req.time_sec * req.fps)

        # Try to get real audio data
        spectrum = None
        levels = None
        beats = []
        if req.audio_path:
            try:
                audio = Path(req.audio_path)
                if not audio.is_absolute():
                    audio = Path(get_settings().exports_dir).parent / audio
                if audio.exists():
                    from app.services.media.audio_analyzer import analyze_audio as _analyze
                    audio_data = _analyze(str(audio), fps=req.fps)
                    spectrum = audio_data["spectrum"]
                    levels = audio_data["levels"]
                    beats = audio_data["beats"]
                    n_frames = audio_data["n_frames"]
            except Exception:
                pass

        if spectrum is None:
            # Generate synthetic spectrum for preview
            rng = np.random.default_rng(42)
            spectrum = []
            for i in range(n_frames):
                bands = (rng.random(8) * 0.6 + 0.15).tolist()
                spectrum.append(bands)
            levels = (rng.random(n_frames) * 0.5 + 0.3).tolist()

        visualizer_config = {
            "type": req.visualizer_type,
            "color": req.visualizer_color,
            "color2": req.visualizer_color2,
            "opacity": req.visualizer_opacity,
            "intensity": req.visualizer_intensity,
            "position": req.visualizer_position,
            "bar_count": req.visualizer_bar_count,
            "glow": req.visualizer_glow,
            "show_song_info": req.show_song_info,
            "skip_song_info": bool(text_overlays),  # don't duplicate text
            "song_title": req.overlay_text,
            "artist": req.overlay_subtitle,
            "spectrum": spectrum,
            "levels": levels,
            "beats": beats,
            "fps": req.fps,
            "n_frames": n_frames,
        }

    # Render the preview frame
    frame = render_preview_frame(
        image_path=image_path,
        clip_path=clip_path,
        text_overlays=text_overlays or None,
        effect_config=effect_config,
        visualizer_config=visualizer_config,
        aspect=req.aspect,
        time_sec=req.time_sec,
        fps=req.fps,
    )

    # Convert to PNG
    img = PILImage.fromarray(frame)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")


# ── Presets & History ───────────────────────────────────────────────────────

class PresetSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Preset name")
    settings: dict = Field(..., description="All video generation settings")


class PresetResponse(BaseModel):
    id: str
    name: str
    settings: dict
    created_at: str
    updated_at: str


class VideoHistoryEntry(BaseModel):
    id: str
    filename: str
    video_path: str
    settings: dict
    created_at: str


@router.get("/presets", response_model=list[PresetResponse])
def list_presets():
    """List all saved presets (newest first)."""
    data = _load_json(_presets_path())
    data.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return data


@router.post("/presets", response_model=PresetResponse)
def save_preset(req: PresetSaveRequest):
    """Save or update a named preset. If name exists, it updates in place."""
    data = _load_json(_presets_path())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    # Check for existing preset with same name
    existing = next((p for p in data if p["name"] == req.name), None)
    if existing:
        existing["settings"] = req.settings
        existing["updated_at"] = now
        entry = existing
    else:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "name": req.name,
            "settings": req.settings,
            "created_at": now,
            "updated_at": now,
        }
        data.append(entry)

    _save_json(_presets_path(), data)
    log.info("Saved preset: %s", req.name)
    return entry


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str):
    """Delete a preset by ID."""
    data = _load_json(_presets_path())
    before = len(data)
    data = [p for p in data if p["id"] != preset_id]
    if len(data) == before:
        raise HTTPException(status_code=404, detail=f"Preset not found: {preset_id}")
    _save_json(_presets_path(), data)
    return {"status": "deleted", "id": preset_id}


@router.get("/history", response_model=list[VideoHistoryEntry])
def list_history():
    """List video generation history with settings — newest first."""
    data = _load_json(_history_path())
    data.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return data


@router.get("/history/{history_id}", response_model=VideoHistoryEntry)
def get_history_entry(history_id: str):
    """Get a single history entry by ID."""
    data = _load_json(_history_path())
    entry = next((h for h in data if h["id"] == history_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"History entry not found: {history_id}")
    return entry


@router.delete("/history/{history_id}")
def delete_history_entry(history_id: str):
    """Delete a history entry."""
    data = _load_json(_history_path())
    before = len(data)
    data = [h for h in data if h["id"] != history_id]
    if len(data) == before:
        raise HTTPException(status_code=404, detail=f"History entry not found: {history_id}")
    _save_json(_history_path(), data)
    return {"status": "deleted", "id": history_id}


# ── Image management ────────────────────────────────────────────────────────

class ImageSearchRequest(BaseModel):
    query: str = Field(..., description="Search term for Pexels image search")
    count: int = Field(6, ge=1, le=20)


class ImageResult(BaseModel):
    source_id: str
    title: str
    url: str
    preview_url: str
    width: int = 0
    height: int = 0


class ImageUploadResponse(BaseModel):
    filename: str
    path: str
    size_kb: float


@router.post("/search-images", response_model=list[ImageResult])
def search_images(req: ImageSearchRequest):
    """Search Pexels for stock images to use as video backgrounds."""
    from app.services.media.image_source import search_pexels_images

    results = search_pexels_images(req.query, count=req.count)
    return [ImageResult(**r) for r in results]


@router.post("/upload-image", response_model=ImageUploadResponse)
async def upload_image(file: UploadFile = File(...)):
    """Upload an image file to use as a video background."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXT))}",
        )

    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).stem}{ext}"
    dest = _image_uploads_dir() / safe_name

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size_kb = round(dest.stat().st_size / 1024, 1)
    log.info("Image uploaded: %s (%.1f KB)", safe_name, size_kb)
    return ImageUploadResponse(filename=safe_name, path=str(dest), size_kb=size_kb)


def _clip_uploads_dir() -> Path:
    d = Path(get_settings().exports_dir).parent / "media-library" / "test_clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/upload-clip", response_model=ImageUploadResponse)
async def upload_clip(file: UploadFile = File(...)):
    """Upload a video clip (.mp4, .mov, .mpeg, etc.) to the media library."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_CLIP_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported clip format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_CLIP_EXT))}",
        )
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).stem}{ext}"
    dest = _clip_uploads_dir() / safe_name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    size_kb = round(dest.stat().st_size / 1024, 1)
    log.info("Clip uploaded: %s (%.1f KB)", safe_name, size_kb)
    return ImageUploadResponse(filename=safe_name, path=str(dest), size_kb=size_kb)


@router.get("/clip-preview/{filename}")
def clip_preview(filename: str):
    """Serve a video clip for preview."""
    safe = Path(filename).name
    clip_dir = _clip_uploads_dir()
    p = clip_dir / safe
    if p.exists() and p.is_file():
        mt = "video/mp4"
        if safe.endswith(".mov"):
            mt = "video/quicktime"
        elif safe.endswith((".mpeg", ".mpg")):
            mt = "video/mpeg"
        elif safe.endswith(".webm"):
            mt = "video/webm"
        return FileResponse(path=str(p), media_type=mt)
    # glob fallback for UUID-prefixed names
    matches = list(clip_dir.glob(f"*_{safe}"))
    if matches:
        found = matches[0]
        mt = "video/mp4"
        if found.name.endswith(".mov"):
            mt = "video/quicktime"
        elif found.name.endswith((".mpeg", ".mpg")):
            mt = "video/mpeg"
        elif found.name.endswith(".webm"):
            mt = "video/webm"
        return FileResponse(path=str(found), media_type=mt)
    raise HTTPException(status_code=404, detail=f"Clip not found: {filename}")


@router.get("/images")
def list_images():
    """List all available images (uploaded + downloaded from Pexels)."""
    images = []
    # Check image uploads
    uploads = _image_uploads_dir()
    for f in uploads.glob("*"):
        if f.suffix.lower() in ALLOWED_IMAGE_EXT:
            images.append({
                "filename": f.name,
                "path": str(f),
                "size_kb": round(f.stat().st_size / 1024, 1),
                "source": "upload",
                "preview_url": f"/video/image-preview/{f.name}",
            })
    # Check media-library/images
    lib_images = Path(get_settings().media_library_dir) / "images"
    if lib_images.exists():
        for f in lib_images.glob("*"):
            if f.suffix.lower() in ALLOWED_IMAGE_EXT:
                images.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "source": "pexels",
                    "preview_url": f"/video/image-preview/{f.name}",
                })
    # Include video clips from media-library/test_clips
    clip_dir = _clip_uploads_dir()
    if clip_dir.exists():
        for f in clip_dir.glob("*"):
            if f.suffix.lower() in ALLOWED_CLIP_EXT:
                images.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "source": "clip",
                    "preview_url": f"/video/clip-preview/{f.name}",
                    "type": "clip",
                })
    return images


@router.get("/image-preview/{filename}")
def image_preview(filename: str):
    """Serve an image for preview in the UI."""
    safe = Path(filename).name

    def _guess_media_type(name: str) -> str:
        if name.endswith(".png"):
            return "image/png"
        if name.endswith(".webp"):
            return "image/webp"
        return "image/jpeg"

    # Check uploads first (exact match)
    p = _image_uploads_dir() / safe
    if p.exists() and p.is_file():
        return FileResponse(path=str(p), media_type=_guess_media_type(safe))

    # Glob fallback for UUID-prefixed uploads (e.g. "img.png" matches "3122ccc9_img.png")
    matches = list(_image_uploads_dir().glob(f"*_{safe}"))
    if matches:
        found = matches[0]
        return FileResponse(path=str(found), media_type=_guess_media_type(found.name))

    # Check media-library/images (exact match)
    lib = Path(get_settings().media_library_dir) / "images" / safe
    if lib.exists() and lib.is_file():
        return FileResponse(path=str(lib), media_type=_guess_media_type(safe))

    raise HTTPException(status_code=404, detail=f"Image not found: {filename}")


# ── YouTube OAuth2 ──────────────────────────────────────────────────────────

@router.get("/youtube/status")
def youtube_status():
    """Check whether YouTube OAuth is set up and authenticated."""
    from app.services.social.youtube import is_authenticated
    settings = get_settings()
    has_oauth = bool(settings.youtube_client_id and settings.youtube_client_secret)
    has_api_key = bool(settings.youtube_api_key)
    configured = has_oauth or has_api_key
    return {
        "configured": configured,
        "has_oauth": has_oauth,
        "has_api_key": has_api_key,
        "authenticated": is_authenticated() if configured else False,
    }


@router.get("/youtube/auth")
def youtube_auth(
    request: Request,
    redirect_uri: str | None = Query(None),
):
    """Return the Google OAuth2 consent URL for YouTube upload permissions."""
    from app.services.social.youtube import get_auth_url
    callback_uri = redirect_uri or str(request.url_for("youtube_callback"))
    try:
        url = get_auth_url(callback_uri)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"auth_url": url, "redirect_uri": callback_uri}


@router.get("/youtube/callback")
def youtube_callback(
    request: Request,
    code: str = Query(...),
    redirect_uri: str | None = Query(None),
):
    """Handle the OAuth2 callback — exchange the code for tokens."""
    from app.services.social.youtube import exchange_code
    callback_uri = redirect_uri or str(request.url_for("youtube_callback"))
    try:
        tokens = exchange_code(code, callback_uri)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")
    return {"status": "authenticated", "message": "YouTube tokens saved. You can now upload videos."}


# ── Publish ─────────────────────────────────────────────────────────────────

@router.post("/publish", response_model=PublishResponse)
def publish_video(req: PublishRequest):
    """Publish a video.

    - **youtube**: Auto-uploads if OAuth is authenticated.
    - **tiktok / instagram**: Returns download link + copy-ready caption + posting instructions.
    """
    from app.services.social.youtube import is_authenticated as yt_authed, upload_video as yt_upload
    from app.services.social.captions import (
        build_youtube_description,
        build_tiktok_caption,
        build_instagram_caption,
        TIKTOK_INSTRUCTIONS,
        INSTAGRAM_INSTRUCTIONS,
    )

    video = Path(req.video_path)
    if not video.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {req.video_path}")

    download_url = f"/video/download/{video.name}"
    results: list[PublishResult] = []

    for platform in req.platforms:
        p = platform.lower().strip()

        if p == "youtube":
            if not yt_authed():
                results.append(PublishResult(
                    platform="youtube",
                    status="not_authenticated",
                    message="YouTube not authenticated. Click 'Connect YouTube' to set up OAuth first.",
                ))
            else:
                try:
                    yt_desc = build_youtube_description(req.title, req.description, req.tags)
                    result = yt_upload(
                        video_path=str(video),
                        title=req.title or video.stem,
                        description=yt_desc,
                        tags=req.tags,
                        privacy=req.youtube_privacy,
                    )
                    results.append(PublishResult(
                        platform="youtube",
                        status="uploaded",
                        message=f"Uploaded to YouTube as '{req.title or video.stem}'",
                        url=result.get("url"),
                    ))
                except Exception as e:
                    log.exception("YouTube upload failed")
                    results.append(PublishResult(
                        platform="youtube",
                        status="error",
                        message=f"Upload failed: {e}",
                    ))

        elif p == "tiktok":
            caption = build_tiktok_caption(req.title, req.description, req.tags)
            results.append(PublishResult(
                platform="tiktok",
                status="manual",
                message="Download the video and post manually on TikTok.",
                download_url=download_url,
                caption=caption,
                instructions=TIKTOK_INSTRUCTIONS,
            ))

        elif p == "instagram":
            caption = build_instagram_caption(req.title, req.description, req.tags)
            results.append(PublishResult(
                platform="instagram",
                status="manual",
                message="Download the video and post as an Instagram Reel.",
                download_url=download_url,
                caption=caption,
                instructions=INSTAGRAM_INSTRUCTIONS,
            ))

        else:
            results.append(PublishResult(
                platform=p,
                status="error",
                message=f"Platform '{p}' is not supported. Use: youtube, tiktok, instagram.",
            ))

    return PublishResponse(results=results)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_audio(audio_path: str) -> Path:
    """Resolve an audio path (absolute or relative to ai-engine root)."""
    audio = Path(audio_path)
    if not audio.is_absolute():
        audio = Path(get_settings().exports_dir).parent / audio
    if not audio.exists():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_path}")
    return audio


def _run_generate(db: Session, config: VideoConfig, duration_ms: int, settings: dict | None = None) -> VideoGenerateResponse:
    """Run the video engine and return a response model."""
    engine = _get_engine()
    try:
        result = engine.generate_video(db, config)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    stat = result.stat()

    # Auto-save to history
    if settings is not None:
        try:
            history = _load_json(_history_path())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            history.append({
                "id": uuid.uuid4().hex[:12],
                "filename": result.name,
                "video_path": str(result),
                "settings": settings,
                "created_at": now,
            })
            # Keep last 100 entries
            if len(history) > 100:
                history = history[-100:]
            _save_json(_history_path(), history)
        except Exception:
            log.warning("Failed to save history entry for %s", result.name, exc_info=True)

    # Auto-register in media_assets
    try:
        asset = MediaAsset(
            source="generated",
            source_id=result.name,
            title=result.name,
            url=f"/video/download/{result.name}",
            local_path=str(result),
            tags=json.dumps(["video", "generated"]),
            duration=duration_ms / 1000.0,
            downloaded=True,
        )
        db.add(asset)
        db.commit()
    except Exception:
        log.warning("Failed to register generated video %s in media_assets", result.name, exc_info=True)

    return VideoGenerateResponse(
        video_path=str(result),
        filename=result.name,
        size_kb=round(stat.st_size / 1024, 1),
        duration_ms=duration_ms,
    )


# ── Provider Icon Management ────────────────────────────────────────


def _icon_dir() -> Path:
    d = Path(get_settings().exports_dir) / "provider_icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/provider-icons")
async def list_provider_icons():
    """List all uploaded provider icons."""
    icons = []
    for f in sorted(_icon_dir().iterdir()):
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
            icons.append({
                "filename": f.name,
                "url": f"/exports/provider_icons/{f.name}",
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
    return icons


@router.post("/upload-provider-icon")
async def upload_provider_icon(file: UploadFile = File(...)):
    """Upload a provider/label icon image."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files are allowed")
    safe_name = file.filename.replace(" ", "_").replace("/", "_")
    dest = _icon_dir() / safe_name
    with open(dest, "wb") as out:
        content = await file.read()
        if len(content) > 2 * 1024 * 1024:  # 2MB limit
            raise HTTPException(400, "Icon must be under 2MB")
        out.write(content)
    return {"filename": safe_name, "url": f"/exports/provider_icons/{safe_name}"}


@router.delete("/provider-icon/{filename}")
async def delete_provider_icon(filename: str):
    """Delete a provider icon."""
    safe = Path(filename).name  # prevent path traversal
    target = _icon_dir() / safe
    if not target.exists():
        raise HTTPException(404, "Icon not found")
    target.unlink()
    return {"deleted": safe}


# ── Daily Shorts Queue Management ───────────────────────────────────────


def _shorts_queue_path() -> Path:
    return Path(get_settings().exports_dir) / "daily_shorts_queue.json"


def _shorts_config_path() -> Path:
    return Path(get_settings().exports_dir) / "daily_shorts_config.json"


def _load_shorts_queue() -> list[dict]:
    p = _shorts_queue_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []


def _save_shorts_queue(queue: list[dict]) -> None:
    p = _shorts_queue_path()
    p.write_text(json.dumps(queue, indent=2))


@router.get("/daily-shorts/queue")
async def get_daily_shorts_queue(
    status: str | None = Query(None, description="Filter by status: pending, approved, posted, rejected"),
    date: str | None = Query(None, description="Filter by generated_date (YYYY-MM-DD)"),
):
    """List all items in the daily shorts approval queue."""
    queue = _load_shorts_queue()
    if status:
        queue = [e for e in queue if e.get("status") == status]
    if date:
        queue = [e for e in queue if e.get("generated_date") == date]
    return {"queue": queue, "total": len(queue)}


@router.get("/daily-shorts/queue/{entry_id}")
async def get_daily_short_entry(entry_id: str):
    """Get a single queue entry by ID."""
    queue = _load_shorts_queue()
    entry = next((e for e in queue if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(404, "Queue entry not found")
    return entry


@router.post("/daily-shorts/queue/{entry_id}/approve")
async def approve_daily_short(entry_id: str):
    """Approve a pending short for scheduled posting."""
    queue = _load_shorts_queue()
    entry = next((e for e in queue if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(404, "Queue entry not found")
    if entry["status"] not in ("pending", "rejected"):
        raise HTTPException(400, f"Cannot approve entry with status '{entry['status']}'")
    entry["status"] = "approved"
    entry["approved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    _save_shorts_queue(queue)
    return {"id": entry_id, "status": "approved"}


@router.post("/daily-shorts/queue/{entry_id}/reject")
async def reject_daily_short(entry_id: str):
    """Reject a pending short (won't be posted)."""
    queue = _load_shorts_queue()
    entry = next((e for e in queue if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(404, "Queue entry not found")
    entry["status"] = "rejected"
    _save_shorts_queue(queue)
    return {"id": entry_id, "status": "rejected"}


@router.delete("/daily-shorts/queue/{entry_id}")
async def delete_daily_short(entry_id: str):
    """Delete a queue entry and optionally its video file."""
    queue = _load_shorts_queue()
    entry = next((e for e in queue if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(404, "Queue entry not found")
    # Remove video file if it exists
    video = Path(entry.get("video_path", ""))
    if video.exists():
        video.unlink()
    queue = [e for e in queue if e["id"] != entry_id]
    _save_shorts_queue(queue)
    return {"deleted": entry_id}


@router.get("/daily-shorts/config")
async def get_daily_shorts_config():
    """Return the current daily shorts catalog configuration."""
    p = _shorts_config_path()
    if not p.exists():
        raise HTTPException(404, "daily_shorts_config.json not found")
    return json.loads(p.read_text())


@router.put("/daily-shorts/config")
async def update_daily_shorts_config(config: dict):
    """Update the daily shorts catalog configuration."""
    p = _shorts_config_path()
    p.write_text(json.dumps(config, indent=2))
    return {"success": True}


@router.post("/daily-shorts/generate-now")
async def trigger_daily_shorts_now(db: Session = Depends(get_db)):
    """Manually trigger daily shorts generation (bypasses scheduler)."""
    from app.jobs.scheduler import _run_daily_shorts_generation
    try:
        _run_daily_shorts_generation()
        queue = _load_shorts_queue()
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        todays = [e for e in queue if e.get("generated_date") == today]
        return {"success": True, "generated_today": len(todays), "queue": todays}
    except Exception as exc:
        raise HTTPException(500, f"Generation failed: {exc}")


@router.get("/daily-shorts/preview/{entry_id}")
async def preview_daily_short(entry_id: str):
    """Serve the video file for preview/playback."""
    queue = _load_shorts_queue()
    entry = next((e for e in queue if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(404, "Queue entry not found")
    video = Path(entry.get("video_path", ""))
    if not video.exists():
        raise HTTPException(404, "Video file not found")
    return FileResponse(str(video), media_type="video/mp4", filename=entry.get("filename", video.name))


@router.put("/daily-shorts/track/{track_id}/images")
async def set_track_images(track_id: str, body: dict):
    """Set curated images for a specific track.

    Body: {"images": ["filename1.png", "filename2.jpg"]}
    """
    p = _shorts_config_path()
    if not p.exists():
        raise HTTPException(404, "Config not found")
    config = json.loads(p.read_text())
    track = next((t for t in config.get("tracks", []) if t["id"] == track_id), None)
    if not track:
        raise HTTPException(404, f"Track '{track_id}' not found")
    track["images"] = body.get("images", [])
    p.write_text(json.dumps(config, indent=2))
    return {"track_id": track_id, "images": track["images"]}


@router.get("/daily-shorts/track/{track_id}/images")
async def get_track_images(track_id: str):
    """Get curated images for a specific track."""
    p = _shorts_config_path()
    if not p.exists():
        raise HTTPException(404, "Config not found")
    config = json.loads(p.read_text())
    track = next((t for t in config.get("tracks", []) if t["id"] == track_id), None)
    if not track:
        raise HTTPException(404, f"Track '{track_id}' not found")
    return {"track_id": track_id, "images": track.get("images", [])}


@router.put("/daily-shorts/image-pool")
async def update_image_pool(body: dict):
    """Update the approved global image pool.

    Body: {"image_pool": ["filename1.png", "filename2.jpg"]}
    """
    p = _shorts_config_path()
    if not p.exists():
        raise HTTPException(404, "Config not found")
    config = json.loads(p.read_text())
    config["image_pool"] = body.get("image_pool", [])
    p.write_text(json.dumps(config, indent=2))
    return {"image_pool": config["image_pool"]}


# ── Track Memory (per-track MD files with frontmatter) ──────────────────

def _track_memory_dir() -> Path:
    d = Path(get_settings().exports_dir) / "track_memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_track_md(text: str) -> dict:
    """Parse a track memory MD file into {meta: {...}, body: str}."""
    import yaml
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            return {"meta": meta, "body": body}
    return {"meta": {}, "body": text.strip()}


def _serialize_track_md(meta: dict, body: str) -> str:
    """Serialize meta + body back to frontmatter MD."""
    import yaml
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n\n{body}\n"


@router.get("/daily-shorts/track-memory")
def list_track_memories():
    """List all track memory files with their metadata."""
    d = _track_memory_dir()
    results = []
    for f in sorted(d.glob("*.md")):
        parsed = _parse_track_md(f.read_text(encoding="utf-8"))
        results.append({
            "track_id": parsed["meta"].get("track_id", f.stem),
            "title": parsed["meta"].get("title", f.stem),
            "meta": parsed["meta"],
            "has_body": bool(parsed["body"]),
        })
    return results


@router.get("/daily-shorts/track-memory/{track_id}")
def get_track_memory(track_id: str):
    """Get full track memory (meta + body) for a track."""
    safe = Path(track_id).stem  # prevent path traversal
    p = _track_memory_dir() / f"{safe}.md"
    if not p.exists():
        raise HTTPException(404, f"No memory file for track '{track_id}'")
    parsed = _parse_track_md(p.read_text(encoding="utf-8"))
    return {"track_id": track_id, **parsed}


@router.put("/daily-shorts/track-memory/{track_id}")
async def update_track_memory(track_id: str, body: dict):
    """Update or create a track memory file.

    Body: {"meta": {...}, "body": "markdown text"}
    """
    safe = Path(track_id).stem
    meta = body.get("meta", {})
    md_body = body.get("body", "")
    meta.setdefault("track_id", safe)
    p = _track_memory_dir() / f"{safe}.md"
    p.write_text(_serialize_track_md(meta, md_body), encoding="utf-8")
    log.info("Track memory updated: %s", safe)
    return {"track_id": safe, "meta": meta, "body": md_body}


# ── Social Posting History ──────────────────────────────────────────────

def _posting_history_path() -> Path:
    return Path(get_settings().exports_dir) / "posting_history.json"


@router.get("/daily-shorts/posting-history")
def get_posting_history():
    """Get full posting history across all platforms."""
    p = _posting_history_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


@router.post("/daily-shorts/posting-history")
async def add_posting_record(body: dict):
    """Add a posting record. Body: {track_id, platform, status, video_id, ...}"""
    p = _posting_history_path()
    history = []
    if p.exists():
        try:
            history = json.loads(p.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    from datetime import datetime, timezone
    body.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    history.append(body)
    p.write_text(json.dumps(history, indent=2))
    return {"ok": True, "total": len(history)}


@router.put("/daily-shorts/posting-history/{index}")
async def update_posting_record(index: int, body: dict):
    """Update a posting record by index (0-based).

    Use to add manual platform URLs, video IDs, or stats for TikTok/Instagram.
    Body: partial dict merged into existing record.
    """
    p = _posting_history_path()
    history = []
    if p.exists():
        try:
            history = json.loads(p.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    if index < 0 or index >= len(history):
        raise HTTPException(404, f"Record index {index} out of range (0-{len(history) - 1})")
    history[index].update(body)
    p.write_text(json.dumps(history, indent=2))
    return {"ok": True, "record": history[index]}


@router.post("/daily-shorts/refresh-stats")
async def refresh_platform_stats():
    """Fetch latest stats from YouTube for all posted videos and update posting history.

    TikTok/Instagram stats must be entered manually.
    Returns summary of updates made.
    """
    from app.services.social.youtube import fetch_video_stats

    p = _posting_history_path()
    history = []
    if p.exists():
        try:
            history = json.loads(p.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    # Collect YouTube video IDs
    yt_map = {}  # video_id -> list of record indices
    for i, rec in enumerate(history):
        if rec.get("platform") == "youtube" and rec.get("video_id"):
            vid = rec["video_id"]
            yt_map.setdefault(vid, []).append(i)

    updated = 0
    if yt_map:
        stats = fetch_video_stats(list(yt_map.keys()))
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for vid, indices in yt_map.items():
            if vid in stats:
                for idx in indices:
                    history[idx]["stats"] = stats[vid]
                    history[idx]["stats_updated"] = now
                    updated += 1

    if updated:
        p.write_text(json.dumps(history, indent=2))
    log.info("Stats refresh: %d YouTube records updated", updated)
    return {"updated": updated, "youtube_videos_checked": len(yt_map)}
