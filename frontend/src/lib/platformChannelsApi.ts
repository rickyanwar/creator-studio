/**
 * Platform channels configuration, types, and helpers for Repliz multi-platform publishing.
 * Supports Facebook Page, Facebook Profile, Instagram, YouTube, Threads, and X.
 */

export type DestinationPlatformKind =
  | "facebook_page"
  | "facebook_profile"
  | "instagram"
  | "youtube"
  | "threads"
  | "x";

export interface PlatformRuleInfo {
  platform: DestinationPlatformKind;
  label: string;
  icon: string;
  iconBg: string;
  publishMethod: "phone_or_repliz" | "phone_only" | "repliz_only";
  captionLimit?: number;
  maxHashtags?: number;
  acceptedMedia: string[];
  limitsSummary: string;
  rules: string[];
}

export interface ReplizAccountLike {
  id: string;
  name: string;
  username?: string | null;
  type?: string | null;
  platform?: string | null;
  used_by_destination_id?: number | null;
  used_by_destination_name?: string | null;
}

export const PLATFORM_RULES: Record<string, PlatformRuleInfo> = {
  facebook_page: {
    platform: "facebook_page",
    label: "Facebook Page",
    icon: "logos:facebook",
    iconBg: "bg-info-soft text-info",
    publishMethod: "phone_or_repliz",
    captionLimit: 600,
    acceptedMedia: ["image", "album", "video"],
    limitsSummary: "Phone, Repliz or both · Single image, album, or video · Caption ≤ 600",
    rules: [
      "Publish via Phone or Repliz",
      "Single image, album, or video supported",
      "Pacing & quiet hours apply",
    ],
  },
  facebook_profile: {
    platform: "facebook_profile",
    label: "Facebook Profile timeline",
    icon: "solar:user-bold",
    iconBg: "bg-surface2 text-text border border-line",
    publishMethod: "phone_only",
    captionLimit: 600,
    acceptedMedia: ["image", "album", "video"],
    limitsSummary: "Phone only · Timeline posting · Caption ≤ 600",
    rules: [
      "Phone publishing only",
      "Profile timeline posting",
      "Single image, album, or video supported",
    ],
  },
  instagram: {
    platform: "instagram",
    label: "Instagram",
    icon: "skill-icons:instagram",
    iconBg: "bg-rose-500/15 text-rose-500",
    publishMethod: "repliz_only",
    captionLimit: 2200,
    maxHashtags: 30,
    acceptedMedia: ["image", "album", "video"],
    limitsSummary: "Repliz only · Caption ≤ 2200 · Max 30 hashtags",
    rules: [
      "Published via Repliz",
      "Caption limit: 2200 characters",
      "Hashtag limit: maximum 30 hashtags (excess dropped from end)",
      "Quotes are never shortened or paraphrased",
    ],
  },
  youtube: {
    platform: "youtube",
    label: "YouTube",
    icon: "logos:youtube-icon",
    iconBg: "bg-bad-soft text-bad",
    publishMethod: "repliz_only",
    acceptedMedia: ["video"],
    limitsSummary: "Repliz only · Video only · Title ≤ 100",
    rules: [
      "Published via Repliz",
      "Video only (Reels / Shorts) — photos and albums are skipped",
      "Title ≤ 100 characters (derived from design title or caption first line)",
      "Quotes are never shortened or paraphrased (uses brand + date fallback if quote would be cut)",
    ],
  },
  threads: {
    platform: "threads",
    label: "Threads",
    icon: "ri:threads-fill",
    iconBg: "bg-surface2 text-text border border-line",
    publishMethod: "repliz_only",
    captionLimit: 500,
    acceptedMedia: ["image", "album", "video"],
    limitsSummary: "Repliz only · Caption ≤ 500",
    rules: [
      "Published via Repliz",
      "Caption limit: 500 characters (excess hashtags dropped from end)",
      "Text without hashtags longer than 500 is skipped",
      "Quotes are never shortened or paraphrased",
    ],
  },
  x: {
    platform: "x",
    label: "X",
    icon: "ri:twitter-x-fill",
    iconBg: "bg-surface2 text-text border border-line",
    publishMethod: "repliz_only",
    captionLimit: 280,
    acceptedMedia: ["image", "album", "video"],
    limitsSummary: "Repliz only · Single image, album, or video · Caption ≤ 280",
    rules: [
      "Published via Repliz",
      "Caption limit: 280 characters (excess hashtags dropped from end)",
      "Single image, album, or video supported",
      "Quotes are never shortened or paraphrased",
    ],
  },
};

export function getPlatformRuleInfo(kind: string): PlatformRuleInfo {
  return (
    PLATFORM_RULES[kind] || {
      platform: "facebook_page",
      label: kind,
      icon: "solar:link-bold",
      iconBg: "bg-surface2 text-text",
      publishMethod: "repliz_only",
      acceptedMedia: ["image", "album", "video"],
      limitsSummary: "Standard publishing limits apply",
      rules: [],
    }
  );
}

export function findMatchingReplizAccount(
  platformId: string,
  accounts?: ReplizAccountLike[] | null,
  brand?: { name?: string; username?: string | null } | null
): ReplizAccountLike | null {
  if (!accounts || accounts.length === 0) return null;
  const targetType = platformId === "x" ? "twitter" : platformId;
  const matchingAccounts = accounts.filter((a) => {
    const t = (a.type || a.platform || "").toLowerCase();
    return t === platformId || t === targetType;
  });

  if (matchingAccounts.length === 0) return null;
  if (!brand || !brand.name) return matchingAccounts[0];

  const cleanBrand = brand.name.toLowerCase().replace(/[^a-z0-9]/g, "");
  const cleanUsername = (brand.username || "").toLowerCase().replace(/[^a-z0-9]/g, "");

  const exactOrSubstring = matchingAccounts.find((a) => {
    const cleanName = (a.name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const cleanAccUser = (a.username || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    return (
      (cleanBrand && (cleanName.includes(cleanBrand) || cleanBrand.includes(cleanName))) ||
      (cleanUsername && (cleanAccUser.includes(cleanUsername) || cleanUsername.includes(cleanAccUser)))
    );
  });

  return exactOrSubstring || matchingAccounts[0];
}
