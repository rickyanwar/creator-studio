/**
 * ClipPlayer — shared video player for Mode 7 YouTube clip previews (S2e).
 *
 * Used everywhere a youtube_clip job needs a playable preview:
 *   - Queue card (replaces the inline <video>)
 *   - Queue lightbox
 *   - History card
 *   - History lightbox
 *
 * Props:
 *   src     — absolute MP4 URL (job.video_url). If null/undefined the
 *             component renders a muted poster-only placeholder.
 *   poster  — thumbnail URL (job.video_thumbnail_url), shown before play.
 *   preload — HTMLVideoElement preload hint. Defaults to "metadata".
 *             Pass "none" for cards in long lists (e.g. History) to avoid
 *             a network spike when many cards are mounted simultaneously.
 *   className — extra Tailwind classes for the <video> / wrapper.
 */

"use client";

interface ClipPlayerProps {
  src?: string | null;
  poster?: string | null;
  preload?: "none" | "metadata" | "auto";
  className?: string;
}

export default function ClipPlayer({ src, poster, preload = "metadata", className = "" }: ClipPlayerProps) {
  if (!src) {
    /* No MP4 yet (still rendering) — show the thumbnail or a grey box */
    return poster ? (
      <img
        src={poster}
        alt="Clip thumbnail"
        className={`w-full object-cover ${className}`}
      />
    ) : (
      <div className={`w-full bg-black ${className}`} />
    );
  }

  return (
    <video
      src={src}
      poster={poster ?? undefined}
      controls
      playsInline
      preload={preload}
      className={`w-full bg-black ${className}`}
      /* Prevent any absolutely-positioned overlay from catching pointer
         events on top of the native video controls. Each call site is
         responsible for marking its own overlay divs pointer-events-none
         where needed — but the <video> element itself must never have its
         control bar obscured. */
    >
      Your browser does not support HTML5 video.
    </video>
  );
}
