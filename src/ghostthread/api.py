"""HTTP surface. This is what RocketRide Cloud calls and what the demo UI drives."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .contracts import ComplaintEvent
from .events import events_since, last_seq
from .intent import get_profile, push_profile
from .killshot import DEFAULT_SCOPES, run_killshot
from .pipeline import GhostThread

app = FastAPI(title="GhostThread", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = GhostThread()
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"


class RunRequest(BaseModel):
    sources: Optional[list[str]] = None
    act: bool = True


class ComplaintRequest(BaseModel):
    text: str = Field(min_length=8)
    source: str = "slack"
    author_email: str = "judge@frontiertower.dev"
    channel_or_thread: str = "#live-demo"
    sources: Optional[list[str]] = None


class KillshotRequest(BaseModel):
    scopes: Optional[list[list[str]]] = None


@app.get("/health")
def health() -> dict[str, Any]:
    profile = get_profile()
    return {
        "ok": True,
        "capabilities": config.capability_report(),
        "last_seq": last_seq(),
        "backends": {
            "grounding": engine.grounding.backend,
            "extraction": engine.extractor.backend,
            "intent_profile": profile.origin,
        },
    }


@app.post("/run")
def run(req: RunRequest) -> dict[str, Any]:
    return engine.run(sources=req.sources, act_on_leaks=req.act).to_dict()


@app.post("/killshot")
def killshot(req: KillshotRequest) -> dict[str, Any]:
    return run_killshot(engine, scopes=req.scopes or DEFAULT_SCOPES)


@app.post("/complaint")
def complaint(req: ComplaintRequest) -> dict[str, Any]:
    """Live-typed complaint. Ingested, then run through the identical pipeline."""
    if req.source not in ("slack", "gmail"):
        raise HTTPException(400, "source must be slack or gmail")

    event = ComplaintEvent(
        id=f"live-{uuid.uuid4().hex[:8]}",
        source=req.source,
        entity_id=req.author_email,
        text=req.text.strip(),
        t=time.time(),
        channel_or_thread=req.channel_or_thread,
        author_email=req.author_email,
    )
    engine.add_complaint(event)
    report = engine.run(sources=req.sources, act_on_leaks=True, only_complaint_id=event.id)
    return report.to_dict()


@app.get("/profile")
def read_profile() -> dict[str, Any]:
    return get_profile(force_refresh=True).to_dict()


@app.put("/profile")
def write_profile(body: dict[str, Any]) -> dict[str, Any]:
    result = push_profile(body)
    return {"written": result, "profile": get_profile(force_refresh=True).to_dict()}


@app.post("/reload")
def reload_corpus() -> dict[str, Any]:
    engine.__init__()  # rebuild grounding index from whatever is on disk / live
    return {"loaded": engine.load(force=True)}


@app.get("/events")
async def events(since: int = 0) -> StreamingResponse:
    """SSE stream of pipeline events. The dashboard subscribes on load and
    renders sub-agents spawning / actions landing as they happen."""

    async def stream():
        last = since
        idle_s = 0.0
        while True:
            batch = events_since(last)
            if batch:
                last = batch[-1]["seq"]
                for event in batch:
                    yield f"data: {json.dumps(event)}\n\n"
                idle_s = 0.0
            else:
                idle_s += 0.2
                if idle_s >= 15.0:
                    yield ": keepalive\n\n"
                    idle_s = 0.0
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# The built React dashboard (ghostthread/ui). Mounted last so API routes win.
if UI_DIST.exists():
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
