"use client";

import { useState } from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import {
  listPhoneDevices,
  createPhoneDevice,
  updatePhoneDevice,
  listSocialDestinations,
  listDeviceAssignments,
  createDeviceAssignment,
  deleteDeviceAssignment,
  runDeviceDryRun,
} from "@/lib/api";
import type { PhoneDevice, PhoneDeviceCreate, PhoneDeviceStatus, SocialDestination, DestinationDeviceAssignment } from "@/lib/types";

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
  const { data: devices = [], mutate: mutateDevices, isLoading: loadingDevices } = useSWR(
    "phone-devices",
    () => listPhoneDevices().then((res) => res.data)
  );

  const { data: destinations = [] } = useSWR(
    "social-destinations-all",
    () => listSocialDestinations().then((res) => res.data)
  );

  const { data: assignments = [], mutate: mutateAssignments } = useSWR(
    "device-assignments-all",
    () => listDeviceAssignments({ is_active: true }).then((res) => res.data)
  );

  // Registration form state
  const [showRegisterForm, setShowRegisterForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formIp, setFormIp] = useState("");
  const [formSerial, setFormSerial] = useState("");
  const [formNodeId, setFormNodeId] = useState("");
  const [formActorId, setFormActorId] = useState("");
  const [formActorName, setFormActorName] = useState("");
  const [formStatus, setFormStatus] = useState<PhoneDeviceStatus>("maintenance");
  const [submittingDevice, setSubmittingDevice] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);

  // Assignment draft state
  const [showAssignForm, setShowAssignForm] = useState(false);
  const [assignDestinationId, setAssignDestinationId] = useState<number | "">("");
  const [assignDeviceId, setAssignDeviceId] = useState<number | "">("");
  const [assignPriority, setAssignPriority] = useState<number>(0);
  const [submittingAssign, setSubmittingAssign] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);

  // Dry-run execution state
  const [dryRunRunning, setDryRunRunning] = useState<Record<string, boolean>>({});
  const [dryRunStatus, setDryRunStatus] = useState<{ id: string; success: boolean; message: string } | null>(null);

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
        expected_profile_actor_id: formActorId.trim() || null,
        expected_profile_name: formActorName.trim() || null,
        status: formStatus,
      };
      await createPhoneDevice(payload);
      setFormName("");
      setFormIp("");
      setFormSerial("");
      setFormNodeId("");
      setFormActorId("");
      setFormActorName("");
      setFormStatus("maintenance");
      setShowRegisterForm(false);
      mutateDevices();
    } catch (err) {
      setDeviceError(getErrorMessage(err));
    } finally {
      setSubmittingDevice(false);
    }
  }

  async function handleDraftAssignment(e: React.FormEvent) {
    e.preventDefault();
    setAssignError(null);
    if (assignDestinationId === "" || assignDeviceId === "") {
      setAssignError("Select both a destination and a device.");
      return;
    }
    setSubmittingAssign(true);
    try {
      await createDeviceAssignment({
        destination_id: Number(assignDestinationId),
        device_id: Number(assignDeviceId),
        priority: assignPriority,
        is_active: true,
      });
      setShowAssignForm(false);
      setAssignDestinationId("");
      setAssignDeviceId("");
      setAssignPriority(0);
      mutateAssignments();
    } catch (err) {
      setAssignError(getErrorMessage(err));
    } finally {
      setSubmittingAssign(false);
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

  async function handleRemoveAssignment(destinationId: number, deviceId: number) {
    if (!confirm("Deactivate this device assignment draft?")) return;
    try {
      await deleteDeviceAssignment(destinationId, deviceId);
      mutateAssignments();
    } catch (err) {
      alert(getErrorMessage(err));
    }
  }

  async function handleRunDryRun(deviceId: number, destinationId?: number) {
    const key = destinationId ? `${deviceId}-${destinationId}` : `${deviceId}`;
    setDryRunRunning((prev) => ({ ...prev, [key]: true }));
    setDryRunStatus(null);
    try {
      await runDeviceDryRun(deviceId, destinationId ? { destination_id: destinationId } : undefined);
      const isAssignment = !!destinationId;
      setDryRunStatus({
        id: key,
        success: true,
        message: isAssignment
          ? "Assignment verified successfully via host runner dry-run."
          : "Device calibration verified successfully.",
      });
      await Promise.all([mutateDevices(), mutateAssignments()]);
    } catch (err) {
      setDryRunStatus({
        id: key,
        success: false,
        message: getErrorMessage(err),
      });
    } finally {
      setDryRunRunning((prev) => ({ ...prev, [key]: false }));
    }
  }

  const destinationMap = new Map<number, SocialDestination>();
  for (const d of destinations) {
    destinationMap.set(d.id, d);
  }

  const deviceMap = new Map<number, PhoneDevice>();
  for (const dev of devices) {
    deviceMap.set(dev.id, dev);
  }

  return (
    <section className="card space-y-5" id="devices">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-divider-soft pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-text-primary">Phone Devices &amp; Automation</h2>
            <span className="badge badge-gray">{devices.length} registered</span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5">
            Registered Tailscale/ADB Android phones for stealth Facebook Profile and Page publishing.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => { setShowRegisterForm(!showRegisterForm); setDeviceError(null); }}
            className="btn-secondary text-xs py-1.5 px-3"
          >
            <Icon icon={showRegisterForm ? "solar:close-circle-bold-duotone" : "solar:add-circle-bold-duotone"} width={16} />
            {showRegisterForm ? "Cancel" : "Register Phone"}
          </button>
          <button
            type="button"
            onClick={() => { setShowAssignForm(!showAssignForm); setAssignError(null); }}
            className="btn-ghost text-xs py-1.5 px-3"
          >
            <Icon icon="solar:link-bold-duotone" width={16} />
            Draft Assignment
          </button>
        </div>
      </div>

      {/* Safety Notice: Tailscale reachability != Facebook Actor Identity */}
      <div className="rounded-lg border border-warning-main/30 bg-warning-lighter/20 p-3.5 flex items-start gap-3 text-xs text-text-secondary">
        <Icon icon="solar:shield-warning-bold-duotone" width={20} className="text-warning-main flex-shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-semibold text-text-primary">Identity &amp; Calibration Architecture Note</p>
          <p>
            Tailscale IP provides network reachability only and does not establish Facebook identity.
            Device identity and Facebook actor authorization are verified strictly through ADB/Mobilerun inspections
            before composing. App Card calibration must complete a dry-run before publishing can be activated.
          </p>
        </div>
      </div>

      {/* Dry-Run Feedback Banner */}
      {dryRunStatus && (
        <div
          className={`rounded-lg p-3 text-xs flex items-center justify-between border ${
            dryRunStatus.success
              ? "border-primary-main/40 bg-primary-lighter/30 text-primary-dark"
              : "border-error-main/40 bg-error-lighter/30 text-error-darker"
          }`}
        >
          <div className="flex items-center gap-2">
            <Icon
              icon={dryRunStatus.success ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
              width={16}
            />
            <span>{dryRunStatus.message}</span>
          </div>
          <button
            type="button"
            onClick={() => setDryRunStatus(null)}
            className="text-text-disabled hover:text-text-primary ml-2"
          >
            <Icon icon="solar:close-circle-bold" width={14} />
          </button>
        </div>
      )}

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

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Device Name *</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. Realme 12 Pro (Phone 1)"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="label">ADB Serial</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. 192.168.1.50:5555 or USB serial"
                value={formSerial}
                onChange={(e) => setFormSerial(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Tailscale IP</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. 100.90.12.34"
                value={formIp}
                onChange={(e) => setFormIp(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Tailscale Node ID</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. node-xyz123"
                value={formNodeId}
                onChange={(e) => setFormNodeId(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Expected Profile Actor ID</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. 100089123456789"
                value={formActorId}
                onChange={(e) => setFormActorId(e.target.value)}
              />
              <p className="text-[10px] text-text-secondary mt-0.5">Required before status can be marked Available.</p>
            </div>
            <div>
              <label className="label">Expected Profile Name</label>
              <input
                className="input-rect text-xs py-2"
                placeholder="e.g. John Doe"
                value={formActorName}
                onChange={(e) => setFormActorName(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Initial Status</label>
              <select
                className="input text-xs py-2"
                value={formStatus}
                onChange={(e) => setFormStatus(e.target.value as PhoneDeviceStatus)}
              >
                <option value="maintenance">Maintenance (Calibration pending)</option>
                <option value="available">Available (Requires expected Profile Actor ID)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowRegisterForm(false)}
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

      {/* Draft Device Assignment Form */}
      {showAssignForm && (
        <form onSubmit={handleDraftAssignment} className="card-sm bg-bg-paper-hover space-y-4 border border-divider-soft">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text-primary">Draft Device Assignment</h3>
            <span className="text-[11px] text-text-secondary">Unverified until host runner dry-run</span>
          </div>

          {assignError && (
            <div className="rounded-md border border-error-main/40 bg-error-lighter/30 p-2.5 text-xs text-error-darker">
              {assignError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="label">Destination *</label>
              <select
                className="input text-xs py-2"
                value={assignDestinationId}
                onChange={(e) => setAssignDestinationId(e.target.value ? Number(e.target.value) : "")}
                required
              >
                <option value="">Select destination…</option>
                {destinations.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.kind === "facebook_profile" ? "Profile" : "Page"} · {d.mode})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Device *</label>
              <select
                className="input text-xs py-2"
                value={assignDeviceId}
                onChange={(e) => setAssignDeviceId(e.target.value ? Number(e.target.value) : "")}
                required
              >
                <option value="">Select device…</option>
                {devices.map((dev) => (
                  <option key={dev.id} value={dev.id}>
                    {dev.name} ({dev.status})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Priority (0 = Primary, 1+ = Backup)</label>
              <input
                type="number"
                min={0}
                className="input-rect text-xs py-2"
                value={assignPriority}
                onChange={(e) => setAssignPriority(parseInt(e.target.value) || 0)}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowAssignForm(false)}
              className="btn-ghost text-xs py-1.5 px-3"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submittingAssign}
              className="btn-primary text-xs py-1.5 px-4"
            >
              {submittingAssign ? "Saving…" : "Save Assignment Draft"}
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
            const deviceAssignments = assignments.filter((a) => a.device_id === device.id);
            const isAvailable = device.status === "available";
            const isCalibrated = !!device.calibration_verified_at;

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
                        {isCalibrated ? (
                          <span className="badge badge-green" title={`Calibrated: ${device.calibration_verified_at}`}>
                            Calibrated
                          </span>
                        ) : (
                          <span className="badge badge-gray" title="Calibration pending via dry-run">
                            Uncalibrated
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-text-secondary">
                        {device.expected_profile_name ? (
                          <>Actor: <strong className="text-text-primary">{device.expected_profile_name}</strong> {device.expected_profile_actor_id && `(${device.expected_profile_actor_id})`}</>
                        ) : device.expected_profile_actor_id ? (
                          <>Actor ID: <strong className="text-text-primary">{device.expected_profile_actor_id}</strong></>
                        ) : (
                          <span className="text-warning-main">No expected Facebook actor assigned</span>
                        )}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={dryRunRunning[`${device.id}`]}
                      onClick={() => handleRunDryRun(device.id)}
                      className="btn-outline text-xs py-1 px-2.5 flex items-center gap-1.5"
                      title="Run dry-run inspection to calibrate device"
                    >
                      <Icon
                        icon={dryRunRunning[`${device.id}`] ? "solar:refresh-circle-bold-duotone" : "solar:play-bold-duotone"}
                        width={14}
                        className={dryRunRunning[`${device.id}`] ? "animate-spin text-primary-main" : ""}
                      />
                      <span>{dryRunRunning[`${device.id}`] ? "Calibrating…" : "Run Calibration"}</span>
                    </button>
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
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">App Card Version</span>
                    <span className="text-text-primary">{device.app_card_version || "—"}</span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">FB App Version</span>
                    <span className="text-text-primary">{device.facebook_app_version || "—"}</span>
                  </div>
                </div>

                {/* Device assignments */}
                <div className="pt-1">
                  <div className="text-[11px] font-semibold text-text-secondary uppercase mb-1.5">
                    Assigned Destinations ({deviceAssignments.length})
                  </div>
                  {deviceAssignments.length === 0 ? (
                    <p className="text-xs text-text-disabled italic">No destinations currently assigned to this device.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {deviceAssignments.map((a) => {
                        const dest = destinationMap.get(a.destination_id);
                        const isPrimary = a.priority === 0;
                        return (
                          <div
                            key={a.id}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-bg-paper-hover border border-divider-soft text-xs"
                          >
                            <span className="font-semibold text-text-primary">
                              {dest?.name || `Destination #${a.destination_id}`}
                            </span>
                            <span className={`text-[10px] font-bold px-1 rounded ${isPrimary ? "bg-primary-main/20 text-primary-dark" : "bg-grey-500/20 text-text-secondary"}`}>
                              {isPrimary ? "Primary" : `Backup (p${a.priority})`}
                            </span>
                            {a.verified_at ? (
                              <span title={`Verified: ${a.verified_at}`}>
                                <Icon icon="solar:verified-check-bold-duotone" width={14} className="text-primary-main" />
                              </span>
                            ) : (
                              <div className="flex items-center gap-1">
                                <span className="text-[10px] text-warning-main" title="Unverified draft until dry-run">
                                  (Draft)
                                </span>
                                <button
                                  type="button"
                                  disabled={dryRunRunning[`${device.id}-${a.destination_id}`]}
                                  onClick={() => handleRunDryRun(device.id, a.destination_id)}
                                  className="text-[10px] font-semibold text-primary-main hover:underline flex items-center gap-0.5 ml-0.5"
                                  title="Run dry-run to verify actor identity and assignment"
                                >
                                  <Icon
                                    icon={dryRunRunning[`${device.id}-${a.destination_id}`] ? "solar:refresh-circle-bold-duotone" : "solar:verified-check-bold-duotone"}
                                    width={12}
                                    className={dryRunRunning[`${device.id}-${a.destination_id}`] ? "animate-spin" : ""}
                                  />
                                  <span>{dryRunRunning[`${device.id}-${a.destination_id}`] ? "Verifying…" : "Verify"}</span>
                                </button>
                              </div>
                            )}
                            <button
                              type="button"
                              onClick={() => handleRemoveAssignment(a.destination_id, a.device_id)}
                              className="text-text-disabled hover:text-error-main ml-1"
                              title="Deactivate assignment"
                            >
                              <Icon icon="solar:close-circle-bold-duotone" width={14} />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
