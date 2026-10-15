import argparse
import json
import os
import sys
import time
from typing import Any

from phone_runner.adb import (
    parse_screen_state,
    put_to_sleep,
    query_power_state,
)
from phone_runner.config import is_serial_allowed, is_serial_valid_format
from phone_runner.dry_run import execute_dry_run
from phone_runner.identity_proof import collect_own_url_proof_chain
from phone_runner.server import get_serial_lock


def sanitize_evidence_for_report(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strips any raw XML, raw tokens, or sensitive payloads from evidence report."""
    sanitized: dict[str, Any] = {}
    allowed_keys = {
        "serial",
        "resolution",
        "app_card_version",
        "facebook_version",
        "portal_version",
        "evidence_source",
        "proof_method",
        "verified_actor",
        "verified_actor_id",
        "verified_main_profile_id",
        "active_composer_actor_id",
        "observed_root_url",
        "observed_target_url",
        "stopped_before_publish",
    }
    for k, v in evidence.items():
        if k in allowed_keys:
            sanitized[k] = v
        elif k == "proof_chain" and isinstance(v, dict):
            sanitized["proof_chain"] = {
                "nonce": v.get("nonce"),
                "method": v.get("method"),
                "observed_root_id": v.get("observed_root_id"),
                "observed_root_url": v.get("observed_root_url"),
                "observed_target_id": v.get("observed_target_id"),
                "observed_target_url": v.get("observed_target_url"),
                "steps": [
                    {
                        "step": s.get("step"),
                        "timestamp": s.get("timestamp"),
                        "digest": s.get("digest"),
                    }
                    for s in v.get("steps", [])
                ],
                "composer_readiness": v.get("composer_readiness", {}),
            }
    return sanitized


def run_live_dry_run(
    serial: str,
    main_name: str,
    main_id: str | None = None,
    target_name: str | None = None,
    target_id: str | None = None,
    expected_geometry: str | None = None,
    app_card_version: str | None = None,
    model: str | None = None,
    discovery: bool = False,
    timeout: float = 240.0,
) -> dict[str, Any]:
    if not is_serial_valid_format(serial):
        return {
            "success": False,
            "status": "invalid_serial",
            "error": "Invalid serial format",
        }

    if model:
        os.environ["MOBILERUN_MODEL"] = model

    lock = get_serial_lock(serial)
    if not lock.acquire(blocking=False):
        return {
            "success": False,
            "status": "device_busy",
            "error": "Device serial is locked by an ongoing operation",
        }

    was_asleep = False
    try:
        pwr_res = query_power_state(serial)
        was_asleep = parse_screen_state(pwr_res.stdout) == "asleep"

        if discovery:
            proof = collect_own_url_proof_chain(
                serial=serial,
                main_name=main_name,
                expected_root_id="",
                target_name=target_name,
                expected_target_id="",
                app_card_version=app_card_version,
                expected_geometry=expected_geometry,
                timeout=timeout,
                discovery_mode=True,
            )
            report = {
                "success": proof.passed,
                "calibrated": False,
                "status": "discovered" if proof.passed else "discovery_failed",
                "stages": proof.stages,
                "evidence": sanitize_evidence_for_report({
                    "serial": serial,
                    "resolution": expected_geometry,
                    "evidence_source": "adb_device_read",
                    "proof_method": "own_url_chain_v1",
                    "verified_actor": target_name or main_name,
                    "verified_actor_id": proof.observed_target_id,
                    "verified_main_profile_id": proof.observed_root_id,
                    "active_composer_actor_id": proof.active_composer_actor_id,
                    "observed_root_url": proof.observed_root_url,
                    "observed_target_url": proof.observed_target_url,
                    "proof_chain": proof.to_dict(),
                    "stopped_before_publish": True,
                }),
                "checked_at": proof.created_at,
            }
            return report

        # Normal verification dry-run
        main_profile_arg: dict[str, Any] = {"name": main_name, "id": main_id or ""}
        target_page_arg: dict[str, Any] | None = None
        if target_name or target_id:
            target_page_arg = {
                "name": target_name or main_name,
                "id": target_id or main_id or "",
            }

        res = execute_dry_run(
            serial=serial,
            main_profile=main_profile_arg,
            target_page=target_page_arg,
            expected_geometry=expected_geometry,
            app_card_version=app_card_version,
            timeout=timeout,
        )

        res["calibrated"] = res.get("success", False)
        if "stages" in res and isinstance(res["stages"], list):
            clean_stages = []
            for s in res["stages"]:
                if isinstance(s, dict):
                    clean_stages.append({
                        "stage": s.get("stage"),
                        "passed": bool(s.get("passed")),
                        "detail": str(s.get("detail", "")).splitlines()[0][:200] if s.get("detail") else "",
                    })
            res["stages"] = clean_stages
        if "evidence" in res and isinstance(res["evidence"], dict):
            res["evidence"] = sanitize_evidence_for_report(res["evidence"])

        return res
    finally:
        if was_asleep:
            try:
                put_to_sleep(serial)
            except Exception:
                pass
        lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded Facebook Identity Dry-Run Driver")
    parser.add_argument("--serial", required=True, help="Target device serial")
    parser.add_argument("--main-name", required=True, help="Main profile display name")
    parser.add_argument("--main-id", default=None, help="Expected main profile numeric ID")
    parser.add_argument("--target-name", default=None, help="Target Page display name")
    parser.add_argument("--target-id", default=None, help="Expected target Page numeric ID")
    parser.add_argument("--expected-geometry", default=None, help="Expected screen geometry, e.g. 1080x2408")
    parser.add_argument("--app-card-version", default=None, help="Expected App Card version")
    parser.add_argument("--model", default=None, help="Override MOBILERUN_MODEL per-process")
    parser.add_argument("--discovery", action="store_true", help="Discovery mode: return observed IDs with calibrated=false")
    parser.add_argument("--timeout", type=float, default=240.0, help="Operation timeout in seconds (default 240)")

    args = parser.parse_args()

    result = run_live_dry_run(
        serial=args.serial,
        main_name=args.main_name,
        main_id=args.main_id,
        target_name=args.target_name,
        target_id=args.target_id,
        expected_geometry=args.expected_geometry,
        app_card_version=args.app_card_version,
        model=args.model,
        discovery=args.discovery,
        timeout=args.timeout,
    )

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
