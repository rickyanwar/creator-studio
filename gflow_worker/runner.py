from datetime import datetime, timezone
import logging
from pathlib import Path
import random
import re
import threading
import time

from .api import JobLost, TransientApiError, UploadTooLarge
from .gflow import classify
from .state import JobState

log = logging.getLogger(__name__)

SUBMIT_NO_REPLY = "of clicking submit"
SUBMITTED_ERRORS = ("clicking submit", "no signed media URL")   # Flow got the prompt; the clip may still appear
STRANDED_WAIT_S = 240        # how long to wait for a stranded clip to finish
STRANDED_POLL_S = 30         # interval in seconds between checks for a stranded clip
PRE_SUBMIT_RETRY_DELAY_S = (45, 120)   # random pause before the one in-process retry (anti-bot jitter)
PRE_SUBMIT_EXIT_CODES = (9, 23)        # transport timeout, UI selector drift
SEED_RETRY_EXTRA_MS = 200    # take the retry seed this much earlier
SUCCESS = "MEDIA_GENERATION_STATUS_SUCCESSFUL"  # status indicating successful clip generation
FAILED = "MEDIA_GENERATION_STATUS_FAILED"        # status indicating failed clip generation


class JobRunner:
    def __init__(self, cfg, api, gflow, media, sleep=None):
        self.cfg, self.api, self.gflow, self.media = cfg, api, gflow, media
        self._custom_sleep = sleep
        self.cancel_ids = set()
        self.busy_profiles = set()
        self.credits_stale = False
        self.stale_profiles = set()  # profiles whose balance should be re-read after a no-reply submit
        self.lock = threading.Lock()

    def _sleep(self, seconds):
        if self._custom_sleep is not None:
            self._custom_sleep(seconds)
        else:
            time.sleep(seconds)

    def _fail(self, jid, generation, result, profile=None):
        """Report a failed CLI step; a submit that got no usable reply often means the account ran out of credits."""
        if SUBMIT_NO_REPLY in (result.message or ""):
            self.credits_stale = True
            if profile:
                with self.lock:
                    self.stale_profiles.add(profile)
        self.api.fail(jid, generation, classify(result), result.message)

    def _find_media_id(self, result, *, profile=None, project=None, submit_time=None, stranded=True):
        if isinstance(result.payload, dict):
            media_id = result.payload.get("media_id")
            if not media_id and isinstance(result.payload.get("error"), dict):
                media_id = result.payload["error"].get("media_id")
            if not media_id and isinstance(result.payload.get("asset"), dict):
                media_id = result.payload["asset"].get("media_id")
            if not media_id:
                media_id = result.payload.get("workflow_id")
            if media_id:
                return str(media_id)
        if result.message:
            match = re.search(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", result.message)
            if match:
                return match.group(1)
        if stranded and hasattr(self.gflow, "find_stranded_media_id"):
            return self.gflow.find_stranded_media_id(profile=profile, project=project, submit_time=submit_time)
        return None

    def _recover_clip(self, result, *, jid, index, profile, project, submit_time, out, state=None):
        msg = result.message or ""
        if not any(err in msg for err in SUBMITTED_ERRORS):
            return None

        # Try payload/message first (existing first two branches, not stranded search)
        media_id = self._find_media_id(result, stranded=False)
        if media_id:
            rec = self.gflow.download_media(media_id, profile=profile, out=out.parent)
            rec_path = Path(rec.payload.get("path") or rec.payload.get("local_path") or (out.parent / f"{media_id}.mp4"))
            if not rec_path.is_absolute():
                rec_path = self.cfg.cli_dir / rec_path
            if rec.ok and rec_path.is_file() and rec_path.stat().st_size:
                log.info("Recovered clip %s for job %s clip %s via data download (%s)", media_id, jid, index, rec_path)
                rec.payload.setdefault("local_path", str(rec_path))
                rec.payload.setdefault("media_id", media_id)
                return rec
            log.warning("Recovery failed for job %s clip %s (media_id=%s): %s", jid, index, media_id, rec.message or "file missing")

        if hasattr(self.gflow, "stranded_candidates"):
            elapsed = 0
            tried = set()
            attempt = 1
            while elapsed <= STRANDED_WAIT_S:
                if self.cancelled(jid):
                    return None
                for row in self.gflow.stranded_candidates(profile=profile, project=project, submit_time=submit_time):
                    mid = row.get("media_id")
                    if not mid:
                        continue
                    rec = self.gflow.download_media(mid, profile=profile, out=out.parent)
                    rec_path = Path(rec.payload.get("path") or rec.payload.get("local_path") or (out.parent / f"{mid}.mp4"))
                    if not rec_path.is_absolute():
                        rec_path = self.cfg.cli_dir / rec_path
                    if rec.ok and rec_path.is_file() and rec_path.stat().st_size:
                        log.info(
                            "Recovered clip %s for job %s clip %s via stranded download (attempt %d)",
                            mid, jid, index, attempt,
                        )
                        rec.payload.setdefault("local_path", str(rec_path))
                        rec.payload.setdefault("media_id", mid)
                        return rec
                    tried.add(mid)
                if elapsed + STRANDED_POLL_S > STRANDED_WAIT_S:
                    break
                self._sleep(STRANDED_POLL_S)
                elapsed += STRANDED_POLL_S
                attempt += 1
                if self.cancelled(jid):
                    return None
            if project and hasattr(self.gflow, "recover_from_project"):
                if self.cancelled(jid):
                    return None
                exclude = []
                if state and isinstance(state.data.get("clips"), dict):
                    exclude = [
                        v["media_id"]
                        for v in state.data["clips"].values()
                        if isinstance(v, dict) and "media_id" in v
                    ]
                if not exclude:
                    for p in (self.cfg.work_dir / "jobs").glob(f"{jid}-g*/state.json"):
                        try:
                            d = json.loads(p.read_text())
                            if isinstance(d.get("clips"), dict):
                                exclude.extend([
                                    v["media_id"]
                                    for v in d["clips"].values()
                                    if isinstance(v, dict) and "media_id" in v
                                ])
                        except Exception:
                            pass
                sub_epoch = (
                    int(submit_time.timestamp())
                    if isinstance(submit_time, datetime)
                    else int(float(submit_time))
                )
                rec = self.gflow.recover_from_project(
                    profile=profile,
                    project=project,
                    submit_time=sub_epoch,
                    out_dir=out.parent,
                    exclude=exclude,
                )
                if self.cancelled(jid):
                    return None
                if rec and rec.ok:
                    mid = rec.payload.get("media_id")
                    rec_path = Path(
                        rec.payload.get("local_path")
                        or rec.payload.get("path")
                        or (out.parent / f"{mid}.mp4")
                    )
                    if not rec_path.is_absolute():
                        rec_path = self.cfg.cli_dir / rec_path
                    if rec_path.is_file() and rec_path.stat().st_size:
                        log.info(
                            "Recovered clip %s for job %s clip %s from the Flow project listing",
                            mid,
                            jid,
                            index,
                        )
                        rec.payload.setdefault("local_path", str(rec_path))
                        rec.payload.setdefault("media_id", mid)
                        return rec
            log.warning(
                "No stranded clip could be downloaded for job %s clip %s within %ss (candidates tried: %d)",
                jid, index, STRANDED_WAIT_S, len(tried),
            )
            return None

        return None

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
        if not job.get("clips_done"):
            stored_profile = state.data.get("profile")
            current_profile = job["account"]["profile"]
            if (stored_profile and stored_profile != current_profile) or (
                job.get("flow_project_id") is None and stored_profile != current_profile
            ):
                state.data["project_id"] = None
                state.data["clips"] = {}
                state.save()
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
            # Flow can create the project and then time out on a follow-up call; its id is in the error URL.
            orphan = re.search(r"/project/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", result.message or "")
            if not result.ok and not orphan:
                self._fail(jid, generation, result, profile=job["account"]["profile"])
                return
            project = result.payload.get("project_id") if result.ok else orphan.group(1)
            if orphan and not result.ok:
                log.info("Project create for job %s reported %s but created %s; reusing it", jid, result.error_class, project)
            if not project or str(project).strip() in ("", "None"):
                self.api.fail(jid, generation, "transient", "project create returned no project id")
                return
        state.data["project_id"] = project
        state.data["profile"] = job["account"]["profile"]
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
                is_t2v = (index == 1 or clip.get("mode") == "t2v")
                frame = None
                if not is_t2v:
                    frame = self.media.last_frame(paths[-1], state.directory / f"seed-{index}.jpg", self.cfg.seed_offset_ms)
                    if self.cancelled(jid):
                        return
                if job.get("clip_qc"):
                    checkpoint = self._qc_clip(job, state, index=index, clip=clip, is_t2v=is_t2v,
                                               frame=frame, project=project)
                    if checkpoint is None:
                        return
                else:
                    checkpoint = self._generate_clip(job, state, index=index, prompt=clip["prompt"],
                                                     is_t2v=is_t2v, frame=frame,
                                                     out=(state.directory / f"clip-{index}.mp4").resolve(),
                                                     project=project)
                    if checkpoint is None:
                        return
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
        cover_bg = self._cover_background(job, state, project)
        self.api.complete(jid, generation, video, thumb,
                          {"duration_s": duration, "clips": metadata},
                          cover_bg=cover_bg)
        state.data["uploaded"] = True
        state.save()

    def _generate_clip(self, job, state, *, index, prompt, is_t2v, frame, out, project):
        """Generate one clip file, preserving the existing retry and recovery behavior."""
        jid, generation = job["id"], job["generation"]
        profile = job["account"]["profile"]
        kwargs = dict(profile=profile, project=project, model=job["model"],
                      aspect=job["aspect"], duration=job["clip_seconds"], out=out)
        submit_time = datetime.now(timezone.utc)
        if is_t2v:
            result = self.gflow.t2v(prompt, **kwargs)
        else:
            result = self.gflow.i2v(prompt, frame=frame, **kwargs)
        if not result.ok:
            recovered = self._recover_clip(result, jid=jid, index=index, profile=profile,
                                           project=project, submit_time=submit_time, out=out, state=state)
            if self.cancelled(jid):
                return None
            if recovered:
                result = recovered
            else:
                msg = result.message or ""
                if (result.exit_code in PRE_SUBMIT_EXIT_CODES and
                        not any(err in msg for err in SUBMITTED_ERRORS)):
                    delay = random.uniform(*PRE_SUBMIT_RETRY_DELAY_S)
                    remaining = delay
                    while remaining > 0:
                        if self.cancelled(jid):
                            return None
                        step = min(5.0, remaining)
                        self._sleep(step)
                        remaining -= step
                    if self.cancelled(jid):
                        return None
                    submit_time = datetime.now(timezone.utc)
                    result = self.gflow.t2v(prompt, **kwargs) if is_t2v else self.gflow.i2v(prompt, frame=frame, **kwargs)
                    if self.cancelled(jid):
                        return None
                    if not result.ok:
                        recovered = self._recover_clip(result, jid=jid, index=index, profile=profile,
                                                       project=project, submit_time=submit_time, out=out, state=state)
                        if self.cancelled(jid):
                            return None
                        if recovered:
                            result = recovered
                elif (not is_t2v and result.error_class == "MediaUploadRejectedError" and
                      "upload-terms" not in msg):
                    previous = state.data.get("clips", {}).get(str(index - 1), {}).get("path")
                    frame = self.media.last_frame(previous, state.directory / f"seed-{index}-b.jpg",
                                                  self.cfg.seed_offset_ms + SEED_RETRY_EXTRA_MS)
                    if self.cancelled(jid):
                        return None
                    submit_time = datetime.now(timezone.utc)
                    result = self.gflow.i2v(prompt, frame=frame, **kwargs)
                    if self.cancelled(jid):
                        return None
                    if not result.ok:
                        recovered = self._recover_clip(result, jid=jid, index=index, profile=profile,
                                                       project=project, submit_time=submit_time, out=out, state=state)
                        if self.cancelled(jid):
                            return None
                        if recovered:
                            result = recovered
                if not result.ok:
                    self._fail(jid, generation, result, profile=profile)
                    return None
        path = Path(result.payload.get("local_path") or result.payload.get("path") or out)
        if not path.is_absolute():
            path = self.cfg.cli_dir / path
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("CLI did not write clip")
        return {"media_id": result.payload["media_id"], "path": str(path.resolve())}

    def _qc_clip(self, job, state, *, index, clip, is_t2v, frame, project):
        jid, generation = job["id"], job["generation"]
        qc = state.data.setdefault("qc", {}).setdefault(str(index), {
            "attempt": 1, "prompts": {"1": clip["prompt"]}, "files": {}, "uploaded": []
        })
        max_attempts = job.get("clip_qc_max_attempts", 4)
        waiting_reason = None
        while True:
            attempt = int(qc["attempt"])
            attempt_key = str(attempt)
            if attempt_key not in qc["files"]:
                out = (state.directory / (f"clip-{index}.mp4" if attempt == 1 else f"clip-{index}-a{attempt}.mp4")).resolve()
                result = self._generate_clip(job, state, index=index, prompt=qc["prompts"][attempt_key],
                                             is_t2v=is_t2v, frame=frame, out=out, project=project)
                if result is None:
                    return None
                qc["files"][attempt_key] = result
                state.save()
            if attempt not in qc["uploaded"]:
                self.api.clip_qc_upload(jid, generation, index, attempt, qc["prompts"][attempt_key],
                                        qc["files"][attempt_key]["path"])
                qc["uploaded"].append(attempt)
                state.save()
            try:
                response = self.api.clip_qc_status(jid, generation, index, attempt)
            except TransientApiError:
                log.warning("Clip QC status unavailable for job %s clip %s; continuing to poll", jid, index)
                response = {"state": "pending"}
            status = response.get("state")
            if status == "pending":
                reason = response.get("waiting_reason")
                if reason and reason != waiting_reason:
                    log.info("Clip QC waiting for job %s clip %s: %s", jid, index, reason)
                    waiting_reason = reason
                remaining = self.cfg.clip_qc_poll_seconds
                while remaining > 0:
                    if self.cancelled(jid):
                        return None
                    step = min(5.0, remaining)
                    self._sleep(step)
                    remaining -= step
                continue
            if status == "pass":
                return qc["files"][attempt_key]
            if status == "use_attempt":
                chosen = int(response["use_attempt"])
                log.info("Using clip QC attempt %s for job %s clip %s", chosen, jid, index)
                return qc["files"][str(chosen)]
            if status == "retry":
                next_attempt = attempt + 1
                if next_attempt > max_attempts:
                    self.api.fail(jid, generation, "other", "clip QC asked for too many attempts")
                    return None
                qc["attempt"] = next_attempt
                qc["prompts"][str(next_attempt)] = response["prompt"]
                state.save()
                continue
            if status == "failed":
                self.api.fail(jid, generation, "other", (response.get("waiting_reason") or "clip QC failed")[:500])
                return None
            self.api.fail(jid, generation, "other", f"clip QC returned an unknown state: {status!r}"[:500])
            return None

    def _resolve_cover_raw(self, result, out: Path) -> Path | None:
        payload = getattr(result, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        first = images[0] if images and isinstance(images[0], dict) else {}
        # gflow image t2i --json reports the file as images[0].local_path (seen on the VPS, v0.83.2);
        # it may write .jpg although -o says .png, so the -o variants are tried after the reported paths.
        candidates = [Path(v) for v in (payload.get("local_path"), payload.get("path"), first.get("local_path"), first.get("path")) if v]
        for cand in (*candidates, out.with_suffix(".jpg"), out.with_suffix(".png"), out):
            p = cand if cand.is_absolute() else self.cfg.cli_dir / cand
            if p.is_file() and p.stat().st_size > 0:
                return p
        return None

    def _cover_background(self, job, state, project) -> Path | None:
        jid = job.get("id")
        try:
            prompt = job.get("cover_prompt")
            if not prompt:
                return None
            cached = state.data.get("cover_bg")
            if cached:
                cached_path = Path(cached)
                if cached_path.is_file() and cached_path.stat().st_size > 0:
                    return cached_path
            min_sec, max_sec = self.cfg.cover_gap_seconds
            self._sleep(random.uniform(min_sec, max_sec))
            raw_out = (state.directory / "cover_raw.png").resolve()
            result = self.gflow.t2i(
                prompt,
                profile=job["account"]["profile"],
                project=project,
                model=self.cfg.cover_image_model,
                aspect=job["aspect"],
                out=raw_out,
            )
            if not result.ok:
                log.warning("cover t2i failed for job %s: %s", jid, getattr(result, "message", "unknown"))
                return None
            raw = self._resolve_cover_raw(result, raw_out)
            if not raw or not raw.is_file() or not raw.stat().st_size:
                log.warning("cover raw file missing or empty for job %s", jid)
                return None
            bg_path = state.directory / "cover_bg.jpg"
            self.media.to_cover_jpeg(raw, bg_path)
            state.data["cover_bg"] = str(bg_path.resolve())
            state.save()
            return bg_path
        except Exception as exc:
            log.warning("cover background failed for job %s: %s", jid, exc)
            return None
