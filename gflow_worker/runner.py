import logging
from pathlib import Path
import threading

from .api import JobLost, TransientApiError, UploadTooLarge
from .gflow import classify
from .state import JobState

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, cfg, api, gflow, media):
        self.cfg, self.api, self.gflow, self.media = cfg, api, gflow, media
        self.cancel_ids = set()
        self.busy_profiles = set()
        self.lock = threading.Lock()

    def apply_cancels(self, ids):
        with self.lock:
            self.cancel_ids = set(ids)

    def cancelled(self, job_id):
        with self.lock:
            return job_id in self.cancel_ids

    def run_job(self, payload):
        job_id, generation = payload["id"], payload["generation"]
        profile = payload["account"]["profile"]
        acquired = False
        try:
            with self.lock:
                if profile not in self.busy_profiles:
                    self.busy_profiles.add(profile)
                    acquired = True
            if not acquired:
                self.api.fail(job_id, generation, "other", "profile busy")
                return
            self._run(payload)
        except JobLost:
            return
        except TransientApiError:
            # Keep the checkpoint; the server's lease/requeue mechanism owns retries.
            log.warning("Studio unavailable for job %s; retaining checkpoint", job_id)
        except UploadTooLarge:
            self.api.fail(job_id, generation, "other", "upload rejected: too large")
        except Exception as exc:
            log.warning("Worker processing failed for job %s (%s: %s)",
                        job_id, type(exc).__name__, str(exc)[:200])
            try:
                self.api.fail(job_id, generation, "other", "worker processing error")
            except (JobLost, TransientApiError):
                pass
        finally:
            if acquired:
                with self.lock:
                    self.busy_profiles.discard(profile)

    def _run(self, job):
        jid, generation = job["id"], job["generation"]
        if self.cancelled(jid):
            return
        state = JobState(self.cfg.work_dir, jid, generation)
        done = {str(c["index"]): c for c in job.get("clips_done", [])}
        local = state.data["clips"]
        # A checkpoint includes the file, not merely an entry in state.json.
        if any(k not in local or local[k]["media_id"] != c["media_id"] or
               not Path(local[k]["path"]).is_file() for k, c in done.items()):
            self.api.fail(jid, generation, "lost_checkpoint", "server clip checkpoint is missing locally")
            return
        project = state.data["project_id"] or job.get("flow_project_id")
        if state.data["project_id"] and job.get("flow_project_id") and state.data["project_id"] != job["flow_project_id"]:
            self.api.fail(jid, generation, "lost_checkpoint", "project checkpoint mismatch")
            return
        if not project:
            result = self.gflow.create_project(job["account"]["profile"], job["project_name"])
            if not result.ok:
                self.api.fail(jid, generation, classify(result), result.message)
                return
            project = result.payload["project_id"]
        state.data["project_id"] = project
        state.save()
        # Replaying project/clip progress recovers a lost HTTP acknowledgement.
        self.api.progress(jid, generation, "project", flow_project_id=project)
        paths, metadata = [], []
        for clip in sorted(job["clips"], key=lambda c: c["index"]):
            if self.cancelled(jid):
                return
            index = clip["index"]
            key = str(index)
            checkpoint = local.get(key)
            if checkpoint and not Path(checkpoint["path"]).is_file():
                self.api.fail(jid, generation, "lost_checkpoint", "local clip file is missing")
                return
            if not checkpoint:
                out = (state.directory / f"clip-{index}.mp4").resolve()
                kwargs = dict(profile=job["account"]["profile"], project=project, model=job["model"],
                              aspect=job["aspect"], duration=job["clip_seconds"], out=out)
                if index == 1:
                    result = self.gflow.t2v(clip["prompt"], **kwargs)
                else:
                    frame = self.media.last_frame(paths[-1], state.directory / f"seed-{index}.png", self.cfg.seed_offset_ms)
                    if self.cancelled(jid):
                        return
                    result = self.gflow.i2v(clip["prompt"], frame=frame, **kwargs)
                if not result.ok:
                    self.api.fail(jid, generation, classify(result), result.message)
                    return
                path = Path(result.payload.get("local_path") or out)
                if not path.is_absolute():
                    path = self.cfg.cli_dir / path
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError("CLI did not write clip")
                checkpoint = {"media_id": result.payload["media_id"], "path": str(path.resolve())}
                local[key] = checkpoint
                state.save()
            if self.cancelled(jid):
                return
            if key not in done:
                self.api.progress(jid, generation, "clip_done", clip_index=index, media_id=checkpoint["media_id"])
            paths.append(checkpoint["path"])
            metadata.append({"index": index, "media_id": checkpoint["media_id"]})
        video, thumb = state.directory / "video.mp4", state.directory / "thumbnail.jpg"
        if self.cancelled(jid):
            return
        self.media.concat(paths, video, trim_tail_ms=self.cfg.seed_offset_ms)
        duration = self.media.probe_duration(video)
        if video.stat().st_size > 40 * 1024 * 1024:
            self.media.reencode_for_upload(video, duration)
            if video.stat().st_size > 44 * 1024 * 1024:
                self.api.fail(jid, generation, "other", "video too large after re-encode")
                return
        self.media.thumbnail(video, thumb)
        if self.cancelled(jid):
            return
        self.api.complete(jid, generation, video, thumb,
                          {"duration_s": duration, "clips": metadata})
        state.data["uploaded"] = True
        state.save()
