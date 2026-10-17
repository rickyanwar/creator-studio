from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.gallery import ImportLinksBody, get_import_links_status, start_import_links
from app.tasks.celery_app import celery_app


def test_import_task_is_registered_on_gallery_queue():
    assert "app.tasks.gallery_link_import" in celery_app.conf.include
    assert celery_app.conf.task_routes["app.tasks.gallery_link_import.*"] == {"queue": "gallery"}


def test_import_endpoint_rejects_more_than_twenty_links():
    body = ImportLinksBody(links="\n".join(f"https://example.test/{i}.jpg" for i in range(21)))
    with pytest.raises(HTTPException) as error:
        start_import_links(body, MagicMock(), MagicMock())
    assert error.value.status_code == 400


def test_import_endpoint_dispatches_task():
    body = ImportLinksBody(links="https://example.test/a.jpg", keyword="rider")
    task = MagicMock(id="task-1")
    with patch("app.tasks.gallery_link_import.import_gallery_links.delay", return_value=task) as dispatch:
        result = start_import_links(body, MagicMock(), MagicMock())
    dispatch.assert_called_once_with(["https://example.test/a.jpg"], "rider", None)
    assert result == {"task_id": "task-1"}


def test_import_status_returns_result():
    result = MagicMock(state="SUCCESS", ready=lambda: True, failed=lambda: False, result={"results": []})
    with patch("celery.result.AsyncResult", return_value=result):
        assert get_import_links_status("task-1", MagicMock()) == {"state": "SUCCESS", "results": []}


def test_phone_delivery_tasks_have_their_own_queue():
    from app.tasks.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["app.tasks.social_delivery.dispatch_social_deliveries"] == {"queue": "delivery"}
    assert routes["app.tasks.phone_watchdog.check_phone_devices"] == {"queue": "delivery"}
