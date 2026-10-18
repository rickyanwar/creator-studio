import Link from "next/link";
import type { ContentModeOut } from "@/lib/socialBrandsApi";

export interface BrandContentTableProps {
  brandId: number;
  content: ContentModeOut[];
  publishMode: string | null;
}

function formatMedia(media: string): string {
  if (!media) return "—";
  return media
    .split("/")
    .map((m) => m.trim().charAt(0).toUpperCase() + m.trim().slice(1))
    .join(" / ");
}

function formatPublishMode(mode: string | null): string {
  if (!mode) return "—";
  if (mode === "auto") return "Auto publish";
  if (mode === "manual_review") return "Review first";
  return mode;
}

export function BrandContentTable({
  brandId,
  content,
  publishMode,
}: BrandContentTableProps) {
  const sortedModes = [...(content || [])].sort(
    (a, b) => Number(b.enabled) - Number(a.enabled)
  );

  return (
    <section aria-labelledby="brand-content-heading" className="card space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2
            id="brand-content-heading"
            className="text-lg font-bold text-text-primary"
          >
            Content this brand makes
          </h2>
          <p className="text-xs text-text-secondary mt-1 max-w-2xl">
            Set once for the whole brand. Every channel below receives these
            posts, filtered only by the media types each channel accepts.
          </p>
        </div>
        <Link
          href={`/brands/${brandId}?tab=content`}
          className="text-xs sm:text-sm font-semibold text-pri hover:underline whitespace-nowrap"
        >
          Sources, templates and captions →
        </Link>
      </div>

      <div className="overflow-x-auto border border-divider-soft rounded-lg">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="border-b border-divider-soft bg-bg-paper-hover text-text-secondary font-semibold">
              <th scope="col" className="py-3 px-4">
                Content type
              </th>
              <th scope="col" className="py-3 px-4">
                Media
              </th>
              <th scope="col" className="py-3 px-4">
                Per day
              </th>
              <th scope="col" className="py-3 px-4">
                Before posting
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider-soft">
            {sortedModes.map((mode) => {
              const isOff = !mode.enabled;
              return (
                <tr
                  key={mode.key}
                  className={`transition-colors ${
                    isOff
                      ? "bg-bg-paper-hover/30 text-text-disabled"
                      : "hover:bg-bg-paper-hover text-text-primary"
                  }`}
                >
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{mode.label}</span>
                      {isOff && (
                        <span className="badge badge-gray text-[10px]">
                          Off
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    <span
                      className={`badge ${
                        isOff
                          ? "badge-gray opacity-60"
                          : mode.media.includes("video")
                          ? "badge-purple"
                          : "badge-blue"
                      }`}
                    >
                      {formatMedia(mode.media)}
                    </span>
                  </td>
                  <td className="py-3 px-4 tabular-nums">
                    {isOff ? (
                      <span className="text-text-disabled">Off</span>
                    ) : mode.daily_count !== null ? (
                      `${mode.daily_count} / day`
                    ) : (
                      <span className="text-text-secondary">As available</span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    {isOff ? (
                      <span className="text-text-disabled">—</span>
                    ) : (
                      <span className="font-medium">
                        {formatPublishMode(publishMode)}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
