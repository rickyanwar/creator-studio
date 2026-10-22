import fnmatch
from pathlib import Path
import yaml

from app.tasks.celery_app import celery_app
from app.tasks.crawler import crawl_all_sources, crawl_single_source, crawl_wave_step
from app.tasks.radar_crawl import radar_crawl_tick


def _route_for(task_name: str) -> str:
    for pattern, route in celery_app.conf.task_routes.items():
        if fnmatch.fnmatch(task_name, pattern):
            return route.get("queue")
    return "celery"


def test_task_routes_isolation():
    assert _route_for("app.tasks.crawler.crawl_wave_step") == "crawl"
    assert _route_for("app.tasks.crawler.crawl_single_source") == "crawl"
    assert _route_for("app.tasks.crawler.crawl_all_sources") == "crawl"
    assert _route_for("app.tasks.radar_crawl.radar_crawl_tick") == "crawl"
    assert _route_for("app.tasks.queue_watchdog.check_queue_backlog") == "delivery"


def test_time_limits():
    assert crawl_wave_step.soft_time_limit == 420
    assert crawl_wave_step.time_limit == 480

    assert crawl_single_source.soft_time_limit == 420
    assert crawl_single_source.time_limit == 480

    assert radar_crawl_tick.soft_time_limit == 1200
    assert radar_crawl_tick.time_limit == 1260


def test_beat_schedule_and_includes():
    beat_schedule = celery_app.conf.beat_schedule
    assert "check-queue-backlog" in beat_schedule
    assert beat_schedule["check-queue-backlog"]["schedule"] == 300
    assert "app.tasks.queue_watchdog" in celery_app.conf.include


def test_docker_compose_worker_crawl():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.server.yml"
    assert compose_path.exists(), f"Missing docker-compose file: {compose_path}"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data["services"]
    assert "worker-crawl" in services
    wc = services["worker-crawl"]
    assert "--queues=crawl" in wc["command"]
    assert "--concurrency=1" in wc["command"]
    assert "mem_limit" in wc
    assert "ports" not in wc

    w = services["worker"]
    assert "--queues=celery" in w["command"]
    assert "crawl" not in w["command"]
