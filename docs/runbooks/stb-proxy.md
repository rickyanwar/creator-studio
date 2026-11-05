# Runbook — STB residential proxy (`stb-proxy`)

Owner of day-to-day operation: **Hermes** (VPS agent). Owner (human) handles anything
physical at home: power, cables, router.

## 1. What it is

Everything that must not be seen with the VPS datacenter IP goes out through a SOCKS5
proxy on a set-top box (STB) at the owner's home. It replaced the phone's `hev-socks5`
app on **2026-11-05**. The exit IP did not change (the phone used the same home WiFi),
so no Google/ChatGPT session needed a re-login.

```
VPS service ──Tailscale──▶ STB stb-proxy (Dante :1080) ──home LAN──▶ IndiHome router ──▶ internet
```

| Item | Value |
|---|---|
| Proxy URL | `socks5h://100.77.191.71:1080` (use `socks5h` so DNS resolves at home) |
| Tailscale node | `stb-proxy`, `100.77.191.71` |
| LAN IP | `192.168.100.177` (static, NetworkManager "Wired connection 1", gw/DNS `192.168.100.1`) |
| Exit IP (2026-11-05) | `110.136.219.150`, AS7713 Telkom Indonesia (can change when IndiHome renews) |
| Hardware | Amlogic S9xx box, `aml-s9xx-box`, Armbian 25.8 (Ubuntu 24.04, aarch64), 2 GB RAM, 8 GB eMMC |
| Extra software | CasaOS (web UI on :80), Docker |

The phone (`realme-9i-5g`, `100.64.96.50`) is **no longer a proxy**. Its `hev.socks5` app is disabled (`pm disable-user`) since 2026-11-05. It is still the
phone_runner posting device over ADB (`100.64.96.50:5555`). Do not remove Tailscale from it.

## 2. Who uses the proxy (VPS)

| Consumer | Where the proxy is set |
|---|---|
| gemini-web-serve (Argon prompt polish) | `/etc/gemini-web-serve.env` `GWS_DEFAULT_PROXY`, and `"proxy"` in each `/etc/gemini-web/accounts/*.json` |
| gflow-worker (Mode 8, Google Flow) | `/etc/gflow-worker.env` `GFLOW_PROXY`, `GFLOW_CLI_BROWSER_PROXY` |
| gpt-serve / `gpt` CLI (ChatGPT) | `/etc/gpt-serve.env` `GPT_PROXY`; defaults in `/root/chatgpt-cli/src/chatgpt_cli/config.py`, `/usr/local/bin/gpt-daemon` |
| ai-invite-hunter (cron) | `/root/ai-invite-hunter/.env` `SOCKS_PROXY` |
| Hermes scripts | `~/.hermes/scripts/gemini_health_monitor.sh`, `gflow_auth_renew_cron.sh` |

Code defaults that still name the phone (`gflow_worker/proxy_guard.py` revive,
`deploy/gemini-web-serve/server.py` / `login.py` defaults) are dead paths: the env files
above always win. `proxy_guard.revive_phone_proxy` can no longer fix anything.

## 3. Access

Credentials live **only** on the VPS in `/root/.config/stb-proxy/credentials` (mode 600):
STB root SSH password, CasaOS admin user/password, IPs. Never commit them, never paste
them into Telegram or chat.

```bash
ssh stb-proxy                      # from the VPS as root: key auth (/root/.ssh/id_ed25519), alias in /root/.ssh/config
ssh stb-proxy 'systemctl is-active tailscaled danted'
curl -s -m 15 --socks5-hostname 100.77.191.71:1080 https://api.ipify.org   # proxy liveness + exit IP
```

CasaOS UI: `http://100.77.191.71` (over Tailscale) or `http://192.168.100.177` (home LAN).

## 4. How the STB is set up

| Piece | Config | Notes |
|---|---|---|
| Static IP | NetworkManager, `ipv4.method manual`, autoconnect yes | Survives reboot |
| Tailscale | apt repo `pkgs.tailscale.com`, `tailscaled` enabled, hostname `stb-proxy` | Node key expiry is **disabled** (done 2026-11-05 in the Tailscale admin console); keep it that way or the STB drops off the tailnet |
| Dante | `/etc/danted.conf`; listens on `tailscale0:1080` only; clients `100.64.0.0/10` only; blocks 10/8, 172.16/12, 192.168/16, 127/8 as destinations | Not reachable from LAN or internet (verified) |
| Dante unit | `/etc/systemd/system/danted.service.d/override.conf`: waits for a `tailscale0` IPv4, `Restart=always` | Original config kept at `/etc/danted.conf.orig` |
| HW watchdog | `/etc/systemd/system.conf.d/10-hw-watchdog.conf`: `RuntimeWatchdogSec=30s`, `RebootWatchdogSec=2min` | A hung box reboots itself |
| Filesystem | kernel `fsck.repair=yes` | Auto-repair after power loss |

**Power cut:** the box boots on its own when power returns (no power button state), gets
`192.168.100.177`, starts `tailscaled`, then `danted`. Verified with a reboot on
2026-11-05. The proxy is down only while home power or IndiHome is down.

## 5. Monitoring and alerts

| Piece | What it does |
|---|---|
| `/usr/local/bin/phone-proxy-watchdog.sh` (timer `phone-proxy-watchdog.timer`, every 5 min; copy at `/root/chatgpt-cli/ops/`) | Probes the STB proxy (SOCKS5 greeting + real egress). Also keeps the phone's ADB link and Tailscale app awake for phone_runner. Writes one line per tick to `/var/log/phone-proxy-watchdog.log`: `up proxy=open via=stb egress=<ip> phone_adb=<0/1>` or `DOWN proxy=<closed/no_egress> ...`. The name is historical; keep it, gpt-heal.py calls it. |
| Hermes job `stb-proxy-down-alert` (`f4f3b46eb1e5`, script `phone-down-alert.sh`) | Telegram message to the owner when ≥2 DOWN ticks in 10 min, or the latest tick is DOWN. Silent when healthy. |
| Hermes jobs `gpt-cli health alert`, `gpt-serve self-heal`, `gemini-cli-health-watchdog`, gemini-web-serve runbook | Report `proxy=down` / `proxy_down`; they point at the STB, not the phone. |

## 6. Hermes playbook — proxy down

1. Confirm from the VPS:
   ```bash
   tail -n 5 /var/log/phone-proxy-watchdog.log
   tailscale status | grep stb-proxy
   curl -s -m 15 --socks5-hostname 100.77.191.71:1080 https://api.ipify.org
   ```
2. **`stb-proxy` offline in Tailscale** → the box, home power or IndiHome is down. Hermes
   cannot fix this. Send the owner one message: *"STB proxy offline (stb-proxy). ChatGPT,
   Gemini and Google Flow are paused. Check the STB power and home internet."* Stop. Do not
   switch any service to a direct connection (that exposes the datacenter IP).
3. **Online but proxy fails** → `ssh stb-proxy 'systemctl restart danted; sleep 3; systemctl is-active danted'`,
   re-run the curl. If still failing: `ssh stb-proxy 'journalctl -u danted -n 30 --no-pager'`
   and send the owner a short summary.
4. **Proxy works but the exit IP changed** → normal (IndiHome renewed). No action.
5. Restart cap: 2 `danted` restarts per incident, then hand over to the owner.

Allowed for Hermes on the STB: read status/logs, restart `danted` / `tailscaled`, reboot the
STB once per incident if SSH works but services do not recover. Not allowed: changing
`/etc/danted.conf`, network settings, the root password, CasaOS users, or installing
software, unless the owner asks.

## 7. Rollback to the phone proxy

Only if the owner asks. Backups taken on 2026-11-05 on the VPS:
`/root/backup-proxy-20261105-104258` (gemini-web-serve env + accounts, gflow-worker env)
and `/root/backup-proxy-hermes-20261105-105502` (gpt-daemon, chatgpt-cli config,
ai-invite-hunter, Hermes scripts/skills/memory/jobs, old watchdog). Restore the files,
re-enable and start `hev-socks5` on the phone (it was disabled on 2026-11-05 to save
battery: `adb -s 100.64.96.50:5555 shell pm enable hev.socks5`, then
`am start-foreground-service -a hev.socks5.START hev.socks5/.Socks5Service`), then restart
`gemini-web-serve`, `gflow-worker`, `gpt-serve`.

## 8. Router (IndiHome ONT Huawei HG8145V5, `http://192.168.100.1`)

Owner-managed; credentials are not stored anywhere in this repo.

- **DHCP Static IP** (Advanced → LAN → DHCP Static IP): `72:69:c9:82:5a:59` → `192.168.100.177`,
  set 2026-11-05. The DHCP pool is `.2–.254`, so this binding stops any other device from
  taking the STB's address.
- **QoS / priority:** this firmware has no QoS, bandwidth or priority menu for the `admin`
  account (checked 2026-11-05). The STB is wired (LAN3), which already avoids WiFi
  contention, and proxy traffic is small (API calls and browser sessions). If the home line
  is saturated, the fix is on the owner's side (fewer heavy downloads) or a router that
  supports QoS.
