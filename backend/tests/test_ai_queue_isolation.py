import fnmatch
from pathlib import Path
import yaml

from app.tasks.celery_app import celery_app
from app.tasks.queue_watchdog import QUEUE_BACKLOG_LIMITS, QUEUE_WORKERS


def _route_for(task_name: str) -> str:
    for pattern, route in celery_app.conf.task_routes.items():
        if fnmatch.fnmatch(task_name, pattern):
            return route.get("queue")
    return "celery"


def test_task_routes_isolation():
    assert _route_for("app.tasks.design_renderer.render_design") == "ai"
    assert _route_for("app.tasks.news_copywriter.copywrite_article") == "ai"
    assert _route_for("app.tasks.design_renderer.render_pending_designs") == "celery"
    assert _route_for("app.tasks.design_renderer.dispatch_fair_visual_news") == "celery"
    assert _route_for("app.tasks.radar_dispatch.radar_dispatch_tick") == "celery"
    assert _route_for("app.tasks.publisher.publish_job") == "celery"


def test_docker_compose_server_worker_ai():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.server.yml"
    with open(compose_path, "r", encoding="utf-8") as f:
        services = yaml.safe_load(f)["services"]

    worker_ai = services["worker-ai"]
    assert "--queues=ai" in worker_ai["command"]
    assert "--concurrency=2" in worker_ai["command"]
    assert "mem_limit" in worker_ai
    assert "ports" not in worker_ai
    assert "--queues=celery" in services["worker"]["command"]
    assert "--queues=ai" not in services["worker"]["command"]


def test_docker_compose_local_worker_ai():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    with open(compose_path, "r", encoding="utf-8") as f:
        services = yaml.safe_load(f)["services"]

    assert "--queues=ai" in services["worker-ai"]["command"]


def test_deploy_worker_lists_include_ai():
    deploy_path = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"
    lines = deploy_path.read_text(encoding="utf-8").splitlines()
    worker_lines = [line for line in lines if "worker-visual" in line]
    assert worker_lines
    assert all("worker-ai" in line for line in worker_lines)


def test_queue_watchdog_monitors_ai():
    assert QUEUE_BACKLOG_LIMITS["ai"] == 150
    assert QUEUE_WORKERS["ai"] == "worker-ai"
