"use client";

import React, { useState } from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type {
  SocialDestination,
  PhoneDevice,
  DestinationDeviceAssignment,
} from "@/lib/types";
import { runDeviceDryRun, createDeviceAssignment, deleteDeviceAssignment } from "@/lib/api";
import { getErrorMessage } from "./channelUtils";

interface Props {
  destination: SocialDestination;
  profiles: SocialDestination[];
  devices: PhoneDevice[];
  assignments: DestinationDeviceAssignment[];
  parentProfileId: number | null;
  setParentProfileId: (id: number | null) => void;
  facebookActorId: string;
  setFacebookActorId: (id: string) => void;
  onRefreshData: () => void;
  onError: (msg: string) => void;
}

export function PhoneSettingsBlock({
  destination,
  profiles,
  devices,
  assignments,
  parentProfileId,
  setParentProfileId,
  facebookActorId,
  setFacebookActorId,
  onRefreshData,
  onError,
}: Props) {
  const isProfile = destination.kind === "facebook_profile";
  const [selectedDeviceIdToAssign, setSelectedDeviceIdToAssign] = useState<string>("");
  const [isDryRunning, setIsDryRunning] = useState(false);
  const [dryRunMessage, setDryRunMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const activeAssignments = assignments.filter((a) => a.is_active);
  const assignedDeviceIds = new Set(activeAssignments.map((a) => a.device_id));
  const assignedDevices = devices.filter((d) => assignedDeviceIds.has(d.id));
  const unassignedDevices = devices.filter((d) => !assignedDeviceIds.has(d.id));
  const primaryAssignedDevice = assignedDevices[0] || null;

  const parentProfile = profiles.find((p) => p.id === parentProfileId);
  const profileIdentityVerified = isProfile
    ? Boolean(destination.identity_verified_at)
    : Boolean(destination.identity_verified_at || parentProfile?.identity_verified_at);

  const pageIdentityVerified = isProfile
    ? true
    : Boolean(
        destination.identity_verified_at ||
          activeAssignments.some(
            (a) =>
              a.verified_target_actor_id &&
              a.verified_target_actor_id === facebookActorId.trim()
          )
      );

  const appCardPassed = Boolean(primaryAssignedDevice?.calibration_verified_at);

  const isPhoneOnline = Boolean(
    primaryAssignedDevice &&
      (primaryAssignedDevice.status === "available" ||
        (primaryAssignedDevice.last_seen_at &&
          (Date.now() - new Date(primaryAssignedDevice.last_seen_at).getTime()) / (1000 * 60) <= 10))
  );

  const handleRunDryRun = async () => {
    if (!primaryAssignedDevice) {
      setDryRunMessage({ type: "error", text: "No assigned phone device found to run dry-run." });
      return;
    }
    setIsDryRunning(true);
    setDryRunMessage(null);
    try {
      const res = await runDeviceDryRun(primaryAssignedDevice.id, {
        destination_id: destination.id,
      });
      const resultData = res.data;
      if (resultData.status === "success") {
        setDryRunMessage({
          type: "success",
          text: `App Card dry-run passed! Calibration verified at ${new Date().toLocaleTimeString()}.`,
        });
      } else {
        setDryRunMessage({
          type: "error",
          text: `Dry-run reported status: ${resultData.status}. Review phone logs.`,
        });
      }
      onRefreshData();
    } catch (err) {
      setDryRunMessage({
        type: "error",
        text: `Dry-run failed: ${getErrorMessage(err)}`,
      });
    } finally {
      setIsDryRunning(false);
    }
  };

  const handleAssignDevice = async () => {
    const devId = parseInt(selectedDeviceIdToAssign, 10);
    if (!devId) return;
    try {
      await createDeviceAssignment({
        destination_id: destination.id,
        device_id: devId,
        is_active: true,
      });
      setSelectedDeviceIdToAssign("");
      onRefreshData();
    } catch (err) {
      onError(getErrorMessage(err));
    }
  };

  const handleUnassignDevice = async (deviceId: number) => {
    try {
      await deleteDeviceAssignment(destination.id, deviceId);
      onRefreshData();
    } catch (err) {
      onError(getErrorMessage(err));
    }
  };

  return (
    <section className="card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-text flex items-center gap-2">
          <Icon icon="solar:smartphone-bold" className="text-info" width={16} />
          Phone Settings &amp; Verification
        </h3>
        <Link
          href="/brands/accounts"
          className="text-xs font-semibold text-pri hover:opacity-90 transition"
        >
          Manage profiles and phones →
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {!isProfile && (
          <div>
            <label className="label">Managing Facebook Profile</label>
            <select
              value={parentProfileId ?? ""}
              onChange={(e) => setParentProfileId(e.target.value ? Number(e.target.value) : null)}
              className="input-rect"
            >
              <option value="">Select profile...</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} {p.facebook_actor_id ? `(${p.facebook_actor_id})` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label className="label">
            {isProfile ? "Profile ID as seen on phone" : "Page ID as seen on the phone"}
          </label>
          <input
            type="text"
            value={facebookActorId}
            onChange={(e) => setFacebookActorId(e.target.value)}
            placeholder="e.g. 100094901131361"
            className="input-rect font-mono text-xs"
          />
        </div>

        <div>
          <label className="label">Assigned Phone Device</label>
          {primaryAssignedDevice ? (
            <div className="h-[42px] px-3 rounded-md bg-surface2 border border-line flex items-center justify-between text-xs">
              <span className="flex items-center gap-2 font-medium text-text">
                <span
                  className={`w-2 h-2 rounded-full ${
                    isPhoneOnline ? "bg-emerald-500" : "bg-rose-500"
                  }`}
                />
                {primaryAssignedDevice.name}
              </span>
              <button
                type="button"
                onClick={() => handleUnassignDevice(primaryAssignedDevice.id)}
                className="text-text2 hover:text-bad transition"
                title="Remove assignment"
              >
                <Icon icon="solar:trash-bin-trash-bold" width={14} />
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <select
                value={selectedDeviceIdToAssign}
                onChange={(e) => setSelectedDeviceIdToAssign(e.target.value)}
                className="input-rect text-xs py-2"
              >
                <option value="">Pick phone...</option>
                {unassignedDevices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.status})
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={handleAssignDevice}
                disabled={!selectedDeviceIdToAssign}
                className="btn-secondary text-xs px-3 py-1 flex-shrink-0"
              >
                Assign
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Checklist */}
      <div className="p-4 rounded-xl bg-surface2 border border-line space-y-3">
        <span className="text-xs font-semibold text-text">
          Before the phone may post:
        </span>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <li className="flex items-center gap-2">
            <span
              className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                profileIdentityVerified
                  ? "bg-pri-soft text-pri-text border border-pri/30"
                  : "bg-surface text-text2 border border-line"
              }`}
            >
              {profileIdentityVerified ? "✓" : "○"}
            </span>
            <span className={profileIdentityVerified ? "text-text" : "text-text2"}>
              Profile identity verified on phone
            </span>
          </li>

          <li className="flex items-center gap-2">
            <span
              className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                pageIdentityVerified
                  ? "bg-pri-soft text-pri-text border border-pri/30"
                  : "bg-surface text-text2 border border-line"
              }`}
            >
              {pageIdentityVerified ? "✓" : "○"}
            </span>
            <span className={pageIdentityVerified ? "text-text" : "text-text2"}>
              Page identity verified (switch works)
            </span>
          </li>

          <li className="flex items-center gap-2">
            <span
              className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                appCardPassed
                  ? "bg-pri-soft text-pri-text border border-pri/30"
                  : "bg-surface text-text2 border border-line"
              }`}
            >
              {appCardPassed ? "✓" : "○"}
            </span>
            <span className={appCardPassed ? "text-text" : "text-text2"}>
              App Card dry-run passed
            </span>
          </li>

          <li className="flex items-center gap-2">
            <span
              className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                isPhoneOnline
                  ? "bg-pri-soft text-pri-text border border-pri/30"
                  : "bg-surface text-text2 border border-line"
              }`}
            >
              {isPhoneOnline ? "✓" : "○"}
            </span>
            <span className={isPhoneOnline ? "text-text" : "text-text2"}>
              Phone online over Tailscale
            </span>
          </li>
        </ul>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <button
            type="button"
            onClick={handleRunDryRun}
            disabled={isDryRunning || !primaryAssignedDevice}
            className="btn-ghost text-xs px-3 py-1.5 flex items-center gap-1.5"
          >
            <Icon
              icon="solar:refresh-circle-bold-duotone"
              className={isDryRunning ? "animate-spin" : ""}
              width={15}
            />
            {isDryRunning ? "Running dry-run..." : "Run dry-run again"}
          </button>

          {dryRunMessage && (
            <span
              className={`text-xs font-medium ${
                dryRunMessage.type === "success" ? "text-pri" : "text-bad"
              }`}
            >
              {dryRunMessage.text}
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
