from datetime import datetime, timezone
import hashlib
import html
import logging
import re
from typing import Any

from phone_runner.adb import (
    AdbResult,
    is_device_secure_locked,
    launch_facebook_feed,
    ping_portal,
    put_to_sleep,
    query_activity_state,
    query_ui_hierarchy,
    run_adb,
    wake_and_unlock,
)
from phone_runner.config import sanitize_actor_name_for_prompt
from phone_runner.dry_run import (
    get_local_app_card_version,
    normalize_geometry,
    parse_device_identity_evidence,
)
from phone_runner.identity_proof import collect_own_url_proof_chain, IdentityProofResult
from phone_runner.media_staging import stage_media_files
from phone_runner.mobilerun import run_mobilerun, type_text
from phone_runner.probe import probe_device
from phone_runner.result_verification import poll_for_post_result

logger = logging.getLogger("phone_runner")


def verify_mobilerun_service(serial: str) -> tuple[bool, str]:
    """Verifies Mobilerun portal service responds with 'pong'.

    Fails closed on any nonzero return code, timeout, malformed response, or non-pong result.
    """
    try:
        ping_res = ping_portal(serial)
    except Exception as exc:
        return False, f"Mobilerun service exception: {type(exc).__name__}"

    if ping_res.returncode != 0:
        return False, f"Mobilerun service nonzero exit code {ping_res.returncode}"

    stdout = (ping_res.stdout or "").strip().lower()
    if not stdout:
        return False, "Mobilerun service malformed response: empty output"

    if "pong" not in stdout:
        return False, f"Mobilerun service non-pong result: {stdout}"

    return True, "pong"


def build_composition_prompt(
    dest_name: str,
    has_media: bool = False,
) -> str:
    """Constructs prompt for Mobilerun navigation and composer preparation.

    Safety:
    - Actor name is strictly sanitized.
    - Caption is NEVER interpolated into LLM prompt.
    - Strictly forbids typing caption text or tapping Post or Publish.
    """
    clean_dest = sanitize_actor_name_for_prompt(dest_name)
    media_clause = "select staged media attachments, " if has_media else ""

    return "\n".join([
        "NAVIGATION AND COMPOSITION ONLY. STRICT SAFETY RULE: DO NOT TAP POST OR PUBLISH.",
        f"Switch context to actor '{clean_dest}' if needed, open post composer, {media_clause}and focus the post caption text input.",
        "DO NOT enter post caption text or tap post or publish. Stop immediately once composer is open and caption input is focused.",
    ])


def parse_composer_content_evidence(
    ui_dump: str,
    expected_caption: str,
    expected_media_count: int,
) -> tuple[bool, bool, dict[str, Any]]:
    """Parses composer UI hierarchy to verify caption and attached media preview.

    Invariants:
    - Requires composer input evidence (is_caption_node).
    - Parses XML entities and newlines correctly via html.unescape.
    - Rejects matching text outside composer.
    - Requires exact observed attachment count, including expected zero.
    """
    caption_matched = False
    composer_input_found = False
    media_count_found = 0

    norm_expected = html.unescape(expected_caption or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for match in node_pattern.finditer(ui_dump or ""):
        attrs = match.group(1)
        m_res = re.search(r'resource-id="([^"]*)"', attrs, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', attrs, re.IGNORECASE)
        m_desc = re.search(r'content-desc="([^"]*)"', attrs, re.IGNORECASE)
        m_class = re.search(r'class="([^"]*)"', attrs, re.IGNORECASE)

        res_id = (m_res.group(1) if m_res else "").lower()
        raw_text = m_text.group(1) if m_text else ""
        desc = (m_desc.group(1) if m_desc else "").lower()
        cls_name = (m_class.group(1) if m_class else "").lower()

        # Identify caption input node
        is_caption_node = (
            ("composer_text_input" in res_id or "caption" in res_id or "edittext" in cls_name)
            and not any(k in res_id for k in ("search", "privacy", "audience", "filter"))
        )

        if is_caption_node:
            composer_input_found = True
            raw_unescaped = html.unescape(raw_text).replace("\r\n", "\n").replace("\r", "\n")
            norm_node_text = raw_unescaped.strip()
            if norm_expected:
                if norm_node_text and norm_node_text == norm_expected:
                    caption_matched = True
            else:
                # When expected caption is empty, composer node text MUST be empty!
                # Reject stale non-empty text in composer.
                if not norm_node_text:
                    caption_matched = True

        # Check media attachments structurally - only recognizable preview nodes OUTSIDE caption input
        if not is_caption_node:
            is_media_item = any(
                k in res_id
                for k in (
                    "composer_photo_attachment",
                    "media_attachment",
                    "photo_attachment",
                    "video_attachment",
                    "selected_media",
                    "thumbnail_image",
                    "composer_attachment",
                    "attachment_item",
                    "media_preview_item",
                    "attachment_preview",
                )
            ) or any(
                k in desc
                for k in ("selected photo", "selected video", "photo attachment", "media attachment")
            )
            if is_media_item:
                media_count_found += 1

    if not norm_expected:
        # Require composer input evidence and that it was confirmed empty
        caption_matched = composer_input_found and caption_matched

    # Exact observed attachment count, including expected zero
    media_matched = (media_count_found == expected_media_count)

    return caption_matched, media_matched, {
        "caption_matched": caption_matched,
        "composer_input_found": composer_input_found,
        "media_count_found": media_count_found,
        "expected_media_count": expected_media_count,
    }


EXACT_PUBLISH_LABELS = {"post", "publish", "share", "post now", "publish now", "share now"}
KNOWN_PUBLISH_RES_IDS = (
    "composer_publish_button",
    "publish_button",
    "post_button",
    "composer_post_button",
    "composer_post",
    "share_button",
    "button_post",
)


def find_publish_button_coords(ui_dump: str) -> tuple[int, int] | None:
    """Finds coordinates of the actual Post/Publish button node from XML hierarchy.

    Requires exact known button labels combined with clickable button node/class/resource ID.
    Ignores caption, privacy selector, and arbitrary text nodes.
    """
    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for match in node_pattern.finditer(ui_dump or ""):
        attrs = match.group(1)
        m_res = re.search(r'resource-id="([^"]*)"', attrs, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', attrs, re.IGNORECASE)
        m_desc = re.search(r'content-desc="([^"]*)"', attrs, re.IGNORECASE)
        m_class = re.search(r'class="([^"]*)"', attrs, re.IGNORECASE)
        m_clickable = re.search(r'clickable="([^"]*)"', attrs, re.IGNORECASE)
        m_bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs, re.IGNORECASE)

        res_id = (m_res.group(1) if m_res else "").lower()
        raw_text = m_text.group(1) if m_text else ""
        raw_desc = m_desc.group(1) if m_desc else ""
        cls_name = (m_class.group(1) if m_class else "").lower()
        clickable = (m_clickable.group(1) if m_clickable else "").lower() == "true"

        text_clean = raw_text.strip().lower()
        desc_clean = raw_desc.strip().lower()

        # Ignore caption input nodes
        if "edittext" in cls_name or any(k in res_id for k in ("composer_text_input", "caption", "input")):
            continue

        # Ignore privacy selector and audience nodes
        if any(k in res_id for k in ("privacy", "audience", "selector")):
            continue

        # Check button indicators
        is_button_widget = "button" in cls_name or clickable
        has_publish_res_id = any(k in res_id for k in KNOWN_PUBLISH_RES_IDS)
        has_exact_label = text_clean in EXACT_PUBLISH_LABELS or desc_clean in EXACT_PUBLISH_LABELS

        # Must combine exact label with clickable button node/class/resource ID,
        # OR known publish button resource ID with button widget and matching/empty label
        is_actual_button = (
            (has_exact_label and (is_button_widget or has_publish_res_id))
            or (has_publish_res_id and is_button_widget and (has_exact_label or not text_clean))
        )

        if is_actual_button and m_bounds:
            x1, y1, x2, y2 = map(int, m_bounds.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2

    return None


def execute_publish(
    serial: str,
    main_profile: dict[str, Any] | str,
    destination: dict[str, Any],
    caption: str = "",
    media_urls: list[str] | None = None,
    media_kind: str | None = None,
    expected_geometry: Any = None,
    app_card_version: str | None = None,
) -> dict[str, Any]:
    """Executes the guarded multi-gate Facebook publishing workflow.

    Safety invariants:
    - Never bypass PIN/password/pattern lock.
    - Confirm main Profile session and target actor identity before composition.
    - Stage media strictly through HTTPS allowlist with size/time limits and no redirects.
    - Verify actor, caption, and media preview in composer before tapping publish.
    - Submit exactly once.
    - Verify post ID/permalink from device evidence; return uncertain if unproven.
    - Restore initial screen sleep state.
    - Redact sensitive content (tokens, captions, raw media) from logs and evidence.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    stages: list[dict[str, Any]] = []

    # Resolve actor IDs and names
    main_name = main_profile.get("name") if isinstance(main_profile, dict) else str(main_profile)
    main_id = str(main_profile.get("id") or "").strip() if isinstance(main_profile, dict) else ""
    main_record_id = str(main_profile.get("record_id") or "").strip() if isinstance(main_profile, dict) else ""

    dest_name = str(destination.get("name") or main_name).strip() if isinstance(destination, dict) else ""
    dest_id = str(destination.get("id") or "").strip() if isinstance(destination, dict) else ""
    dest_kind = str(destination.get("kind") or "facebook_profile").strip() if isinstance(destination, dict) else ""
    parent_profile_id = str(destination.get("parent_profile_id") or "").strip() if isinstance(destination, dict) else ""

    expected_target_id = dest_id
    media_list = list(media_urls or [])
    caption_str = str(caption or "")

    caption_sha256 = hashlib.sha256(caption_str.encode("utf-8")).hexdigest() if caption_str else None
    evidence: dict[str, Any] = {
        "serial": serial,
        "actor_id": expected_target_id,
        "actor_name": dest_name,
        "caption_length": len(caption_str),
        "caption_sha256": caption_sha256,
        "media_count": len(media_list),
        "media_kind": media_kind,
        "publish_may_have_started": False,
    }

    # 1. Parameter and App Card version validation
    if not main_id:
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "missing_profile_id",
            "evidence": evidence,
            "stages": [{"stage": "validation", "passed": False, "detail": "Missing main profile ID"}],
            "checked_at": checked_at,
        }

    if not dest_id:
        error_code = "missing_page_id" if dest_kind == "facebook_page" else "missing_profile_id"
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": error_code,
            "evidence": evidence,
            "stages": [{"stage": "validation", "passed": False, "detail": f"Missing destination ID for {dest_kind}"}],
            "checked_at": checked_at,
        }

    if dest_kind == "facebook_page":
        if not parent_profile_id:
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "page_missing_parent_proof",
                "evidence": evidence,
                "stages": [{"stage": "validation", "passed": False, "detail": "Page destination requires parent_profile_id"}],
                "checked_at": checked_at,
            }
        if not main_record_id or parent_profile_id != main_record_id:
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "parent_profile_mismatch",
                "evidence": evidence,
                "stages": [{
                    "stage": "validation",
                    "passed": False,
                    "detail": f"Parent profile mismatch: destination parent '{parent_profile_id}' does not match main profile record ID '{main_record_id}'",
                }],
                "checked_at": checked_at,
            }

    local_card_ver = get_local_app_card_version()
    if not local_card_ver:
        stages.append({
            "stage": "app_card",
            "passed": False,
            "detail": "Local app card is missing, unreadable, or contains invalid version",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "app_card_invalid",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    if app_card_version and app_card_version.strip() != local_card_ver:
        stages.append({
            "stage": "app_card",
            "passed": False,
            "detail": f"App card version mismatch: expected {app_card_version}, found {local_card_ver}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "app_card_version_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "app_card",
        "passed": True,
        "detail": f"App card version {local_card_ver} verified",
    })

    # 2. Device connectivity & Reachability
    probe = probe_device(serial, wake=False)
    if not probe["adb_connected"]:
        stages.append({
            "stage": "connectivity",
            "passed": False,
            "detail": f"Device unreachable: {probe['status']}",
        })
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "remote_unreachable",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    mob_ok, mob_detail = verify_mobilerun_service(serial)
    if not mob_ok or not probe.get("portal_ready", False):
        stages.append({
            "stage": "portal",
            "passed": False,
            "detail": f"Mobilerun verification failed: {mob_detail}" if not mob_ok else "Mobilerun Portal is not ready",
        })
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "portal_unready",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "connectivity",
        "passed": True,
        "detail": f"ADB connected and Portal ready (initial screen: {probe['screen']})",
    })

    was_asleep = probe["screen"] == "asleep"

    # 3. Geometry verification
    expected_geom_str = normalize_geometry(expected_geometry)
    actual_geom = probe.get("resolution")
    if expected_geom_str and actual_geom != expected_geom_str:
        stages.append({
            "stage": "geometry",
            "passed": False,
            "detail": f"Geometry mismatch: expected {expected_geom_str}, found {actual_geom}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "geometry_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 4. Wake & Unlock Swipe/None only (regardless of initial sleep state)
    unlock_res = wake_and_unlock(serial)
    if unlock_res.returncode != 0:
        stages.append({
            "stage": "unlock",
            "passed": False,
            "detail": f"Failed to dismiss keyguard (exit code {unlock_res.returncode})",
        })
        if was_asleep:
            put_to_sleep(serial)
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "keyguard_dismiss_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    if is_device_secure_locked(serial):
        stages.append({
            "stage": "unlock",
            "passed": False,
            "detail": "Device is protected with PIN/Password/Pattern lock; bypass prohibited",
        })
        if was_asleep:
            put_to_sleep(serial)
        return {
            "status": "waiting_for_device",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "secure_lock_detected",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 5. Execute guarded candidate capture, proof/staging, composition, and submission
    staging_cm = None
    try:
        ui_cand_res = query_ui_hierarchy(serial)
        act_cand_res = query_activity_state(serial)

        if ui_cand_res.returncode != 0 or act_cand_res.returncode != 0:
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": "Identity verification failed: device capture failed (UI hierarchy or activity read nonzero)",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_mismatch",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        (
            cand_composer_id,
            cand_root_id,
            cand_switcher_ids,
        ) = parse_device_identity_evidence(ui_cand_res.stdout, act_cand_res.stdout)

        # Check for explicit conflict:
        # Any independently observed root/composer ID conflicting with expected => identity_mismatch, no collector
        if (cand_root_id is not None and cand_root_id != main_id) or (
            cand_composer_id is not None and cand_composer_id != expected_target_id
        ):
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": (
                    f"Identity mismatch: candidate direct ID conflicts with expected "
                    f"(root '{cand_root_id}' vs '{main_id}', composer '{cand_composer_id}' vs '{expected_target_id}')"
                ),
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_mismatch",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        has_legacy_marker = (cand_root_id == main_id or cand_composer_id == expected_target_id)

        if has_legacy_marker:
            # Legacy old path: stage media first if required, then run composition and publish
            staged_remote_files: list[str] = []
            if media_list:
                try:
                    staging_cm = stage_media_files(media_list, serial=serial)
                    staged = staging_cm.__enter__()
                    staged_remote_files = [str(item["remote_path"]) for item in staged]
                    stages.append({
                        "stage": "media_staging",
                        "passed": True,
                        "detail": f"Successfully staged {len(staged_remote_files)} media files to device",
                    })
                except Exception as exc:
                    stages.append({
                        "stage": "media_staging",
                        "passed": False,
                        "detail": f"Media staging failed: {type(exc).__name__}",
                    })
                    return {
                        "status": "failed",
                        "facebook_post_id": None,
                        "permalink": None,
                        "actor_id": expected_target_id,
                        "publish_may_have_started": False,
                        "error_code": "media_staging_failed",
                        "evidence": evidence,
                        "stages": stages,
                        "checked_at": checked_at,
                    }

            try:
                return _run_composition_and_publish(
                    serial=serial,
                    main_id=main_id,
                    main_name=main_name,
                    dest_id=expected_target_id,
                    dest_name=dest_name,
                    dest_kind=dest_kind,
                    caption=caption_str,
                    expected_media_count=len(media_list),
                    stages=stages,
                    evidence=evidence,
                    checked_at=checked_at,
                )
            finally:
                if staging_cm is not None:
                    try:
                        staging_cm.__exit__(None, None, None)
                    except Exception as cleanup_exc:
                        logger.warning("Remote staging cleanup error: %s", type(cleanup_exc).__name__)
                    finally:
                        staging_cm = None

        # Modern path: both IDs absent in candidate capture
        try:
            proof = collect_own_url_proof_chain(
                serial=serial,
                main_name=main_name,
                expected_root_id=main_id,
                target_name=dest_name,
                expected_target_id=expected_target_id,
                app_card_version=local_card_ver,
                facebook_version=probe.get("facebook_version"),
                portal_version=probe.get("portal_version"),
                expected_geometry=actual_geom,
                timeout=240.0,
            )
        except Exception as exc:
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": f"Identity proof collection failed: {type(exc).__name__}",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_unverified",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        expected_fb_ver = probe.get("facebook_version")
        expected_portal_ver = probe.get("portal_version")

        proof_valid = (
            proof.passed is True
            and proof.observed_root_id == main_id
            and proof.verified_main_profile_id == main_id
            and proof.observed_target_id == expected_target_id
            and proof.active_composer_actor_id == expected_target_id
            and proof.serial == serial
            and proof.method == "own_url_chain_v1"
            and bool(proof.resolution)
            and proof.resolution == actual_geom
            and bool(proof.app_card_version)
            and proof.app_card_version == local_card_ver
            and bool(proof.facebook_version)
            and (expected_fb_ver is None or proof.facebook_version == expected_fb_ver)
            and bool(proof.portal_version)
            and (expected_portal_ver is None or proof.portal_version == expected_portal_ver)
        )

        if not proof_valid:
            detail = f"Identity proof verification failed: {proof.error or 'contradictory evidence'}" if not proof.passed else "Identity proof contradictory with probe evidence"
            if proof.error == "IME_RESTORATION_FAILED":
                detail = "Identity proof failed: IME restoration failed"
            stages.append({
                "stage": "identity_gate",
                "passed": False,
                "detail": detail,
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "identity_unverified",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        stages.append({
            "stage": "identity_gate",
            "passed": True,
            "detail": f"Actor '{proof.observed_target_id}' verified under main profile '{proof.observed_root_id}' via {proof.method}",
        })

        evidence["actor_id"] = proof.observed_target_id
        evidence["verified_main_profile_id"] = proof.observed_root_id
        evidence["active_composer_actor_id"] = proof.active_composer_actor_id
        evidence["proof_method"] = proof.method
        evidence["proof_nonce"] = proof.nonce
        evidence["observed_root_url"] = proof.observed_root_url
        evidence["observed_target_url"] = proof.observed_target_url
        evidence["composer_readiness"] = proof.composer_readiness

        if media_list:
            stages.append({
                "stage": "media_staging",
                "passed": False,
                "detail": "Media publishing is not supported on modern Facebook composer layout",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "media_unsupported_on_modern",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        readiness = proof.composer_readiness or {}
        empty_certainty = readiness.get("empty_state_certainty")
        if empty_certainty is not True:
            stages.append({
                "stage": "draft_state_gate",
                "passed": False,
                "detail": "Composer empty state cannot be verified with certainty (empty_state_certainty is not True); typing blocked to prevent corrupting or publishing existing draft",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "draft_state_uncertain",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        header_variant = readiness.get("header_variant")
        stages.append({
            "stage": "submission_gate",
            "passed": False,
            "detail": f"Modern composer layout '{header_variant}' submission flow is uncalibrated (Next button to final Post screen unobserved)",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": False,
            "error_code": "submission_flow_uncalibrated",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    except Exception as exc:
        err_type = type(exc).__name__
        if evidence.get("publish_may_have_started") is False:
            stages.append({
                "stage": "execution_error",
                "passed": False,
                "detail": f"Execution error before publication: {err_type}",
            })
            return {
                "status": "failed",
                "facebook_post_id": None,
                "permalink": None,
                "actor_id": expected_target_id,
                "publish_may_have_started": False,
                "error_code": "pre_submit_failed",
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "execution_error",
            "passed": False,
            "detail": f"Execution error with uncertain publish state: {err_type}",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": expected_target_id,
            "publish_may_have_started": True,
            "error_code": "post_submit_uncertain",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    finally:
        if staging_cm is not None:
            try:
                staging_cm.__exit__(None, None, None)
            except Exception as cleanup_exc:
                logger.warning("Remote staging cleanup error: %s", type(cleanup_exc).__name__)
            staging_cm = None
        if was_asleep:
            try:
                put_to_sleep(serial)
            except Exception as sleep_exc:
                logger.warning("Failed to restore sleep state: %s", type(sleep_exc).__name__)


def _run_composition_and_publish(
    serial: str,
    main_id: str,
    main_name: str,
    dest_id: str,
    dest_name: str,
    dest_kind: str,
    caption: str,
    expected_media_count: int,
    stages: list[dict[str, Any]],
    evidence: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    # Launch Facebook feed
    launch_res = launch_facebook_feed(serial)
    if launch_res.returncode != 0:
        stages.append({
            "stage": "app_launch",
            "passed": False,
            "detail": f"Failed to launch Facebook feed (exit code {launch_res.returncode})",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "app_launch_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    stages.append({
        "stage": "app_launch",
        "passed": True,
        "detail": "Facebook launched to feed",
    })

    # Mobilerun assisted navigation and composer preparation (DO NOT TAP PUBLISH)
    # The prompt strictly instructs navigation and focusing input, but never contains caption or permits submit
    mob_prompt = build_composition_prompt(
        dest_name=dest_name,
        has_media=(expected_media_count > 0),
    )
    mob_res = run_mobilerun(serial=serial, prompt=mob_prompt, steps=8)
    if mob_res.returncode != 0:
        stages.append({
            "stage": "composition_navigation",
            "passed": False,
            "detail": f"Mobilerun navigation failed (exit code {mob_res.returncode})",
        })
        is_unavail = any(
            phrase in (mob_res.stderr or "").lower()
            for phrase in (
                "not found",
                "device offline",
                "unreachable",
                "connection refused",
                "timed out",
                "timeout",
                "daemon not running",
                "no device",
            )
        )
        return {
            "status": "waiting_for_device" if is_unavail else "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "portal_unready" if is_unavail else "navigation_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "composition_navigation",
        "passed": True,
        "detail": "Composer opened and input focused",
    })

    # Direct device capture for independent pre-publish gate verification
    ui_dump_res = query_ui_hierarchy(serial)
    act_dump_res = query_activity_state(serial)

    if ui_dump_res.returncode != 0 or act_dump_res.returncode != 0:
        stages.append({
            "stage": "identity_gate",
            "passed": False,
            "detail": "Identity verification failed: device capture failed (UI hierarchy or activity read nonzero)",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "identity_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 1. Identity re-verification Gate
    (
        active_composer_actor_id,
        verified_main_profile_id,
        switcher_account_ids,
    ) = parse_device_identity_evidence(ui_dump_res.stdout, act_dump_res.stdout)

    evidence["verified_main_profile_id"] = verified_main_profile_id
    evidence["active_composer_actor_id"] = active_composer_actor_id

    main_profile_proven = bool(main_id) and verified_main_profile_id == main_id
    active_actor_matched = bool(dest_id) and active_composer_actor_id == dest_id

    if not main_profile_proven or not active_actor_matched:
        detail = "Identity mismatch: "
        if not main_profile_proven:
            detail += f"main profile expected '{main_id}', found '{verified_main_profile_id}'. "
        if not active_actor_matched:
            if dest_id in switcher_account_ids:
                detail += f"target ID '{dest_id}' found only in switcher (inactive). "
            else:
                detail += f"composer actor expected '{dest_id}', found '{active_composer_actor_id}'. "

        stages.append({
            "stage": "identity_gate",
            "passed": False,
            "detail": detail.strip(),
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "identity_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "identity_gate",
        "passed": True,
        "detail": f"Actor '{dest_id}' active under verified main profile '{main_id}'",
    })

    # 2. Deterministic literal typing through subprocess argv (never through adb shell)
    # Always clears any stale draft text and types caption (or clears if caption is empty)
    type_res = type_text(serial=serial, text=caption, clear=True)
    if type_res.returncode != 0:
        stages.append({
            "stage": "caption_typing",
            "passed": False,
            "detail": f"Failed to type caption into composer (code {type_res.returncode})",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_typing_failed",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    stages.append({
        "stage": "caption_typing",
        "passed": True,
        "detail": "Caption typed deterministically via mobilerun device type",
    })
    # Re-capture UI hierarchy after typing to verify entered caption
    ui_dump_res = query_ui_hierarchy(serial)
    if ui_dump_res.returncode != 0:
        stages.append({
            "stage": "content_gate",
            "passed": False,
            "detail": "Content gate failed: UI hierarchy capture failed after caption typing",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    # 3. Content & Media Preview Gate
    caption_ok, media_ok, content_evidence = parse_composer_content_evidence(
        ui_dump_res.stdout,
        expected_caption=caption,
        expected_media_count=expected_media_count,
    )
    evidence.update(content_evidence)

    if not caption_ok:
        stages.append({
            "stage": "content_gate",
            "passed": False,
            "detail": "Caption text in composer does not match delivery snapshot",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "caption_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    if not media_ok:
        stages.append({
            "stage": "media_preview_gate",
            "passed": False,
            "detail": f"Media preview mismatch: expected {expected_media_count}, found {content_evidence['media_count_found']}",
        })
        return {
            "status": "failed",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": False,
            "error_code": "media_preview_mismatch",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "content_gate",
        "passed": True,
        "detail": "Caption and media preview verified in composer",
    })

    # 3. Final Publish Gate: Submit exactly once
    coords = find_publish_button_coords(ui_dump_res.stdout)
    try:
        if coords:
            x, y = coords
            submit_res = run_adb(["shell", "input", "tap", str(x), str(y)], serial=serial)
        else:
            # Single submit fallback via Mobilerun
            submit_res = run_mobilerun(serial=serial, prompt="Tap Post or Publish button once.", steps=2)

        # Crucial state transition: submission has occurred
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "submission",
            "passed": True,
            "detail": "Submitted publish command once to Facebook app",
        })

        # 4. Post-Submission Result Verification
        post_id, permalink, verify_details = poll_for_post_result(
            serial=serial,
            actor_id=dest_id,
            expected_caption=caption,
            max_attempts=4,
            delay_sec=1.5,
        )
        evidence.update(verify_details)

        if post_id:
            stages.append({
                "stage": "result_verification",
                "passed": True,
                "detail": f"Post verified: id={post_id}",
            })
            return {
                "status": "published",
                "facebook_post_id": post_id,
                "permalink": permalink,
                "actor_id": dest_id,
                "publish_may_have_started": True,
                "error_code": None,
                "evidence": evidence,
                "stages": stages,
                "checked_at": checked_at,
            }

        # If post identity cannot be proven from device evidence, return uncertain with publish_may_have_started=True
        stages.append({
            "stage": "result_verification",
            "passed": False,
            "detail": "Post submitted but post ID could not be proven from device evidence",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": True,
            "error_code": "post_identity_unproven",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
    except Exception as exc:
        evidence["publish_may_have_started"] = True
        stages.append({
            "stage": "post_submission_error",
            "passed": False,
            "detail": f"Exception after submit initiated: {type(exc).__name__}",
        })
        return {
            "status": "uncertain",
            "facebook_post_id": None,
            "permalink": None,
            "actor_id": dest_id,
            "publish_may_have_started": True,
            "error_code": "post_submit_uncertain",
            "evidence": evidence,
            "stages": stages,
            "checked_at": checked_at,
        }
