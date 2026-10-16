"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  listPhoneDevices,
  listSocialDestinations,
  listDeviceAssignments,
  createDeviceAssignment,
  deleteDeviceAssignment,
  runDeviceDryRun,
} from "@/lib/api";
import type {
  PhoneDevice,
  SocialDestination,
  DestinationDeviceAssignment,
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

function isAssignmentFullyVerified(
  assignment: DestinationDeviceAssignment,
  dest?: SocialDestination,
  destMap?: Map<number, SocialDestination>
): boolean {
  if (!assignment.verified_at) return false;
  if (!dest) return false;

  const expectedTargetId = dest.facebook_actor_id?.trim() || null;
  if (!expectedTargetId || assignment.verified_target_actor_id !== expectedTargetId) {
    return false;
  }

  if (dest.kind === "facebook_profile") {
    return assignment.verified_profile_actor_id === expectedTargetId;
  }

  if (dest.kind === "facebook_page") {
    if (!dest.parent_profile_id || !destMap) return false;
    const parent = destMap.get(dest.parent_profile_id);
    if (!parent || parent.kind !== "facebook_profile") return false;
    const expectedRootId = parent.facebook_actor_id?.trim() || null;
    if (!expectedRootId) return false;
    return assignment.verified_profile_actor_id === expectedRootId;
  }

  return false;
}

export interface AccountDevicesCardProps {
  destinations?: SocialDestination[];
  devices?: PhoneDevice[];
  assignments?: DestinationDeviceAssignment[];
  mutateAssignments?: () => Promise<unknown> | void;
  mutateDestinations?: () => Promise<unknown> | void;
  mutateDevices?: () => Promise<unknown> | void;
}

export function AccountDevicesCard({
  destinations: propDestinations,
  devices: propDevices,
  assignments: propAssignments,
  mutateAssignments: propMutateAssignments,
  mutateDestinations: propMutateDestinations,
  mutateDevices: propMutateDevices,
}: AccountDevicesCardProps) {
  const {
    data: swrDestinations = [],
    mutate: swrMutateDestinations,
  } = useSWR(
    propDestinations ? null : "social-destinations",
    () => listSocialDestinations().then((res) => res.data)
  );

  const {
    data: swrDevices = [],
    mutate: swrMutateDevices,
  } = useSWR(
    propDevices ? null : "phone-devices",
    () => listPhoneDevices().then((res) => res.data)
  );

  const {
    data: swrAssignments = [],
    mutate: swrMutateAssignments,
    isLoading: loadingAssignments,
  } = useSWR(
    propAssignments ? null : "device-assignments-all",
    () => listDeviceAssignments({ is_active: true }).then((res) => res.data)
  );

  const destinations = propDestinations ?? swrDestinations;
  const devices = propDevices ?? swrDevices;
  const assignments = propAssignments ?? swrAssignments;

  const refreshAll = async () => {
    await Promise.all([
      propMutateDestinations ? propMutateDestinations() : swrMutateDestinations(),
      propMutateDevices ? propMutateDevices() : swrMutateDevices(),
      propMutateAssignments ? propMutateAssignments() : swrMutateAssignments(),
    ]);
  };

  const [showAssignForm, setShowAssignForm] = useState(false);
  const [assignDestinationId, setAssignDestinationId] = useState<number | "">("");
  const [assignDeviceId, setAssignDeviceId] = useState<number | "">("");
  const [assignPriority, setAssignPriority] = useState<number>(0);
  const [submittingAssign, setSubmittingAssign] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);

  const [verifyingKey, setVerifyingKey] = useState<string | null>(null);
  const [verifyStatus, setVerifyStatus] = useState<{
    id: string;
    success: boolean;
    message: string;
  } | null>(null);

  const destinationMap = new Map<number, SocialDestination>();
  for (const d of destinations) {
    destinationMap.set(d.id, d);
  }

  const deviceMap = new Map<number, PhoneDevice>();
  for (const dev of devices) {
    deviceMap.set(dev.id, dev);
  }

  async function handleDraftAssignment(e: React.FormEvent) {
    e.preventDefault();
    setAssignError(null);
    if (assignDestinationId === "" || assignDeviceId === "") {
      setAssignError("Select both a social account destination and a phone device.");
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
      await refreshAll();
    } catch (err) {
      setAssignError(getErrorMessage(err));
    } finally {
      setSubmittingAssign(false);
    }
  }

  async function handleRemoveAssignment(destinationId: number, deviceId: number) {
    if (!confirm("Deactivate this account-to-phone assignment draft?")) return;
    try {
      await deleteDeviceAssignment(destinationId, deviceId);
      await refreshAll();
    } catch (err) {
      alert(getErrorMessage(err));
    }
  }

  async function handleVerifyAccount(deviceId: number, destinationId: number) {
    const key = `${deviceId}-${destinationId}`;
    setVerifyingKey(key);
    setVerifyStatus(null);
    try {
      const res = await runDeviceDryRun(deviceId, { destination_id: destinationId });
      const verified = res.data?.assignment_verified === true;
      setVerifyStatus({
        id: key,
        success: verified,
        message: verified
          ? "Account-to-phone assignment verified successfully via dry-run."
          : "Account dry-run completed with warnings or missing verification proof.",
      });
      await refreshAll();
    } catch (err) {
      setVerifyStatus({
        id: key,
        success: false,
        message: getErrorMessage(err),
      });
    } finally {
      setVerifyingKey(null);
    }
  }

  return (
    <section className="card space-y-5" id="account-devices">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-divider-soft pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-text-primary">Account-to-Phone Assignments</h2>
            <span className="badge badge-gray">{assignments.length} assigned</span>
          </div>
          <p className="text-xs text-text-secondary mt-0.5">
            Connect Facebook Profiles and Pages to dedicated phone runner devices for physical mobile automation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setShowAssignForm(!showAssignForm);
              setAssignError(null);
            }}
            className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1.5"
          >
            <Icon icon={showAssignForm ? "solar:close-circle-bold-duotone" : "solar:link-bold-duotone"} width={16} />
            {showAssignForm ? "Cancel" : "Draft Assignment"}
          </button>
          <Link
            href="/settings#devices"
            className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
          >
            <Icon icon="solar:smartphone-bold-duotone" width={16} />
            Manage Phones ({devices.length})
          </Link>
        </div>
      </div>

      <div className="rounded-lg border border-primary-main/20 bg-primary-lighter/10 p-3.5 flex items-start gap-3 text-xs text-text-secondary">
        <Icon icon="solar:shield-check-bold-duotone" width={20} className="text-primary-main flex-shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-semibold text-text-primary">Account Verification</p>
          <p>
            Facebook Profiles and Pages publish through designated phones. Run Account Verification (dry-run) to
            confirm Facebook identity on the phone before enabling phone publishing. Pages may still publish via Repliz.
          </p>
        </div>
      </div>

      {verifyStatus && (
        <div
          className={`rounded-lg p-3 text-xs flex items-center justify-between border ${
            verifyStatus.success
              ? "border-primary-main/40 bg-primary-lighter/30 text-primary-dark"
              : "border-error-main/40 bg-error-lighter/30 text-error-darker"
          }`}
        >
          <div className="flex items-center gap-2">
            <Icon
              icon={verifyStatus.success ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
              width={16}
            />
            <span>{verifyStatus.message}</span>
          </div>
          <button
            type="button"
            onClick={() => setVerifyStatus(null)}
            className="text-text-disabled hover:text-text-primary ml-2"
          >
            <Icon icon="solar:close-circle-bold" width={14} />
          </button>
        </div>
      )}

      {showAssignForm && (
        <form onSubmit={handleDraftAssignment} className="card-sm bg-bg-paper-hover space-y-4 border border-divider-soft">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text-primary">Draft Account-to-Phone Assignment</h3>
            <span className="text-[11px] text-text-secondary">Unverified until account dry-run</span>
          </div>

          {assignError && (
            <div className="rounded-md border border-error-main/40 bg-error-lighter/30 p-2.5 text-xs text-error-darker">
              {assignError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="assign-destination-select" className="label">Account Destination *</label>
              <select
                id="assign-destination-select"
                className="input text-xs py-2"
                value={assignDestinationId}
                onChange={(e) => setAssignDestinationId(e.target.value ? Number(e.target.value) : "")}
                required
              >
                <option value="">Select destination…</option>
                {destinations.map((d) => {
                  const parent = d.parent_profile_id ? destinationMap.get(d.parent_profile_id) : null;
                  const kindLabel = d.kind === "facebook_profile" ? "Profile" : "Page";
                  const parentNote = parent ? ` · via ${parent.name}` : "";
                  return (
                    <option key={d.id} value={d.id}>
                      {d.name} ({kindLabel}{parentNote} · {d.mode})
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label htmlFor="assign-device-select" className="label">Phone Device *</label>
              <select
                id="assign-device-select"
                className="input text-xs py-2"
                value={assignDeviceId}
                onChange={(e) => setAssignDeviceId(e.target.value ? Number(e.target.value) : "")}
                required
              >
                <option value="">Select phone…</option>
                {devices.map((dev) => (
                  <option key={dev.id} value={dev.id}>
                    {dev.name} ({dev.status})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="assign-priority-input" className="label">Priority (0 = Primary, 1+ = Backup)</label>
              <input
                id="assign-priority-input"
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

      {loadingAssignments ? (
        <div className="py-6 text-center text-xs text-text-secondary">Loading account assignments…</div>
      ) : assignments.length === 0 ? (
        <div className="py-8 text-center rounded-lg border border-dashed border-divider-soft space-y-2">
          <Icon icon="solar:link-broken-bold-duotone" width={32} className="mx-auto text-text-disabled" />
          <p className="text-sm font-semibold text-text-primary">No Account Assignments Drafted</p>
          <p className="text-xs text-text-secondary max-w-sm mx-auto">
            Connect your Facebook Profiles and Pages to a registered phone to prepare automated publishing.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {assignments.map((assignment) => {
            const dest = destinationMap.get(assignment.destination_id);
            const dev = deviceMap.get(assignment.device_id);
            const isPrimary = assignment.priority === 0;
            const parent = dest?.parent_profile_id ? destinationMap.get(dest.parent_profile_id) : null;
            const isVerified = isAssignmentFullyVerified(assignment, dest, destinationMap);
            const key = `${assignment.device_id}-${assignment.destination_id}`;
            const isVerifying = verifyingKey === key;
            const isAnyVerifying = verifyingKey !== null;

            return (
              <div
                key={assignment.id}
                className="rounded-lg border border-divider-soft bg-bg-paper p-4 space-y-3 hover:border-text-disabled transition-colors"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary-lighter/40 text-primary-darker flex items-center justify-center flex-shrink-0">
                      <Icon icon={dest?.kind === "facebook_profile" ? "solar:user-bold-duotone" : "solar:flag-bold-duotone"} width={18} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-text-primary">
                          {dest?.name || `Destination #${assignment.destination_id}`}
                        </span>
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${isPrimary ? "bg-primary-main/20 text-primary-dark" : "bg-grey-500/20 text-text-secondary"}`}>
                          {isPrimary ? "Primary" : `Backup (p${assignment.priority})`}
                        </span>
                        {isVerified ? (
                          <span className="badge badge-green flex items-center gap-1" title={`Verified at: ${assignment.verified_at}`}>
                            <Icon icon="solar:verified-check-bold" width={12} />
                            Verified
                          </span>
                        ) : (
                          <span className="badge badge-yellow" title="Unverified draft until account dry-run and actor IDs match">
                            Draft (Unverified)
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-text-secondary mt-0.5">
                        {dest?.kind === "facebook_profile" ? "Facebook Profile" : "Facebook Page"}
                        {parent && <span> · Parent Profile: <strong className="text-text-primary">{parent.name}</strong></span>}
                        {dest?.mode && <span> · Mode: <span className="font-mono">{dest.mode}</span></span>}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={isAnyVerifying}
                      onClick={() => handleVerifyAccount(assignment.device_id, assignment.destination_id)}
                      className="btn-outline text-xs py-1 px-2.5 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Run dry-run to verify Facebook Actor authorization on phone"
                    >
                      <Icon
                        icon={isVerifying ? "solar:refresh-circle-bold-duotone" : "solar:play-bold-duotone"}
                        width={14}
                        className={isVerifying ? "animate-spin text-primary-main" : ""}
                      />
                      <span>{isVerifying ? "Verifying…" : "Verify Account"}</span>
                    </button>
                    <button
                      type="button"
                      disabled={isAnyVerifying}
                      onClick={() => handleRemoveAssignment(assignment.destination_id, assignment.device_id)}
                      className="btn-ghost text-xs py-1 px-2.5 text-text-disabled hover:text-error-main disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Deactivate assignment draft"
                    >
                      <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                      <span>Deactivate</span>
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] bg-bg-paper-hover p-2.5 rounded-md font-mono text-text-secondary">
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Assigned Phone</span>
                    <span className="text-text-primary font-sans font-medium">{dev?.name || `Device #${assignment.device_id}`}</span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Phone Status</span>
                    <span className="text-text-primary font-sans">{dev?.status || "—"}</span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">Tailscale IP</span>
                    <span className="text-text-primary">{dev?.tailscale_ip || "—"}</span>
                  </div>
                  <div>
                    <span className="text-text-disabled block font-sans text-[10px] uppercase font-semibold">ADB Serial</span>
                    <span className="text-text-primary truncate block" title={dev?.adb_serial || ""}>{dev?.adb_serial || "—"}</span>
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
