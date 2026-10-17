from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.utils.logger import setup_logging
from app.api import auth, fanpages, publish_jobs, settings as settings_router, jobs, ig_sources, dashboard, notifications, logs, news_sources, gallery, templates, yt_clips, scraper_health, analytics, strategy, hermes, ai_video_ideas, gflow_worker, ai_video_jobs, radar, f1_drivers, labeler, media_thumb, research, content_projects, social_media


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title="IG → FB Auto Reposter",
    description="Multi-fanpage auto reposter from Instagram to Facebook via Repliz",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(fanpages.router)
app.include_router(publish_jobs.router)
app.include_router(settings_router.router)
app.include_router(jobs.router)
app.include_router(ig_sources.router)
app.include_router(dashboard.router)
app.include_router(scraper_health.router)
app.include_router(radar.router)
app.include_router(f1_drivers.router, prefix="/f1-drivers", tags=["f1-drivers"])
app.include_router(notifications.router)
app.include_router(logs.router)
app.include_router(news_sources.router)
app.include_router(gallery.router)
app.include_router(templates.router)
app.include_router(yt_clips.router)
app.include_router(analytics.router)
app.include_router(strategy.router)
app.include_router(hermes.router)
app.include_router(ai_video_ideas.router)
app.include_router(gflow_worker.router)
app.include_router(ai_video_jobs.router)
app.include_router(labeler.router)
app.include_router(research.router)
app.include_router(media_thumb.router)
app.include_router(content_projects.router)
app.include_router(social_media.router)


@app.get("/health")
def health():
    return {"status": "ok"}
