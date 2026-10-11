import json
import os
from pathlib import Path
import shutil
import tempfile
import time


class JobState:
    def __init__(self, work_dir, job_id, generation):
        self.directory = Path(work_dir) / "jobs" / f"{int(job_id)}-g{int(generation)}"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.data = dict(job_id=job_id, generation=generation, project_id=None, clips={}, uploaded=False)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data["job_id"] != job_id or self.data["generation"] != generation:
                raise ValueError("checkpoint identity mismatch")

    def save(self):
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


def cleanup(work_dir, keep_done_days):
    cutoff = time.time() - keep_done_days * 86400
    for path in (Path(work_dir) / "jobs").glob("*/state.json"):
        try:
            if path.stat().st_mtime < cutoff and json.loads(path.read_text()).get("uploaded") is True:
                shutil.rmtree(path.parent)
        except (OSError, ValueError):
            continue
