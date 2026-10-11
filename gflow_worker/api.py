import json
from pathlib import Path

import httpx


class JobLost(Exception):
    pass


class TransientApiError(Exception):
    pass


class UploadTooLarge(Exception):
    pass


class StudioClient:
    def __init__(self, cfg, *, transport=None):
        self.worker_id = cfg.worker_id
        self.client = httpx.Client(base_url=cfg.studio_api_url.rstrip("/") + "/",
                                   headers={"Authorization": f"Bearer {cfg.token}"},
                                   timeout=30, transport=transport)

    def close(self):
        self.client.close()

    def _post(self, route, **kwargs):
        try:
            response = self.client.post("worker/gflow/" + route, **kwargs)
        except httpx.RequestError:
            raise TransientApiError("studio network error") from None
        if response.status_code == 409:
            raise JobLost()
        if response.status_code == 413 and route.startswith("jobs/") and route.endswith("/complete"):
            raise UploadTooLarge("upload rejected: too large")
        if response.status_code >= 500:
            raise TransientApiError("studio server error")
        response.raise_for_status()
        return None if response.status_code == 204 else response.json()

    def heartbeat(self, payload):
        return self._post("heartbeat", json={**payload, "worker_id": self.worker_id})

    def claim(self):
        result = self._post("claim", json={"worker_id": self.worker_id})
        return result["job"] if result else None

    def progress(self, job_id, generation, event, **data):
        return self._post(f"jobs/{job_id}/progress", json=dict(worker_id=self.worker_id, generation=generation, event=event, **data))

    def fail(self, job_id, generation, reason, message):
        return self._post(f"jobs/{job_id}/fail", json=dict(worker_id=self.worker_id, generation=generation, reason=reason, message=message))

    def complete(self, job_id, generation, video, thumbnail, meta):
        with Path(video).open("rb") as v, Path(thumbnail).open("rb") as t:
            return self._post(f"jobs/{job_id}/complete", timeout=600,
                              data={"worker_id": self.worker_id, "generation": str(generation), "meta": json.dumps(meta)},
                              files={"video": ("video.mp4", v, "video/mp4"), "thumbnail": ("thumbnail.jpg", t, "image/jpeg")})
