from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.utils.logger import setup_logging
from app.api import auth, fanpages, burners, publish_jobs, settings as settings_router, jobs, ig_sources, dashboard, notifications, logs, news_sources, gallery, templates


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
app.include_router(burners.router)
app.include_router(publish_jobs.router)
app.include_router(settings_router.router)
app.include_router(jobs.router)
app.include_router(ig_sources.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(logs.router)
app.include_router(news_sources.router)
app.include_router(gallery.router)
app.include_router(templates.router)


@app.get("/health")
def health():
    return {"status": "ok"}
