"use client";

import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { SocialTabs } from "@/components/social-media/SocialTabs";
import { AccountDevicesCard } from "@/components/social-media/AccountDevicesCard";
import {
  listSocialDestinations,
  listContentProjects,
  listPhoneDevices,
  listDeviceAssignments,
} from "@/lib/api";
import { listReplizAccounts } from "@/lib/socialBrandsApi";
import type {
  SocialDestination,
  ContentProject,
  PhoneDevice,
  DestinationDeviceAssignment,
} from "@/lib/types";
import type { ReplizAccountsOut } from "@/lib/socialBrandsApi";

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

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return "never";
  try {
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return dateString;
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diffSec < 60) return "just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}h ago`;
    const diffDay = Math.floor(diffHour / 24);
    return `${diffDay}d ago`;
  } catch {
    return dateString;
  }
}

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export default function SocialMediaAccountsPage() {
  const {
    data: destinations = [],
    isLoading: loadingDestinations,
    error: destinationsError,
    mutate: mutateDestinations,
  } = useSWR<SocialDestination[]>("social-destinations", () =>
    listSocialDestinations().then((r) => r.data)
  );

  const { data: projects = [] } = useSWR<ContentProject[]>(
    "content-projects",
    () => listContentProjects().then((r) => r.data)
  );

  const {
    data: devices = [],
    isLoading: loadingDevices,
    error: devicesError,
    mutate: mutateDevices,
  } = useSWR<PhoneDevice[]>("phone-devices", () =>
    listPhoneDevices().then((r) => r.data)
  );

  const {
    data: assignments = [],
    mutate: mutateAssignments,
  } = useSWR<DestinationDeviceAssignment[]>("device-assignments-all", () =>
    listDeviceAssignments({ is_active: true }).then((r) => r.data)
  );

  const {
    data: replizData,
    isLoading: loadingRepliz,
    error: replizFetchError,
    mutate: mutateRepliz,
  } = useSWR<ReplizAccountsOut>("repliz-accounts", listReplizAccounts);

  const projectMap = new Map<number, ContentProject>(projects.map((p) => [p.id, p]));
  const deviceMap = new Map<number, PhoneDevice>(devices.map((d) => [d.id, d]));

  const profiles = destinations.filter((d) => d.kind === "facebook_profile");
  const replizAccounts = replizData?.accounts || [];

  return (
    <div className="max-w-6xl space-y-6">
      {/* ── Top Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">
            Social Media — Publishing accounts
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Brands decide where posts go. Accounts and devices decide how they get out.
          </p>
        </div>
      </div>

      {/* ── Tabs Navigation ── */}
      <SocialTabs
        accounts={profiles.length + replizAccounts.length}
        devices={devices.length}
      />

      {/* ── Account Types Overview ── */}
      <section
        aria-label="Account types"
        className="grid grid-cols-1 md:grid-cols-3 gap-4"
      >
        <div className="card p-4 space-y-1.5">
          <span className="text-sm font-bold text-text-primary">Facebook Profiles</span>
          <p className="text-xs text-text-secondary leading-relaxed">
            A person’s account signed in on a phone. Posts to its own timeline and to the Pages
            it manages.
          </p>
        </div>
        <div className="card p-4 space-y-1.5">
          <span className="text-sm font-bold text-text-primary">Repliz connections</span>
          <p className="text-xs text-text-secondary leading-relaxed">
            Scheduler accounts that post to Pages and Instagram without a phone.
          </p>
        </div>
        <div className="card p-4 space-y-1.5 border border-dashed border-divider-soft opacity-80">
          <span className="text-sm font-bold text-text-secondary">YouTube, X, Threads</span>
          <p className="text-xs text-text-secondary leading-relaxed">
            Account connections for these platforms are not built yet. Channels can be planned on
            a brand in the meantime.
          </p>
        </div>
      </section>

      {/* ── Section: Facebook Profiles ── */}
      <section aria-labelledby="profiles-heading" className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 id="profiles-heading" className="text-base font-bold text-text-primary">
              Facebook Profiles ({profiles.length})
            </h2>
            <p className="text-xs text-text-secondary mt-0.5">
              A person’s account signed in on a phone. Posts to its own timeline and to the Pages it
              manages.
            </p>
          </div>
        </div>

        {/* Profiles Loading / Error */}
        {loadingDestinations && (
          <div className="card p-8 text-center text-xs text-text-secondary flex flex-col items-center gap-2">
            <Icon icon="solar:refresh-bold-duotone" width={22} className="animate-spin text-primary-main" />
            <span>Loading Facebook Profiles…</span>
          </div>
        )}

        {destinationsError && !loadingDestinations && (
          <div className="card p-6 text-center space-y-2 border-error-main/40 bg-error-lighter/20">
            <Icon icon="solar:danger-triangle-bold-duotone" width={32} className="mx-auto text-error-main" />
            <p className="text-xs font-semibold text-text-primary">Could not load Facebook Profiles</p>
            <p className="text-xs text-text-secondary">{getErrorMessage(destinationsError)}</p>
            <button
              type="button"
              onClick={() => mutateDestinations()}
              className="btn-secondary text-xs py-1 px-3 inline-flex items-center gap-1"
            >
              <Icon icon="solar:refresh-bold-duotone" width={14} />
              Retry
            </button>
          </div>
        )}

        {!loadingDestinations && !destinationsError && profiles.length === 0 && (
          <div className="card py-8 text-center text-xs text-text-secondary">
            No Facebook Profiles registered yet. Add a profile destination from the classic view or channels page.
          </div>
        )}

        {!loadingDestinations &&
          !destinationsError &&
          profiles.map((profile) => {
            const childPages = destinations.filter(
              (d) => d.kind === "facebook_page" && d.parent_profile_id === profile.id
            );
            const isVerified = Boolean(profile.identity_verified_at);

            // Assigned devices for this profile
            const profileAssignments = assignments.filter(
              (a) => a.destination_id === profile.id && a.is_active
            );
            const assignedDevices = profileAssignments
              .map((a) => deviceMap.get(a.device_id))
              .filter((dev): dev is PhoneDevice => Boolean(dev));

            const totalPhonePosts =
              profile.phone_daily_target +
              childPages.reduce((acc, p) => acc + (p.phone_daily_target || 0), 0);

            return (
              <article
                key={profile.id}
                className="card p-6 space-y-5 hover:border-text-disabled transition-colors shadow-sm"
              >
                {/* Profile Top Row */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-divider-soft pb-4">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 rounded-full bg-primary-lighter/40 text-primary-dark flex items-center justify-center flex-shrink-0">
                      <Icon icon="solar:user-bold-duotone" width={24} />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center gap-2.5 flex-wrap">
                        <h3 className="text-lg font-bold text-text-primary">{profile.name}</h3>
                        <span className="badge badge-blue">Facebook Profile</span>
                        {isVerified ? (
                          <span
                            className="badge badge-green"
                            title={`Identity verified at: ${profile.identity_verified_at}`}
                          >
                            Identity verified
                          </span>
                        ) : (
                          <span className="badge badge-yellow" title="Requires dry-run calibration">
                            Not verified
                          </span>
                        )}
                        <span className={`badge ${profile.is_active ? "badge-green" : "badge-gray"}`}>
                          {profile.is_active ? "Active" : "Inactive"}
                        </span>
                      </div>
                      <p className="text-xs text-text-secondary">
                        Actor ID {profile.facebook_actor_id || "Not assigned"} · Time zone{" "}
                        {profile.timezone}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 self-start sm:self-auto">
                    <Link
                      href={`/social-media/channels/${profile.id}`}
                      className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
                    >
                      <Icon icon="solar:pen-bold-duotone" width={14} />
                      <span>Edit</span>
                    </Link>
                  </div>
                </div>

                {/* Profile Stats Grid: Signed in on, Own timeline, Phone work */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {/* Signed in on */}
                  <div className="p-3.5 rounded-xl bg-bg-default border border-divider-soft space-y-1.5">
                    <span className="text-[11px] font-medium text-text-secondary block">
                      Signed in on
                    </span>
                    {assignedDevices.length > 0 ? (
                      assignedDevices.map((dev) => (
                        <div key={dev.id} className="space-y-1">
                          <span className="text-sm font-bold text-text-primary block">{dev.name}</span>
                          <div className="flex items-center gap-1.5 text-xs">
                            <span
                              className={`w-2 h-2 rounded-full ${
                                dev.status === "available" ? "bg-emerald-500" : "bg-grey-500"
                              }`}
                            />
                            <span
                              className={
                                dev.status === "available" ? "text-emerald-400 font-medium" : "text-text-secondary"
                              }
                            >
                              {dev.status === "available" ? "Online" : dev.status}
                              {dev.last_seen_at ? ` · seen ${formatRelativeTime(dev.last_seen_at)}` : ""}
                            </span>
                          </div>
                        </div>
                      ))
                    ) : (
                      <span className="text-xs text-text-disabled italic">No phone assigned</span>
                    )}
                  </div>

                  {/* Own timeline */}
                  <div className="p-3.5 rounded-xl bg-bg-default border border-divider-soft space-y-1.5">
                    <span className="text-[11px] font-medium text-text-secondary block">
                      Own timeline
                    </span>
                    <span className="text-sm font-bold text-text-primary block">
                      Channel of “{projectMap.get(profile.content_project_id)?.name || "—"}”
                    </span>
                    <span className="text-xs text-text-secondary block">
                      {profile.daily_target} / day · {profile.min_gap_minutes} min gap
                    </span>
                  </div>

                  {/* Phone work today */}
                  <div className="p-3.5 rounded-xl bg-bg-default border border-divider-soft space-y-1.5">
                    <span className="text-[11px] font-medium text-text-secondary block">
                      Phone work today
                    </span>
                    <span className="text-sm font-bold text-text-primary block">
                      {totalPhonePosts} posts planned
                    </span>
                    <span className="text-xs text-text-secondary block">
                      Own timeline + {childPages.length} {childPages.length === 1 ? "Page" : "Pages"}
                    </span>
                  </div>
                </div>

                {/* Pages this profile manages */}
                <div className="space-y-2.5 pt-1">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-bold uppercase tracking-wider text-text-primary">
                      Pages this profile manages ({childPages.length})
                    </h4>
                  </div>

                  {childPages.length === 0 ? (
                    <div className="p-4 rounded-xl bg-bg-default border border-divider-soft text-xs text-text-disabled italic">
                      No managed pages under this profile.
                    </div>
                  ) : (
                    <div className="overflow-x-auto rounded-xl border border-divider-soft">
                      <table className="w-full text-xs text-left min-w-[640px]">
                        <thead>
                          <tr className="bg-bg-default text-text-secondary border-b border-divider-soft font-semibold">
                            <th scope="col" className="py-2.5 px-4">
                              Page
                            </th>
                            <th scope="col" className="py-2.5 px-4">
                              Brand
                            </th>
                            <th scope="col" className="py-2.5 px-4">
                              Page ID on the phone
                            </th>
                            <th scope="col" className="py-2.5 px-4">
                              Identity check
                            </th>
                            <th scope="col" className="py-2.5 px-4">
                              Phone posts / day
                            </th>
                            <th scope="col" className="py-2.5 px-4 text-right">
                              Action
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-divider-soft bg-bg-paper">
                          {childPages.map((page) => {
                            const pageVerified = Boolean(page.identity_verified_at);
                            return (
                              <tr key={page.id} className="hover:bg-bg-paper-hover transition-colors">
                                <td className="py-3 px-4 font-semibold text-text-primary">
                                  {page.name}
                                </td>
                                <td className="py-3 px-4 text-text-secondary">
                                  <Link
                                    href={`/content-projects`}
                                    className="text-primary-main hover:underline"
                                  >
                                    {projectMap.get(page.content_project_id)?.name || "—"}
                                  </Link>
                                </td>
                                <td className="py-3 px-4 font-mono text-text-primary">
                                  {page.facebook_actor_id || "—"}
                                </td>
                                <td className="py-3 px-4">
                                  {pageVerified ? (
                                    <span className="badge badge-green">Verified</span>
                                  ) : (
                                    <span className="badge badge-yellow">Not verified</span>
                                  )}
                                </td>
                                <td className="py-3 px-4 text-text-primary font-medium">
                                  {page.phone_daily_target} / day
                                </td>
                                <td className="py-3 px-4 text-right">
                                  <Link
                                    href={`/social-media/channels/${page.id}`}
                                    className="text-primary-main hover:underline font-medium inline-flex items-center gap-1"
                                  >
                                    Edit
                                  </Link>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <p className="text-[11px] text-text-secondary">
                    A Page route on the phone stays off until the identity check and the App Card
                    dry-run both pass.
                  </p>
                </div>
              </article>
            );
          })}
      </section>

      {/* ── Section: Repliz connections ── */}
      <section aria-labelledby="repliz-heading" className="space-y-4 pt-2">
        <div className="flex items-center justify-between">
          <div>
            <h2 id="repliz-heading" className="text-base font-bold text-text-primary">
              Repliz connections ({replizAccounts.length})
            </h2>
            <p className="text-xs text-text-secondary mt-0.5">
              Scheduler accounts that post to Pages and Instagram without a phone.
            </p>
          </div>
          <Link
            href="/settings"
            className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
          >
            <Icon icon="solar:settings-bold-duotone" width={14} />
            <span>Repliz Settings</span>
          </Link>
        </div>

        {/* Loading state */}
        {loadingRepliz && (
          <div className="card p-6 text-center text-xs text-text-secondary flex flex-col items-center gap-2">
            <Icon icon="solar:refresh-bold-duotone" width={22} className="animate-spin text-primary-main" />
            <span>Loading Repliz connections…</span>
          </div>
        )}

        {/* Fetch error */}
        {replizFetchError && !loadingRepliz && (
          <div className="card p-6 text-center space-y-2 border-error-main/40 bg-error-lighter/20">
            <Icon icon="solar:danger-triangle-bold-duotone" width={28} className="mx-auto text-error-main" />
            <p className="text-xs font-semibold text-text-primary">Could not load Repliz accounts</p>
            <p className="text-xs text-text-secondary">{getErrorMessage(replizFetchError)}</p>
            <button
              type="button"
              onClick={() => mutateRepliz()}
              className="btn-secondary text-xs py-1 px-3 inline-flex items-center gap-1"
            >
              <Icon icon="solar:refresh-bold-duotone" width={14} />
              Retry
            </button>
          </div>
        )}

        {/* Repliz not configured */}
        {!loadingRepliz && replizData?.configured === false && (
          <div className="card p-6 text-center space-y-2 border-divider-soft">
            <Icon icon="solar:link-broken-bold-duotone" width={32} className="mx-auto text-text-disabled" />
            <p className="text-sm font-semibold text-text-primary">Repliz is not connected.</p>
            <p className="text-xs text-text-secondary">
              Add the keys in Settings to connect your Repliz scheduling account.
            </p>
            <div className="pt-2">
              <Link href="/settings" className="btn-primary text-xs py-1.5 px-4 inline-flex items-center gap-1.5">
                <Icon icon="solar:settings-bold-duotone" width={14} />
                <span>Go to Settings</span>
              </Link>
            </div>
          </div>
        )}

        {/* Repliz server warning error */}
        {!loadingRepliz && replizData?.error && (
          <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 flex items-start gap-3 text-xs text-amber-300">
            <Icon icon="solar:danger-triangle-bold-duotone" width={18} className="text-amber-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Repliz Warning</p>
              <p>{replizData.error}</p>
            </div>
          </div>
        )}

        {/* Repliz Accounts List */}
        {!loadingRepliz &&
          replizData?.configured !== false &&
          replizAccounts.length > 0 && (
            <div className="card divide-y divide-divider-soft overflow-hidden">
              {replizAccounts.map((acc) => (
                <div
                  key={acc.id}
                  className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 hover:bg-bg-paper-hover transition-colors"
                >
                  <div className="flex items-center gap-3">
                    {acc.picture ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img
                        src={acc.picture}
                        alt={acc.name}
                        className="w-9 h-9 rounded-xl object-cover border border-divider-soft"
                      />
                    ) : (
                      <div className="w-9 h-9 rounded-xl bg-primary-lighter/30 text-primary-main flex items-center justify-center font-bold text-xs flex-shrink-0">
                        {getInitials(acc.name)}
                      </div>
                    )}
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-text-primary">{acc.name}</span>
                        {acc.username && (
                          <span className="text-xs text-text-secondary">@{acc.username}</span>
                        )}
                        <span className="badge badge-gray text-[10px] uppercase">{acc.type}</span>
                      </div>
                      <p className="text-xs text-text-secondary mt-0.5">
                        {acc.used_by_destination_name && acc.used_by_destination_id ? (
                          <span>
                            Used by{" "}
                            <Link
                              href={`/social-media/channels/${acc.used_by_destination_id}`}
                              className="text-primary-main hover:underline font-medium"
                            >
                              {acc.used_by_destination_name}
                            </Link>
                          </span>
                        ) : (
                          <span className="text-text-disabled italic">Not used by any channel</span>
                        )}
                      </p>
                    </div>
                  </div>

                  <span className="badge badge-green self-start sm:self-auto">Connected</span>
                </div>
              ))}
            </div>
          )}

        {!loadingRepliz &&
          replizData?.configured !== false &&
          replizAccounts.length === 0 &&
          !replizData?.error && (
            <div className="card p-6 text-center text-xs text-text-secondary">
              No Repliz accounts linked. Connect one in Settings or Repliz dashboard.
            </div>
          )}
      </section>

      {/* ── Section: YouTube, X, Threads ── */}
      <section aria-labelledby="other-platforms-heading" className="space-y-3 pt-2">
        <h2 id="other-platforms-heading" className="text-base font-bold text-text-secondary">
          YouTube, X, Threads
        </h2>
        <div className="card p-5 border border-dashed border-divider-soft space-y-1.5 opacity-75">
          <div className="flex items-center gap-2">
            <span className="badge badge-gray text-[10px]">Future Platforms</span>
            <span className="text-xs font-semibold text-text-primary">Not built yet</span>
          </div>
          <p className="text-xs text-text-secondary">
            Account connections for these platforms are not built yet. Channels can be planned on a
            brand in the meantime.
          </p>
        </div>
      </section>

      {/* ── Section: Devices ── */}
      <section id="devices" aria-labelledby="devices-heading" className="space-y-4 pt-2">
        <div className="flex items-center justify-between">
          <div>
            <h2 id="devices-heading" className="text-base font-bold text-text-primary">
              Devices ({devices.length})
            </h2>
            <p className="text-xs text-text-secondary mt-0.5">
              Physical phones running automation agents for mobile publishing.
            </p>
          </div>
          <Link
            href="/settings#devices"
            className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
          >
            <Icon icon="solar:smartphone-bold-duotone" width={14} />
            <span>Manage devices</span>
          </Link>
        </div>

        {/* Loading / Error for Devices */}
        {loadingDevices && (
          <div className="card p-6 text-center text-xs text-text-secondary flex flex-col items-center gap-2">
            <Icon icon="solar:refresh-bold-duotone" width={22} className="animate-spin text-primary-main" />
            <span>Loading phone devices…</span>
          </div>
        )}

        {devicesError && !loadingDevices && (
          <div className="card p-6 text-center space-y-2 border-error-main/40 bg-error-lighter/20">
            <Icon icon="solar:danger-triangle-bold-duotone" width={28} className="mx-auto text-error-main" />
            <p className="text-xs font-semibold text-text-primary">Could not load devices</p>
            <p className="text-xs text-text-secondary">{getErrorMessage(devicesError)}</p>
            <button
              type="button"
              onClick={() => mutateDevices()}
              className="btn-secondary text-xs py-1 px-3 inline-flex items-center gap-1"
            >
              <Icon icon="solar:refresh-bold-duotone" width={14} />
              Retry
            </button>
          </div>
        )}

        {!loadingDevices && !devicesError && devices.length === 0 && (
          <div className="card p-6 text-center text-xs text-text-secondary">
            No phone devices registered. Register one in{" "}
            <Link href="/settings#devices" className="text-primary-main underline">
              Settings
            </Link>
            .
          </div>
        )}

        {/* Simple list of Phone Devices */}
        {!loadingDevices && !devicesError && devices.length > 0 && (
          <div className="space-y-3">
            {devices.map((dev) => (
              <article
                key={dev.id}
                className="card p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:border-text-disabled transition-colors"
              >
                <div className="flex items-center gap-3.5">
                  <div className="w-10 h-10 rounded-xl bg-bg-default border border-divider-soft flex items-center justify-center text-text-primary flex-shrink-0">
                    <Icon icon="solar:smartphone-bold-duotone" width={22} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-base font-bold text-text-primary">{dev.name}</span>
                      <span
                        className={`badge ${
                          dev.status === "available" ? "badge-green" : "badge-gray"
                        }`}
                      >
                        {dev.status === "available" ? "Online" : dev.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-text-secondary mt-0.5">
                      {dev.tailscale_ip && <span>IP: {dev.tailscale_ip}</span>}
                      <span>·</span>
                      <span>Last seen: {formatRelativeTime(dev.last_seen_at)}</span>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs bg-bg-default p-3 rounded-lg border border-divider-soft">
                  <div>
                    <span className="text-text-disabled block text-[10px] uppercase font-semibold">
                      Signed-in profile
                    </span>
                    <span className="font-semibold text-text-primary block mt-0.5">
                      {dev.expected_profile_name || "—"}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-disabled block text-[10px] uppercase font-semibold">
                      Calibration
                    </span>
                    <span className="block mt-0.5">
                      {dev.calibration_verified_at ? (
                        <span className="text-emerald-400 font-semibold">Passed</span>
                      ) : (
                        <span className="text-amber-400 font-semibold">Not calibrated</span>
                      )}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-disabled block text-[10px] uppercase font-semibold">
                      FB App Version
                    </span>
                    <span className="text-text-primary font-mono block mt-0.5 truncate">
                      {dev.facebook_app_version || "—"}
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}

        {/* Assignment & Verification Card */}
        <div className="pt-2">
          <AccountDevicesCard
            destinations={destinations}
            devices={devices}
            assignments={assignments}
            mutateAssignments={mutateAssignments}
            mutateDestinations={mutateDestinations}
            mutateDevices={mutateDevices}
          />
        </div>
      </section>
    </div>
  );
}
