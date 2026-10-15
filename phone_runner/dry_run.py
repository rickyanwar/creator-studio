from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any

from phone_runner.adb import (
    is_device_secure_locked,
    launch_facebook_feed,
    put_to_sleep,
    query_activity_state,
    query_ui_hierarchy,
    wake_and_unlock,
)
from phone_runner.config import (
    get_local_app_card_version,
    get_mobilerun_timeout,
    sanitize_actor_name_for_prompt,
)
from phone_runner.identity_proof import collect_own_url_proof_chain
from phone_runner.mobilerun import run_mobilerun
from phone_runner.probe import probe_device


def normalize_geometry(geom: Any) -> str | None:
    if isinstance(geom, str):
        return geom.strip()
    if isinstance(geom, dict):
        w = geom.get("width")
        h = geom.get("height")
        if w is not None and h is not None:
            return f"{w}x{h}"
    return None


def parse_device_identity_evidence(
    ui_dump: str,
    activity_dump: str,
) -> tuple[str | None, str | None, set[str]]:
    """
    Parses direct device output (UI XML hierarchy and activity dumpsys) to recover:
    - active_composer_actor_id: actor ID specifically bound to the active composer
    - verified_main_profile_id: signed-in main profile root user ID (from session/c_user/activity)
    - switcher_account_ids: account IDs appearing only in account switcher menus or lists
    """
    active_composer_actor_id: str | None = None
    verified_main_profile_id: str | None = None
    switcher_account_ids: set[str] = set()

    # Prevent caption reflection: strip text from editable input nodes before searching session tokens
    def _strip_editable(match: re.Match[str]) -> str:
        attrs = match.group(0)
        attrs_lower = attrs.lower()
        if "edittext" in attrs_lower or "autocompletetextview" in attrs_lower or 'editable="true"' in attrs_lower:
            return re.sub(r'text="[^"]*"', 'text=""', attrs)
        return attrs

    safe_ui_dump = re.sub(r"<node\s+[^>]+>", _strip_editable, ui_dump or "")
    combined_text = f"{activity_dump}\n{safe_ui_dump}"

    # Extract verified main profile ID from session indicators
    m_session = re.search(
        r"(?:c_user|logged_in_user|session_user_id|active_user_id|account_id)[=:\s]+([0-9]{4,32})",
        combined_text,
        re.IGNORECASE,
    )
    if m_session:
        verified_main_profile_id = m_session.group(1).strip()

    # Parse XML hierarchy nodes to isolate composer vs switcher elements
    id_pattern = re.compile(
        r"(?:fb://(?:profile|page)/|profile\.php\?id=|/pages/[^/]+/|(?:profile_id|actor_id|page_id)[=:\s]+)([0-9]{4,32})",
        re.IGNORECASE,
    )

    node_pattern = re.compile(r"<node\s+([^>]+)/?>", re.IGNORECASE)
    for node_match in node_pattern.finditer(ui_dump or ""):
        node_attr_str = node_match.group(1)

        m_res = re.search(r'resource-id="([^"]*)"', node_attr_str, re.IGNORECASE)
        m_desc = re.search(r'content-desc="([^"]*)"', node_attr_str, re.IGNORECASE)
        m_text = re.search(r'text="([^"]*)"', node_attr_str, re.IGNORECASE)

        res_id = (m_res.group(1) if m_res else "").lower()
        desc = m_desc.group(1) if m_desc else ""
        text = m_text.group(1) if m_text else ""
        data = f"{res_id} {desc} {text}"

        found_ids = set(id_pattern.findall(data))
        if not found_ids:
            continue

        is_switcher = any(
            k in res_id
            for k in ("switcher", "switch_account", "account_list", "inactive_account")
        )
        if is_switcher:
            switcher_account_ids.update(found_ids)
            continue

        is_composer_actor = any(
            k in res_id
            for k in (
                "composer_actor",
                "composer_header",
                "author_name",
                "publisher_profile",
                "composer_author",
                "composer_profile",
            )
        ) or (
            "composer" in res_id
            and any(k in res_id for k in ("actor", "author", "profile", "avatar"))
        )

        if is_composer_actor:
            for fid in found_ids:
                active_composer_actor_id = fid

    return active_composer_actor_id, verified_main_profile_id, switcher_account_ids


def execute_dry_run(
    serial: str,
    main_profile: dict[str, Any] | str,
    target_page: dict[str, Any] | str | None = None,
    expected_geometry: Any = None,
    app_card_version: str | None = None,
    timeout: float = 240.0,
) -> dict[str, Any]:
    start_time = time.monotonic()
    deadline = start_time + timeout
    checked_at = datetime.now(timezone.utc).isoformat()
    stages: list[dict[str, Any]] = []

    # Resolve target actor name & ID
    main_name = main_profile.get("name") if isinstance(main_profile, dict) else str(main_profile)
    main_id = main_profile.get("id") if isinstance(main_profile, dict) else ""

    target_name = main_name
    target_id = main_id
    if target_page:
        if isinstance(target_page, dict):
            target_name = target_page.get("name") or main_name
            target_id = target_page.get("id") or main_id
        else:
            target_name = str(target_page)

    # 1. App Card version check
    local_version = get_local_app_card_version()
    if not local_version:
        stages.append({
            "stage": "app_card",
            "passed": False,
            "detail": "Local app card is missing, unreadable, or contains invalid version",
        })
        return {
            "success": False,
            "status": "app_card_invalid",
            "stages": stages,
            "evidence": {"stopped_before_publish": True},
            "checked_at": checked_at,
        }
    if app_card_version:
        if app_card_version.strip() != local_version:
            stages.append({
                "stage": "app_card",
                "passed": False,
                "detail": f"App card version mismatch: expected {app_card_version}, found {local_version}",
            })
            return {
                "success": False,
                "status": "app_card_version_mismatch",
                "stages": stages,
                "evidence": {"stopped_before_publish": True},
                "checked_at": checked_at,
            }
    stages.append({
        "stage": "app_card",
        "passed": True,
        "detail": f"App card version {local_version} verified",
    })

    # 2. Connectivity probe
    probe = probe_device(serial, wake=False)
    if not probe["adb_connected"]:
        stages.append({
            "stage": "connectivity",
            "passed": False,
            "detail": f"Device unreachable: {probe['status']}",
        })
        return {
            "success": False,
            "status": "remote_unreachable",
            "stages": stages,
            "evidence": {"stopped_before_publish": True},
            "checked_at": checked_at,
        }

    if not probe["portal_ready"]:
        stages.append({
            "stage": "portal",
            "passed": False,
            "detail": "Mobilerun Portal is not ready or ping failed",
        })
        return {
            "success": False,
            "status": "portal_unready",
            "stages": stages,
            "evidence": {"stopped_before_publish": True},
            "checked_at": checked_at,
        }

    stages.append({
        "stage": "connectivity",
        "passed": True,
        "detail": f"ADB connected and Portal ready (initial screen: {probe['screen']})",
    })

    was_asleep = probe["screen"] == "asleep"

    # 3. Geometry check
    expected_geom_str = normalize_geometry(expected_geometry)
    actual_geom = probe.get("resolution")
    if expected_geom_str:
        if actual_geom != expected_geom_str:
            stages.append({
                "stage": "geometry",
                "passed": False,
                "detail": f"Geometry mismatch: expected {expected_geom_str}, found {actual_geom}",
            })
            return {
                "success": False,
                "status": "geometry_mismatch",
                "stages": stages,
                "evidence": {"resolution": actual_geom, "stopped_before_publish": True},
                "checked_at": checked_at,
            }
        stages.append({
            "stage": "geometry",
            "passed": True,
            "detail": f"Geometry {actual_geom} verified",
        })
    elif actual_geom:
        stages.append({
            "stage": "geometry",
            "passed": True,
            "detail": f"Detected geometry {actual_geom}",
        })

    # 4. Wake & unlock Swipe/None keyguard regardless of initial sleep state
    unlock_res = wake_and_unlock(serial)
    if unlock_res.returncode != 0:
        stages.append({
            "stage": "unlock",
            "passed": False,
            "detail": f"Failed to dismiss keyguard: {unlock_res.stderr.strip()}",
        })
        if was_asleep:
            put_to_sleep(serial)
        return {
            "success": False,
            "status": "keyguard_dismiss_failed",
            "stages": stages,
            "evidence": {"stopped_before_publish": True},
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
            "success": False,
            "status": "waiting_for_device",
            "stages": stages,
            "evidence": {"stopped_before_publish": True},
            "checked_at": checked_at,
        }

    try:
        # 5. Launch Facebook feed
        launch_res = launch_facebook_feed(serial)
        if launch_res.returncode != 0:
            stages.append({
                "stage": "app_launch",
                "passed": False,
                "detail": f"Failed to launch Facebook feed: {launch_res.stderr}",
            })
            return {
                "success": False,
                "status": "app_launch_failed",
                "stages": stages,
                "evidence": {"stopped_before_publish": True},
                "checked_at": checked_at,
            }
        stages.append({
            "stage": "app_launch",
            "passed": True,
            "detail": "Facebook launched to feed via fb://feed intent",
        })

        def _make_capture_fail_response():
            stages.append({
                "stage": "identity_verification",
                "passed": False,
                "detail": "Identity verification failed: device capture failed (UI hierarchy or activity read nonzero)",
            })
            return {
                "success": False,
                "status": "identity_unverified",
                "stages": stages,
                "evidence": {
                    "serial": serial,
                    "resolution": actual_geom,
                    "app_card_version": local_version,
                    "facebook_version": probe.get("facebook_version"),
                    "portal_version": probe.get("portal_version"),
                    "evidence_source": "adb_device_read",
                    "verified_main_profile_id": None,
                    "active_composer_actor_id": None,
                    "switcher_account_ids": [],
                    "stopped_before_publish": True,
                },
                "checked_at": checked_at,
            }

        # Direct fresh capture before preliminary model navigation
        ui_dump_res = query_ui_hierarchy(serial)
        act_dump_res = query_activity_state(serial)

        if ui_dump_res.returncode != 0 or act_dump_res.returncode != 0:
            return _make_capture_fail_response()

        (
            pre_composer_id,
            pre_main_id,
            pre_switcher_ids,
        ) = parse_device_identity_evidence(ui_dump_res.stdout, act_dump_res.stdout)

        expected_main_id = str(main_id).strip() if main_id else ""
        expected_page_id = str(target_id).strip() if target_page else ""
        expected_target_id = expected_page_id if expected_page_id else expected_main_id

        def _run_collector_fallback():
            proof = collect_own_url_proof_chain(
                serial=serial,
                main_name=main_name,
                expected_root_id=expected_main_id,
                target_name=target_name,
                expected_target_id=expected_target_id,
                app_card_version=local_version,
                facebook_version=probe.get("facebook_version"),
                portal_version=probe.get("portal_version"),
                expected_geometry=actual_geom,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            stages.extend(proof.stages)
            if proof.passed:
                stages.append({
                    "stage": "identity_verification",
                    "passed": True,
                    "detail": (
                        f"Actor '{target_name}' (ID: {proof.observed_target_id}) active in composer "
                        f"under verified main profile (ID: {proof.observed_root_id})"
                    ),
                })
                stages.append({
                    "stage": "composer_readiness",
                    "passed": True,
                    "detail": "Composer open and stopped before publish (host-owned proof chain)",
                })
                return {
                    "success": True,
                    "status": "ready",
                    "stages": stages,
                    "evidence": {
                        "serial": serial,
                        "resolution": actual_geom,
                        "app_card_version": local_version,
                        "facebook_version": probe.get("facebook_version"),
                        "portal_version": probe.get("portal_version"),
                        "evidence_source": "adb_device_read",
                        "proof_method": "own_url_chain_v1",
                        "verified_actor": target_name,
                        "verified_actor_id": proof.observed_target_id,
                        "verified_main_profile_id": proof.observed_root_id,
                        "active_composer_actor_id": proof.observed_target_id,
                        "observed_root_url": proof.observed_root_url,
                        "observed_target_url": proof.observed_target_url,
                        "proof_chain": proof.to_dict(),
                        "stopped_before_publish": True,
                    },
                    "checked_at": checked_at,
                }
            else:
                stages.append({
                    "stage": "identity_verification",
                    "passed": False,
                    "detail": f"Identity verification failed: {proof.error or 'Proof chain failed'}",
                })
                return {
                    "success": False,
                    "status": "identity_unverified",
                    "stages": stages,
                    "evidence": {
                        "serial": serial,
                        "resolution": actual_geom,
                        "app_card_version": local_version,
                        "facebook_version": probe.get("facebook_version"),
                        "portal_version": probe.get("portal_version"),
                        "evidence_source": "adb_device_read",
                        "proof_method": "own_url_chain_v1",
                        "verified_main_profile_id": proof.observed_root_id,
                        "active_composer_actor_id": proof.active_composer_actor_id,
                        "proof_chain": proof.to_dict(),
                        "stopped_before_publish": True,
                    },
                    "checked_at": checked_at,
                }

        has_legacy_ids = (pre_main_id is not None or pre_composer_id is not None)

        if not has_legacy_ids:
            return _run_collector_fallback()

        # Legacy direct ID markers detected: run existing legacy navigation task
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            stages.append({
                "stage": "identity_verification",
                "passed": False,
                "detail": "Identity verification failed: deadline exceeded before navigation",
            })
            return {
                "success": False,
                "status": "identity_unverified",
                "stages": stages,
                "evidence": {
                    "serial": serial,
                    "resolution": actual_geom,
                    "app_card_version": local_version,
                    "facebook_version": probe.get("facebook_version"),
                    "portal_version": probe.get("portal_version"),
                    "evidence_source": "adb_device_read",
                    "verified_main_profile_id": None,
                    "active_composer_actor_id": None,
                    "switcher_account_ids": [],
                    "stopped_before_publish": True,
                },
                "checked_at": checked_at,
            }

        clean_target_name = sanitize_actor_name_for_prompt(target_name)
        prompt = (
            f"DRY-RUN INSPECTION ONLY. STRICT SAFETY RULE: STOP BEFORE PUBLISHING. "
            f"UNDER NO CIRCUMSTANCES TAP POST OR PUBLISH. "
            f"STRICTLY FORBIDDEN: do not open Photo, media, gallery, camera, file picker. "
            f"Do not tap Next, Share, Like, Follow, messages, or modify settings. "
            f"Navigate to view active account profile info for '{clean_target_name}' and open post composer. "
            f"Do not enter text or press post."
        )

        cfg_timeout = get_mobilerun_timeout()
        nav_timeout = min(cfg_timeout, remaining_time)

        mob_res = run_mobilerun(
            serial=serial,
            prompt=prompt,
            steps=5,
            timeout=nav_timeout,
            no_reasoning=True,
            save_trajectory="none",
        )

        if mob_res.stderr == "IME_RESTORATION_FAILED":
            stages.append({
                "stage": "identity_verification",
                "passed": False,
                "detail": "Identity verification failed: device IME restoration failed",
            })
            return {
                "success": False,
                "status": "device_error",
                "stages": stages,
                "evidence": {
                    "serial": serial,
                    "resolution": actual_geom,
                    "app_card_version": local_version,
                    "facebook_version": probe.get("facebook_version"),
                    "portal_version": probe.get("portal_version"),
                    "evidence_source": "adb_device_read",
                    "verified_main_profile_id": None,
                    "verified_actor_id": None,
                    "active_composer_actor_id": None,
                    "stopped_before_publish": True,
                },
                "checked_at": checked_at,
            }

        # Recapture fresh UI hierarchy & activity dumpsys
        ui_dump_res = query_ui_hierarchy(serial)
        act_dump_res = query_activity_state(serial)

        if ui_dump_res.returncode != 0 or act_dump_res.returncode != 0:
            return _make_capture_fail_response()

        (
            active_composer_actor_id,
            verified_main_profile_id,
            switcher_account_ids,
        ) = parse_device_identity_evidence(ui_dump_res.stdout, act_dump_res.stdout)

        err_lower = mob_res.stderr.lower()

        # Gate 1: Mobilerun execution clean
        mob_ok = mob_res.returncode == 0 and "not found" not in err_lower

        # Gate 2: Main profile root session must be proven
        main_profile_proven = bool(expected_main_id) and verified_main_profile_id == expected_main_id

        # Gate 3: Active composer actor must match expected target
        active_actor_matched = (
            bool(expected_target_id)
            and active_composer_actor_id == expected_target_id
        )

        actor_confirmed = mob_ok and main_profile_proven and active_actor_matched

        if not actor_confirmed:
            # If a direct legacy ID was present but explicitly conflicted with expected IDs,
            # reject immediately as direct spoof/mismatch without triggering collector.
            direct_id_conflict = (
                (verified_main_profile_id is not None and verified_main_profile_id != expected_main_id)
                or (active_composer_actor_id is not None and active_composer_actor_id != expected_target_id)
            )
            # If direct IDs are absent in modern Facebook app (dumpsys/XML without c_user or actor_id),
            # trigger the host-owned proof collector even if preliminary composer nav failed or timed out.
            # Collector's fresh own-root navigation can independently establish proof.
            if not direct_id_conflict and (verified_main_profile_id is None or active_composer_actor_id is None):
                return _run_collector_fallback()

            failure_reasons = []
            if not mob_ok:
                err_diag = "navigation timeout" if ("timeout" in err_lower or "timed out" in err_lower) else (
                    "navigation binary not found" if "not found" in err_lower else f"navigation failed (code {mob_res.returncode})"
                )
                failure_reasons.append(err_diag)
            if not main_profile_proven:
                failure_reasons.append(
                    f"main profile ID '{expected_main_id}' not proven (found: '{verified_main_profile_id}')"
                )
            if not active_actor_matched:
                if expected_target_id in switcher_account_ids:
                    failure_reasons.append(
                        f"target ID '{expected_target_id}' found only in account switcher (inactive)"
                    )
                else:
                    failure_reasons.append(
                        f"active composer actor '{active_composer_actor_id}' does not match target '{expected_target_id}'"
                    )

            detail_msg = "; ".join(failure_reasons)
            stages.append({
                "stage": "identity_verification",
                "passed": False,
                "detail": f"Identity verification failed: {detail_msg}",
            })
            return {
                "success": False,
                "status": "identity_unverified",
                "stages": stages,
                "evidence": {
                    "serial": serial,
                    "resolution": actual_geom,
                    "app_card_version": local_version,
                    "facebook_version": probe.get("facebook_version"),
                    "portal_version": probe.get("portal_version"),
                    "evidence_source": "adb_device_read",
                    "verified_main_profile_id": verified_main_profile_id,
                    "active_composer_actor_id": active_composer_actor_id,
                    "switcher_account_ids": sorted(switcher_account_ids),
                    "stopped_before_publish": True,
                },
                "checked_at": checked_at,
            }

        stages.append({
            "stage": "identity_verification",
            "passed": True,
            "detail": (
                f"Actor '{target_name}' (ID: {expected_target_id}) active in composer "
                f"under verified main profile (ID: {expected_main_id})"
            ),
        })
        stages.append({
            "stage": "composer_readiness",
            "passed": True,
            "detail": "Composer open and stopped before publish",
        })

        return {
            "success": True,
            "status": "ready",
            "stages": stages,
            "evidence": {
                "serial": serial,
                "resolution": actual_geom,
                "app_card_version": local_version,
                "facebook_version": probe.get("facebook_version"),
                "portal_version": probe.get("portal_version"),
                "evidence_source": "adb_device_read",
                "verified_actor": target_name,
                "verified_actor_id": expected_target_id,
                "verified_main_profile_id": verified_main_profile_id,
                "active_composer_actor_id": active_composer_actor_id,
                "switcher_account_ids": sorted(switcher_account_ids),
                "stopped_before_publish": True,
            },
            "checked_at": checked_at,
        }

    finally:
        # Restore sleep state if initially asleep
        if was_asleep:
            put_to_sleep(serial)
