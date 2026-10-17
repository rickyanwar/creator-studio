export const COMMON_TIMEZONES = [
  "Europe/London",
  "Asia/Jakarta",
  "Asia/Singapore",
  "Asia/Kuala_Lumpur",
  "Asia/Bangkok",
  "Asia/Tokyo",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Australia/Sydney",
  "Australia/Melbourne",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Rome",
  "Europe/Madrid",
  "UTC",
];

export function getAllTimezones(): string[] {
  if (typeof Intl !== "undefined" && typeof (Intl as any).supportedValuesOf === "function") {
    try {
      const all = (Intl as any).supportedValuesOf("timeZone") as string[];
      if (Array.isArray(all) && all.length > 0) {
        return all;
      }
    } catch {
      // Fallback below
    }
  }
  return COMMON_TIMEZONES;
}

export function isValidTimezone(tz: string): boolean {
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function getTimezoneDiffWib(tz: string): string {
  try {
    const now = new Date();
    const targetFormatter = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      hour12: false,
    });
    const wibFormatter = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Jakarta",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      hour12: false,
    });

    const targetDate = new Date(targetFormatter.format(now));
    const wibDate = new Date(wibFormatter.format(now));
    const diffHours = Math.round((targetDate.getTime() - wibDate.getTime()) / (1000 * 60 * 60));
    const city = tz.split("/")[1]?.replace(/_/g, " ") || tz;

    if (diffHours === 0) {
      return `${city} is the same time as WIB (UTC+7)`;
    } else if (diffHours < 0) {
      const h = Math.abs(diffHours);
      return `${city} is ${h} ${h === 1 ? "hour" : "hours"} behind WIB`;
    } else {
      return `${city} is ${diffHours} ${diffHours === 1 ? "hour" : "hours"} ahead of WIB`;
    }
  } catch {
    return "Audience local clock";
  }
}

export function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const res = (err as { response?: { data?: { detail?: unknown } } }).response;
    if (res?.data?.detail) {
      if (typeof res.data.detail === "string") return res.data.detail;
      if (Array.isArray(res.data.detail)) {
        return res.data.detail
          .map((d: { msg?: string }) => d.msg || JSON.stringify(d))
          .join(", ");
      }
      return JSON.stringify(res.data.detail);
    }
  }
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred.";
}

export function formatDualTime(
  dateStr: string | null | undefined,
  targetTz: string
): { main: string; wib: string } {
  if (!dateStr) return { main: "—", wib: "—" };
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return { main: dateStr, wib: "" };

    const targetTime = d.toLocaleTimeString("en-GB", {
      timeZone: isValidTimezone(targetTz) ? targetTz : "Europe/London",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

    const wibTime = d.toLocaleTimeString("en-GB", {
      timeZone: "Asia/Jakarta",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

    const isToday = new Date().toDateString() === d.toDateString();
    const dayLabel = isToday ? "Today" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });

    return {
      main: `${dayLabel} ${targetTime}`,
      wib: `${wibTime} WIB`,
    };
  } catch {
    return { main: dateStr, wib: "" };
  }
}

export function getDesignEngineBadge(engine: string | null | undefined): {
  label: string;
  bg: string;
  text: string;
} {
  switch (engine?.toLowerCase()) {
    case "chatgpt":
      return { label: "ChatGPT", bg: "bg-emerald-950/70 border border-emerald-700/60", text: "text-emerald-300" };
    case "codex":
      return { label: "Codex", bg: "bg-blue-950/70 border border-blue-700/60", text: "text-blue-300" };
    case "codex_fallback":
      return { label: "Codex · fallback", bg: "bg-amber-950/70 border border-amber-700/60", text: "text-amber-300" };
    case "template":
      return { label: "Template", bg: "bg-slate-800/70 border border-slate-700", text: "text-slate-300" };
    default:
      return { label: "—", bg: "bg-transparent", text: "text-text-disabled" };
  }
}

export function getDeliveryStateBadge(state: string | null | undefined): {
  label: string;
  bg: string;
  text: string;
  dot: string;
} {
  const s = state?.toLowerCase() || "";
  if (s === "published" || s === "posted" || s === "success") {
    return { label: "Posted", bg: "bg-emerald-950/60 border border-emerald-800/60", text: "text-emerald-400", dot: "bg-emerald-500" };
  }
  if (s.includes("fallback")) {
    return { label: "Posted via fallback", bg: "bg-amber-950/60 border border-amber-800/60", text: "text-amber-400", dot: "bg-amber-500" };
  }
  if (s.includes("failed") || s.includes("error")) {
    return { label: "Failed", bg: "bg-rose-950/60 border border-rose-800/60", text: "text-rose-400", dot: "bg-rose-500" };
  }
  if (s === "uncertain") {
    return { label: "Uncertain — checking", bg: "bg-amber-950/60 border border-amber-800/60", text: "text-amber-400", dot: "bg-amber-500" };
  }
  if (s === "scheduled" || s === "planned") {
    return { label: "Scheduled", bg: "bg-blue-950/60 border border-blue-800/60", text: "text-blue-400", dot: "bg-blue-500" };
  }
  if (s === "sending" || s === "publishing" || s === "processing") {
    return { label: "Sending", bg: "bg-sky-950/60 border border-sky-800/60", text: "text-sky-400", dot: "bg-sky-500" };
  }
  return { label: state || "Unknown", bg: "bg-slate-800/60 border border-slate-700", text: "text-slate-300", dot: "bg-slate-400" };
}
