"use client";

import { useState, useEffect } from "react";

export const TARGET_COUNTRIES = [
  { code: "GB", name: "United Kingdom", defaultTz: "Europe/London" },
  { code: "US", name: "United States", defaultTz: "America/New_York" },
  { code: "FR", name: "France", defaultTz: "Europe/Paris" },
  { code: "ID", name: "Indonesia", defaultTz: "Asia/Jakarta" },
  { code: "AU", name: "Australia", defaultTz: "Australia/Sydney" },
  { code: "DE", name: "Germany", defaultTz: "Europe/Berlin" },
  { code: "IN", name: "India", defaultTz: "Asia/Kolkata" },
  { code: "IT", name: "Italy", defaultTz: "Europe/Rome" },
];

export const IANA_ZONES =
  typeof Intl !== "undefined" && (Intl as any).supportedValuesOf
    ? ((Intl as any).supportedValuesOf("timeZone") as string[])
    : ["Europe/London", "America/New_York", "Europe/Paris", "Asia/Jakarta"];
const IANA_SET = new Set(IANA_ZONES);

export function isValidTz(z: string): boolean {
  if (!z) return false;
  if (IANA_SET.has(z)) return true;
  if (!z.includes("/")) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: z });
    return true;
  } catch (e) {
    return false;
  }
}

export function TzClock({ tz }: { tz: string }) {
  const [nowLocal, setNowLocal] = useState("");
  const [nowWib, setNowWib] = useState("");

  useEffect(() => {
    function update() {
      try {
        const d = new Date();
        const safeTz = isValidTz(tz) ? tz : "Europe/London";
        setNowLocal(
          d.toLocaleTimeString("en-US", {
            timeZone: safeTz,
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })
        );
        setNowWib(
          d.toLocaleTimeString("en-US", {
            timeZone: "Asia/Jakarta",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })
        );
      } catch (e) {}
    }
    update();
    const timer = setInterval(update, 15000);
    return () => clearInterval(timer);
  }, [tz]);

  return (
    <span className="font-semibold text-pri">
      {nowLocal || "..."}{" "}
      <span className="font-normal text-text2">(WIB {nowWib || "..."})</span>
    </span>
  );
}

/**
 * @deprecated GlobalTab is superseded by PacingTab, DesignTab, and CaptionsTab.
 */
export function GlobalTab() {
  return null;
}
