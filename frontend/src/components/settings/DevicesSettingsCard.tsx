"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  listPhoneDevices,
  createPhoneDevice,
  updatePhoneDevice,
  discoverPhoneDevices,
} from "@/lib/api";
import type {
  PhoneDevice,
  PhoneDeviceCreate,
  PhoneDeviceStatus,
  DiscoveredPhoneDevice,
} from "@/lib/types";

function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const res = (err as { response?: { data?: { detail?: unknown } } }).response;
    if (res?.data?.detail) {
      if (typeof res.data.detail === "string") return res.data.detail;
      if (Array.isArray(res.data.detail)) {
        return res.data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(", ");
      }
    }
  }
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred.";
}

export function DevicesSettingsCard() {
  const {
    data: devices = [],
    mutate: mutateDevices,
    isLoading: loadingDevices,
  } = useSWR(
    "phone-devices",
    () => listPhoneDevices().then((res) => res.data)
  );

  const {
    data: discoveryData,
    error: discoveryError,
    isLoading: loadingDiscovery,
    mutate: mutateDiscovery,
  } = useSWR(
    "phone-devices-discovery",
    () => discoverPhoneDevices().then((res) => res.data),
    {
      revalidateOnFocus: false,
      refreshInterval: 0,
      dedupingInterval: 30000,
      shouldRetryOnError: false,
    }
  );

  // Registration form state
  const [showRegisterForm, setShowRegisterForm] = useState(false);
  const [selectedPhoneKey, setSelectedPhoneKey] = useState<string>("manual");
  const [formName, setFormName] = useState("");
  const [formIp, setFormIp] = useState("");
  const [formSerial, setFormSerial] = useState("");
  const [formNodeId, setFormNodeId] = useState("");
  const [submittingDevice, setSubmittingDevice] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);

  // Test connection state
  const [testingConnection, setTestingConnection] = useState(false);
  const [lastConnectionFailure, setLastConnectionFailure] = useState(false);
  const [connectionTestResult, setConnectionTestResult] = useState<{
    success: boolean;
    message: string;
    deviceFeedback?: string;
  } | null>(null);

  const discoveredPhones: DiscoveredPhoneDevice[] = discoveryData?.devices || [];
  const connectionInfo = discoveryData?.connection;

  async function handleTestConnection(targetKey = selectedPhoneKey) {
    setTestingConnection(true);
    setConnectionTestResult(null);
    try {
      const res = await discoverPhoneDevices();
      const freshData = res.data;
      await mutateDiscovery(freshData, false);
      setLastConnectionFailure(false);

      const conn = freshData.connection;
      const devCount = freshData.devices?.length || 0;
      const adbOnlineCount = freshData.devices?.filter((d) => d.adb_state === "device").length || 0;
      const warnings = freshData.warnings || [];

      let msg = conn?.backend_state === "Running"
        ? `Tailscale running (${conn.account_name || "Active account"}${conn.tailnet_name ? ` · ${conn.tailnet_name}` : ""}). Discovered ${devCount} phone(s), ${adbOnlineCount} connected via ADB.`
        : `Tailscale reachable. Discovered ${devCount} phone(s), ${adbOnlineCount} connected via ADB.`;

      if (warnings.includes("adb_unavailable")) {
        msg += " Warning: Host ADB is currently unavailable.";
      }

      let devFeedback: string | undefined = undefined;
      if (targetKey && targetKey !== "manual") {
        const found = freshData.devices?.find(
          (d) => (d.tailscale_node_id || d.tailscale_ip) === targetKey
        );
        if (found) {
          devFeedback = `${found.name}: ${found.online ? "Online" : "Offline"} · ADB: ${found.adb_state} · Runner allowed: ${found.runner_allowed ? "Yes" : "No"}`;
        } else {
          devFeedback = "Selected phone is no longer present in fresh Tailscale discovery.";
        }
      } else if (targetKey === "manual") {
        devFeedback = "Manual connection targets cannot be individually verified by Tailscale discovery.";
      }

      setConnectionTestResult({
        success: true,
        message: msg,
        deviceFeedback: devFeedback,
      });
    } catch (err) {
      setLastConnectionFailure(true);
      setConnectionTestResult({
        success: false,
        message: `Tailscale connection test failed: ${getErrorMessage(err)}`,
      });
    } finally {
      setTestingConnection(false);
    }
  }

  function handleSelectDiscoveredPhone(e: React.ChangeEvent<HTMLSelectElement>) {
    const key = e.target.value;
    setSelectedPhoneKey(key);
    setDeviceError(null);
    setConnectionTestResult(null);

    if (key === "manual") {
      return;
    }

    const dev = discoveredPhones.find((d) => (d.tailscale_node_id || d.tailscale_ip) === key);
    if (dev) {
      setFormName(dev.name);
      setFormIp(dev.tailscale_ip);
      setFormSerial(dev.adb_serial);
      setFormNodeId(dev.tailscale_node_id || "");
    }
  }

  async function handleRegisterDevice(e: React.FormEvent) {
    e.preventDefault();
    setDeviceError(null);
    if (!formName.trim()) {
      setDeviceError("Device name is required.");
      return;
    }
    setSubmittingDevice(true);
    try {
      const payload: PhoneDeviceCreate = {
        name: formName.trim(),
        tailscale_ip: formIp.trim() || null,
        adb_serial: formSerial.trim() || null,
        tailscale_node_id: formNodeId.trim() || null,
        status: "maintenance",
      };
      await createPhoneDevice(payload);
      setFormName("");
      setFormIp("");
      setFormSerial("");
      setFormNodeId("");
      setSelectedPhoneKey("manual");
      setConnectionTestResult(null);
      setShowRegisterForm(false);
      mutateDevices();
    } catch (err) {
      setDeviceError(getErrorMessage(err));
    } finally {
      setSubmittingDevice(false);
    }
  }

  async function handleToggleDeviceStatus(device: PhoneDevice) {
    const nextStatus: PhoneDeviceStatus = device.status === "available" ? "maintenance" : "available";
    try {
      await updatePhoneDevice(device.id, { status: nextStatus });
      mutateDevices();
    } catch (err) {
      alert(getErrorMessage(err));
    }
  }

  const isCurrentKeyInDiscovery =
    selectedPhoneKey === "manual" ||
    discoveredPhones.some((d) => (d.tailscale_node_id || d.tailscale_ip) === selectedPhoneKey);

  const isChecking = testingConnection || (loadingDiscovery && !discoveryData);
  const isUnavailable =
    !isChecking &&
    (Boolean(discoveryError) || lastConnectionFailure || connectionInfo?.backend_state !== "Running");
  const isConnected = !isChecking && !isUnavailable && connectionInfo?.backend_state === "Running";

  return (
    <section className="card space-y-5" id="devices">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-divider-soft pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-text-primary">Phone Devices &amp; Tailscale</h2>
            <span className="badge badge-gray">{devices.length} registered</span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5">
            Physical Android phones connected via Tailnet VPN and ADB for your social accounts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setShowRegisterForm(!showRegisterForm);
              setDeviceError(null);
              setConnectionTestResult(null);
            }}
            className="btn-secondary text-xs py-1.5 px-3"
          >
            <Icon icon={showRegisterForm ? "solar:close-circle-bold-duotone" : "solar:add-circle-bold-duotone"} width={16} />
            {showRegisterForm ? "Cancel" : "Register Phone"}
          </button>
          <Link
            href="/brands/accounts"
            className="btn-ghost text-xs py-1.5 px-3 flex items-center gap-1.5"
          >
            <Icon icon="solar:link-bold-duotone" width={16} />
            Connect Accounts
          </Link>
        </div>
      </div>

      {/* Tailscale Discovery & Connection Bar */}
      <div className="rounded-lg border border-divider-soft bg-bg-paper-hover p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-full bg-primary-lighter/40 text-primary-dark flex items-center justify-center flex-shrink-0">
            <Icon icon="solar:server-square-bold-duotone" width={16} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-text-primary">
                {connectionInfo?.account_name || "Tailscale Network"}
              </span>
              {connectionInfo?.tailnet_name && (
                <span className="text-text-secondary">({connectionInfo.tailnet_name})</span>
              )}
              {isChecking ? (
                <span className="badge badge-gray">Checking…</span>
              ) : isUnavailable ? (
                <span className="badge badge-yellow">Unavailable</span>
              ) : isConnected ? (
                <span className="badge badge-green">Connected</span>
              ) : (
                <span className="badge badge-gray">Idle</span>
              )}
            </div>
            <p className="text-[11px] text-text-secondary mt-0.5">
              {isChecking
                ? "Checking host discovery…"
                : isUnavailable
                ? "Tailscale connection unavailable or unverified"
                : `${discoveredPhones.length} phone(s) detected on tailnet, ${
                    discoveredPhones.filter((d) => d.adb_state === "device").length
                  } active in ADB`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            type="button"
            disabled={testingConnection}
            onClick={() => handleTestConnection()}
            className="btn-outline text-xs py-1 px-2.5 flex items-center gap-1.5"
          >
            <Icon
              icon={testingConnection ? "solar:refresh-circle-bold-duotone" : "solar:radar-bold-duotone"}
              width={14}
              className={testingConnection ? "animate-spin text-primary-main" : ""}
            />
            <span>{testingConnection ? "Testing…" : "Test Connection"}</span>
          </button>
          <a
            href="https://login.tailscale.com/admin/machines"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-primary-main hover:underline flex items-center gap-1 py-1 px-2"
          >
            <span>Manage Tailscale</span>
            <Icon icon="solar:arrow-right-up-linear" width={12} />
          </a>
        </div>
      </div>

      {/* Connection Test / Status Result Banner */}
      {connectionTestResult && (
        <div
          className={`rounded-lg p-3 text-xs flex items-start justify-between border ${
            connectionTestResult.success
              ? "border-primary-main/40 bg-primary-lighter/30 text-primary-dark"
              : "border-error-main/40 bg-error-lighter/30 text-error-darker"
          }`}
        >
          <div className="flex items-start gap-2">
            <Icon
              icon={connectionTestResult.success ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
              width={16}
              className="flex-shrink-0 mt-0.5"
            />
            <div className="space-y-0.5">
              <p className="font-medium">{connectionTestResult.message}</p>
              {connectionTestResult.deviceFeedback && (
                <p className="text-[11px] opacity-90">{connectionTestResult.deviceFeedback}</p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setConnectionTestResult(null)}
            className="text-text-disabled hover:text-text-primary ml-2 flex-shrink-0"
          >
            <Icon icon="solar:close-circle-bold" width={14} />
          </button>
        </div>
      )}

      {/* Actionable Architecture Note */}
      <div className="rounded-lg border border-primary-main/20 bg-primary-lighter/10 p-3.5 flex items-start gap-3 text-xs text-text-secondary">
        <Icon icon="solar:info-circle-bold-duotone" width={20} className="text-primary-main flex-shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-semibold text-text-primary">Phone Automation Setup</p>
          <p>
            Choose an online Android phone from your Tailscale tailnet or enter connection details manually.
            Phones are registered in Maintenance mode; connect and calibrate your Facebook and social media accounts in the Social Media dashboard before publishing.
          </p>
        </div>
      </div>

      {/* Register Device Form */}
      {showRegisterForm && (
        <form onSubmit={handleRegisterDevice} className="card-sm bg-bg-paper-hover space-y-4 border border-divider-soft">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text-primary">Register New Phone Device</h3>
            <span className="text-[11px] text-text-secondary">Host VPS Phone Runner target</span>
          </div>

          {deviceError && (
            <div className="rounded-md border border-error-main/40 bg-error-lighter/30 p-2.5 text-xs text-error-darker">
              {deviceError}
            </div>
          )}

          <div className="space-y-3">
            {/* Top Dropdown: Tailscale Phone Chooser */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="tailscale-phone-select" className="label mb-0">
                  Choose a phone from Tailscale
                </label>
                <button
                  type="button"
                  disabled={loadingDiscovery || testingConnection}
                  onClick={() => handleTestConnection()}
                  className="text-[11px] text-primary-main hover:underline flex items-center gap-1"
                >
                  <Icon icon="solar:refresh-bold" width={12} className={loadingDiscovery ? "animate-spin" : ""} />
                  Refresh Tailscale list
                </button>
              </div>
              <select
                id="tailscale-phone-select"
                className="input text-xs py-2"
                value={selectedPhoneKey}
                onChange={handleSelectDiscoveredPhone}
              >
                <option value="manual">Manual connection (enter details below)</option>
                {discoveredPhones.map((d) => {
                  const key = d.tailscale_node_id || d.tailscale_ip;
                  const onlineLabel = d.online ? "Online" : "Offline";
                  const adbLabel = `ADB: ${d.adb_state}`;
                  return (
                    <option key={key} value={key}>
                      {d.name} ({d.tailscale_ip}) · {onlineLabel} · {adbLabel}
                    </option>
                  );
                })}
                {!isCurrentKeyInDiscovery && selectedPhoneKey !== "manual" && (
                  <option value={selectedPhoneKey}>
                    {formName || selectedPhoneKey} (Not currently discovered)
                  </option>
                )}
              </select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
              <div>
                <label htmlFor="device-name-input" className="label">Device Name *</label>
                <input
                  id="device-name-input"
                  className="input-rect text-xs py-2"
                  placeholder="e.g. Phone 1 (Realme)"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  required
                />
              </div>
              <div>
                <label htmlFor="device-serial-input" className="label">ADB Serial</label>
                <input
                  id="device-serial-input"
                  className="input-rect text-xs py-2"
                  placeholder="e.g. 100.90.12.34:5555"
                  value={formSerial}
                  onChange={(e) => {
                    setFormSerial(e.target.value);
                    setSelectedPhoneKey("manual");
                    setConnectionTestResult(null);
                  }}
                />
              </div>
              <div>
                <label htmlFor="device-ip-input" className="label">Tailscale IP</label>
                <input
                  id="device-ip-input"
                  className="input-rect text-xs py-2"
                  placeholder="e.g. 100.90.12.34"
                  value={formIp}
                  onChange={(e) => {
                    setFormIp(e.target.value);
                    setSelectedPhoneKey("manual");
                    setConnectionTestResult(null);
                  }}
                />
              </div>
              <div>
                <label htmlFor="device-node-input" className="label">Tailscale Node ID</label>
                <input
                  id="device-node-input"
                  className="input-rect text-xs py-2"
                  placeholder="e.g. node-xyz123"
                  value={formNodeId}
                  onChange={(e) => {
                    setFormNodeId(e.target.value);
                    setSelectedPhoneKey("manual");
                    setConnectionTestResult(null);
                  }}
                />
              </div>
            </div>

            <p className="text-[11px] text-text-secondary italic">
              Register your phone, then connect social accounts in Social Media. Newly registered devices start in Maintenance mode.
            </p>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-divider-soft">
            <button
              type="button"
              onClick={() => {
                setShowRegisterForm(false);
                setSelectedPhoneKey("manual");
                setConnectionTestResult(null);
              }}
              className="btn-ghost text-xs py-1.5 px-3"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submittingDevice}
              className="btn-primary text-xs py-1.5 px-4"
            >
              {submittingDevice ? "Registering…" : "Save Phone Device"}
            </button>
          </div>
        </form>
      )}

      {/* Devices List */}
      {loadingDevices ? (
        <div className="py-6 text-center text-xs text-text-secondary">Loading registered devices…</div>
      ) : devices.length === 0 ? (
        <div className="py-8 text-center rounded-lg border border-dashed border-divider-soft space-y-2">
          <Icon icon="solar:smartphone-2-bold-duotone" width={32} className="mx-auto text-text-disabled" />
          <p className="text-sm font-semibold text-text-primary">No Phone Devices Registered</p>
          <p className="text-xs text-text-secondary max-w-sm mx-auto">
            Register your VPS-connected Android phone to enable automated Facebook publishing.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {devices.map((device) => {
            const isAvailable = device.status === "available";

            return (
              <div
                key={device.id}
                className="rounded-lg border border-divider-soft bg-bg-paper p-4 space-y-3 hover:border-text-disabled transition-colors"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-full bg-primary-lighter/40 text-primary-darker flex items-center justify-center flex-shrink-0">
                      <Icon icon="solar:smartphone-bold-duotone" width={18} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-text-primary">{device.name}</span>
                        <span
                          className={`badge ${
                            device.status === "available"
                              ? "badge-green"
                              : device.status === "in_use"
                              ? "badge-blue"
                              : "badge-yellow"
                          }`}
                        >
                          {device.status}
                        </span>
                      </div>
                      <p className="text-xs text-text-secondary mt-0.5">
                        Shared automation runner ·{" "}
                        <Link href="/brands/accounts" className="text-primary-main hover:underline">
                          Assign accounts in Accounts &amp; phones &rarr;
                        </Link>
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleToggleDeviceStatus(device)}
                      className="btn-ghost text-xs py-1 px-2.5"
                      title={isAvailable ? "Set to maintenance mode" : "Set to available"}
                    >
                      {isAvailable ? "Set Maintenance" : "Set Available"}
                    </button>
                  </div>
                </div>

                {/* Device technical metadata */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] bg-bg-paper-hover p-2.5 rounded-md font-mono text-text-secondary">
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Tailscale IP</span>
                    <span className="text-text-primary">{device.tailscale_ip || "—"}</span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">ADB Serial</span>
                    <span className="text-text-primary truncate block" title={device.adb_serial || ""}>
                      {device.adb_serial || "—"}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Node ID</span>
                    <span className="text-text-primary truncate block" title={device.tailscale_node_id || ""}>
                      {device.tailscale_node_id || "—"}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Last Seen</span>
                    <span className="text-text-primary">{device.last_seen_at || "—"}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
