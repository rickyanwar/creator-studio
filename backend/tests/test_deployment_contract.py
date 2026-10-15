import os
from pathlib import Path
import subprocess
import yaml
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
SERVICE_UNIT_PATH = REPO_ROOT / "deploy" / "phone-runner.service"
README_PATH = REPO_ROOT / "phone_runner" / "README.md"


@pytest.fixture(scope="module")
def workflow_doc():
    assert DEPLOY_WORKFLOW_PATH.is_file(), f"Workflow file not found: {DEPLOY_WORKFLOW_PATH}"
    content = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)
    return doc, content


@pytest.fixture(scope="module")
def workflow_script(workflow_doc):
    doc, _ = workflow_doc
    deploy_job = doc.get("jobs", {}).get("deploy", {})
    steps = deploy_job.get("steps", [])
    ssh_step = next((s for s in steps if s.get("uses", "").startswith("appleboy/ssh-action")), None)
    assert ssh_step is not None, "SSH deploy step not found in workflow"
    script = ssh_step.get("with", {}).get("script", "")
    assert script, "Script string is empty in SSH deploy step"
    return script


def test_deploy_workflow_concurrency(workflow_doc):
    doc, _ = workflow_doc
    concurrency = doc.get("concurrency")
    assert isinstance(concurrency, dict), "Workflow must define a top-level concurrency mapping"
    assert concurrency.get("group") == "production-deploy"
    assert concurrency.get("cancel-in-progress") is False


def test_deploy_workflow_bash_syntax(workflow_script):
    res = subprocess.run(["bash", "-n"], input=workflow_script, text=True, capture_output=True)
    assert res.returncode == 0, f"Bash syntax check failed:\n{res.stderr}"


def test_deploy_workflow_migration_order_and_isolation(workflow_script):
    # Builds must happen before migration
    build_idx = workflow_script.find("docker compose -p studio -f docker-compose.server.yml build")
    assert build_idx != -1, "Docker compose build command missing"

    # Migration must run in isolated one-off container with --no-deps before restart
    migration_idx = workflow_script.find("run --rm --no-deps api alembic upgrade head")
    assert migration_idx != -1, "Alembic upgrade must use run --rm --no-deps api"
    assert "exec -T api alembic upgrade head" not in workflow_script, "Must never exec old running api for migration"

    # Migration must precede service restart
    up_idx = workflow_script.find("docker compose -p studio -f docker-compose.server.yml up -d --remove-orphans")
    assert up_idx != -1, "Docker compose up command missing"

    assert build_idx < migration_idx, "Build must complete before running migration"
    assert migration_idx < up_idx, "Migration must run before starting/restarting containers"

    # Alembic current head verification must follow upgrade and precede up
    verify_head_idx = workflow_script.find("alembic current")
    assert verify_head_idx != -1, "Alembic current verification missing"
    assert migration_idx < verify_head_idx < up_idx, "Alembic current must verify head before up"


def test_deploy_workflow_backup_before_migration(workflow_script):
    dump_idx = workflow_script.find("pg_dump -U studio -Fc studio")
    restore_verify_idx = workflow_script.find("pg_restore --list")
    migration_idx = workflow_script.find("alembic upgrade head")

    assert dump_idx != -1, "Pre-migration pg_dump missing"
    assert restore_verify_idx != -1, "Pre-migration pg_restore --list verification missing"
    assert dump_idx < restore_verify_idx < migration_idx, "Backup and verify must occur before alembic upgrade"

    # Directory and file permission hardening
    assert "chmod 700" in workflow_script, "Backup directory must be chmod 700"
    assert "umask 077" in workflow_script, "Backup creation subshell must enforce umask 077"
    assert "-s" in workflow_script, "Backup file nonempty check missing"


def test_deploy_workflow_rollback_preserves_db_no_automatic_downgrade(workflow_script):
    # Health check must poll internal endpoint using python urllib
    assert "http://127.0.0.1:8000/health" in workflow_script
    assert "urllib.request" in workflow_script

    # On health failure, containers recreated with rollback tags
    assert "force-recreate" in workflow_script
    assert "rollback" in workflow_script

    # Critical: no automatic DB downgrade or restore on failure
    assert "alembic downgrade" not in workflow_script, "Workflow must not automatically downgrade DB"
    assert "pg_restore -d" not in workflow_script, "Workflow must not automatically restore DB"


def test_deploy_workflow_dirty_tracked_server_check(workflow_script):
    # Server tracked files must be verified clean before pulling
    clean_check_idx = workflow_script.find("git status --porcelain -uno")
    pull_idx = workflow_script.find("git pull origin main")
    assert clean_check_idx != -1, "Clean repository check missing"
    assert pull_idx != -1, "Git pull command missing"
    assert clean_check_idx < pull_idx, "Must check tracked files clean before pull"


def test_phone_runner_service_unit_contract():
    assert SERVICE_UNIT_PATH.is_file(), f"Service unit missing: {SERVICE_UNIT_PATH}"
    content = SERVICE_UNIT_PATH.read_text(encoding="utf-8")

    # State & working directory configuration
    assert "StateDirectory=phone-runner" in content
    assert "StateDirectoryMode=0700" in content
    assert "WorkingDirectory=/var/lib/phone-runner" in content

    # Environment variables
    assert "Environment=PYTHONPATH=/opt/phone_runner" in content
    assert "Environment=MOBILERUN_TELEMETRY_ENABLED=false" in content
    assert "Environment=XDG_CONFIG_HOME=/var/lib/phone-runner/.config" in content
    assert "Environment=XDG_CACHE_HOME=/var/lib/phone-runner/.cache" in content

    # Security settings
    assert "ProtectSystem=strict" in content
    assert "ProtectHome=read-only" in content
    assert "PrivateTmp=true" in content
    assert "NoNewPrivileges=true" in content

    # No broad /tmp or /root read-write paths
    assert "ReadWritePaths=/tmp" not in content
    assert "ReadWritePaths=/root" not in content


def test_phone_runner_docs_contract():
    assert README_PATH.is_file(), f"README missing: {README_PATH}"
    content = README_PATH.read_text(encoding="utf-8")

    assert "/opt/phone_runner/phone_runner" in content
    assert "/etc/phone-runner-mobilerun.yaml" in content
    assert "chmod 600 /etc/phone-runner.env" in content
    assert "chmod 644 /etc/phone-runner-mobilerun.yaml" in content
    assert "/var/lib/phone-runner" in content
    assert "0.0.0.0" in content  # Contains warnings against 0.0.0.0


def extract_deploy_lifecycle_script(workflow_script: str) -> str:
    tag_start = workflow_script.find('echo "── Recording rollback image tags')
    tag_end = workflow_script.find('echo "── Rebuilding images')
    assert tag_start != -1 and tag_end != -1, "Tag recording section not found"
    tag_section = workflow_script[tag_start:tag_end].strip()

    restart_start = workflow_script.find('echo "── Restarting services ──"')
    restart_end = workflow_script.find('echo "── gflow-worker ──"')
    assert restart_start != -1 and restart_end != -1, "Restart/health/rollback section not found"
    restart_section = workflow_script[restart_start:restart_end].strip()

    return f"""set -euo pipefail
DEPLOY_ID="20261016_120000"
{tag_section}
{restart_section}
"""


def _setup_fake_env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_log = tmp_path / "docker.log"
    curl_log = tmp_path / "curl.log"

    docker_sh = bin_dir / "docker"
    docker_sh.write_text(f"""#!/usr/bin/env bash
set -e
echo "$@" >> "{docker_log}"

case "$1" in
  compose)
    shift
    while [ "$#" -gt 0 ]; do
      case "$1" in
        ps)
          echo "fake_${{3:-svc}}_cid"
          exit 0
          ;;
        up)
          shift
          for arg in "$@"; do
            if [ "$arg" = "--force-recreate" ]; then
              touch "{tmp_path}/in_rollback"
              exit 0
            fi
          done
          if [ "${{SIMULATE_UP_FAIL:-0}}" = "1" ]; then
            echo "Simulated compose up failure" >&2
            exit 1
          fi
          exit 0
          ;;
        restart)
          shift
          if [ "${{SIMULATE_NGINX_RESTART_FAIL:-0}}" = "1" ] && [ "$1" = "nginx" ] && [ ! -f "{tmp_path}/in_rollback" ]; then
            echo "Simulated nginx restart failure" >&2
            exit 1
          fi
          exit 0
          ;;
        exec)
          shift
          if [ -f "{tmp_path}/in_rollback" ]; then
            if [ "${{SIMULATE_ROLLBACK_HEALTH_FAIL:-0}}" = "1" ]; then
              exit 1
            fi
            exit 0
          fi
          if [ "${{SIMULATE_API_HEALTH_FAIL:-0}}" = "1" ]; then
            exit 1
          fi
          exit 0
          ;;
        *)
          shift
          ;;
      esac
    done
    exit 0
    ;;
  inspect)
    shift
    if echo "$@" | grep -q '{{.Image}}'; then
      cid="${{@: -1}}"
      echo "sha256:img_${{cid}}"
      exit 0
    elif echo "$@" | grep -q '{{.Config.Image}}'; then
      if [ "${{SIMULATE_RAW_DIGEST:-0}}" = "1" ]; then
        echo "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
      else
        echo "studio-custom:tag"
      fi
      exit 0
    fi
    exit 0
    ;;
  tag)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""", encoding="utf-8")
    docker_sh.chmod(0o755)

    curl_sh = bin_dir / "curl"
    curl_sh.write_text(f"""#!/usr/bin/env bash
set -e
echo "$@" >> "{curl_log}"
if [ -f "{tmp_path}/in_rollback" ]; then
  exit 0
fi
if [ "${{SIMULATE_INGRESS_HEALTH_FAIL:-0}}" = "1" ]; then
  exit 1
fi
exit 0
""", encoding="utf-8")
    curl_sh.chmod(0o755)

    sleep_sh = bin_dir / "sleep"
    sleep_sh.write_text("""#!/usr/bin/env bash
exit 0
""", encoding="utf-8")
    sleep_sh.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env, docker_log, curl_log


def test_deploy_workflow_ingress_and_app_services_contract(workflow_script):
    # Ingress probe parameters
    assert "--resolve autostudio.pro:443:127.0.0.1" in workflow_script
    assert "https://autostudio.pro/api/health" in workflow_script
    assert "--max-time 5" in workflow_script

    # Application service isolation in rollback
    assert "--no-deps --force-recreate" in workflow_script
    assert 'APP_SERVICES="api worker worker-gallery worker-visual worker-video beat frontend renderer nginx"' in workflow_script

    # Database & Redis protection
    assert "db" not in 'api worker worker-gallery worker-visual worker-video beat frontend renderer nginx'.split()
    assert "redis" not in 'api worker worker-gallery worker-visual worker-video beat frontend renderer nginx'.split()


def test_simulated_startup_up_failure_triggers_rollback(tmp_path, workflow_script):
    env, docker_log, _ = _setup_fake_env(tmp_path)
    env["SIMULATE_UP_FAIL"] = "1"
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 1, f"Expected rollback failure exit code 1, got {res.returncode}"

    log_content = docker_log.read_text(encoding="utf-8")
    recreate_lines = [l for l in log_content.splitlines() if "--force-recreate" in l]
    assert len(recreate_lines) == 1, "Expected exactly one --force-recreate invocation in rollback"

    rec_cmd = recreate_lines[0].split()
    assert "db" not in rec_cmd, "db must NEVER be in --force-recreate"
    assert "redis" not in rec_cmd, "redis must NEVER be in --force-recreate"
    for svc in ["api", "worker", "worker-gallery", "worker-visual", "worker-video", "beat", "frontend", "renderer", "nginx"]:
        assert svc in rec_cmd, f"Expected {svc} in rollback recreation"

    assert "tag studio-backend:rollback-20261016_120000 studio-backend:latest" in log_content
    assert "tag studio-renderer:rollback-20261016_120000 studio-renderer" in log_content
    assert "tag studio-frontend:rollback-20261016_120000 studio-frontend" in log_content


def test_simulated_nginx_restart_failure_triggers_rollback(tmp_path, workflow_script):
    env, docker_log, _ = _setup_fake_env(tmp_path)
    env["SIMULATE_NGINX_RESTART_FAIL"] = "1"
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 1, f"Expected rollback failure exit code 1, got {res.returncode}"

    log_content = docker_log.read_text(encoding="utf-8")
    recreate_lines = [l for l in log_content.splitlines() if "--force-recreate" in l]
    assert len(recreate_lines) == 1
    assert "db" not in recreate_lines[0].split()
    assert "redis" not in recreate_lines[0].split()


def test_simulated_api_health_failure_triggers_rollback(tmp_path, workflow_script):
    env, docker_log, _ = _setup_fake_env(tmp_path)
    env["SIMULATE_API_HEALTH_FAIL"] = "1"
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 1, f"Expected rollback failure exit code 1, got {res.returncode}"

    log_content = docker_log.read_text(encoding="utf-8")
    assert "--force-recreate" in log_content
    assert "tag studio-backend:rollback-20261016_120000 studio-backend:latest" in log_content


def test_simulated_ingress_health_failure_triggers_rollback(tmp_path, workflow_script):
    env, docker_log, curl_log = _setup_fake_env(tmp_path)
    env["SIMULATE_INGRESS_HEALTH_FAIL"] = "1"
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 1, f"Expected rollback failure exit code 1, got {res.returncode}"

    curl_content = curl_log.read_text(encoding="utf-8")
    assert "--resolve autostudio.pro:443:127.0.0.1" in curl_content
    assert "https://autostudio.pro/api/health" in curl_content

    log_content = docker_log.read_text(encoding="utf-8")
    assert "--force-recreate" in log_content


def test_simulated_healthy_path_completes_without_rollback(tmp_path, workflow_script):
    env, docker_log, _ = _setup_fake_env(tmp_path)
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 0, f"Expected healthy run exit code 0, got {res.returncode}:\n{res.stderr}"

    log_content = docker_log.read_text(encoding="utf-8")
    assert "--force-recreate" not in log_content
    # Normal restart nginx should run
    assert any("restart nginx" in l for l in log_content.splitlines())


def test_simulated_raw_digest_old_config_does_not_break_canonical_tags(tmp_path, workflow_script):
    env, docker_log, _ = _setup_fake_env(tmp_path)
    env["SIMULATE_RAW_DIGEST"] = "1"
    env["SIMULATE_UP_FAIL"] = "1"
    script = extract_deploy_lifecycle_script(workflow_script)

    res = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)
    assert res.returncode == 1

    log_content = docker_log.read_text(encoding="utf-8")
    tag_lines = [l for l in log_content.splitlines() if l.startswith("tag ")]
    for line in tag_lines:
        target = line.split()[-1]
        assert not target.startswith("sha256:"), f"Tag target must be canonical, got {target}"
    assert any("studio-backend:latest" in l for l in tag_lines)
    assert any("studio-renderer" in l for l in tag_lines)
    assert any("studio-frontend" in l for l in tag_lines)
