import { PublishJob } from "@/lib/types";

/**
 * Checks if a URL points to a video file (.mp4, .mov, .webm, .m4v).
 * Strips query parameters and hash fragments before testing.
 */
export function isVideoUrl(url?: string | null): boolean {
  if (!url) return false;
  try {
    const clean = url.split("?")[0].split("#")[0].toLowerCase().trim();
    return (
      clean.endsWith(".mp4") ||
      clean.endsWith(".mov") ||
      clean.endsWith(".webm") ||
      clean.endsWith(".m4v")
    );
  } catch {
    return false;
  }
}

export interface JobMediaInfo {
  isVideo: boolean;
  videoSrc: string | null;
  poster?: string;
  thumbUrl: string | null;
  isAlbum: boolean;
  extraCount: number;
}

/**
 * Resolves media type, video playback URL, poster, and thumbnail for a PublishJob.
 */
export function getJobMediaInfo(job: PublishJob, urls: string[]): JobMediaInfo {
  const isClip =
    job.content_type === "youtube_clip" || job.content_type === "ai_video";

  const directVideoUrl =
    job.video_url && isVideoUrl(job.video_url) ? job.video_url : null;
  const urlVideo = urls.find((u) => isVideoUrl(u)) ?? null;

  const isVideo =
    isClip ||
    !!directVideoUrl ||
    !!urlVideo ||
    (urls.length > 0 && isVideoUrl(urls[0]));

  const videoSrc = isClip
    ? job.video_url ?? urlVideo
    : directVideoUrl ?? urlVideo ?? (job.video_url || null);

  const poster =
    job.video_thumbnail_url ||
    (urls[0] && !isVideoUrl(urls[0]) ? urls[0] : undefined);

  const thumbUrl =
    job.video_thumbnail_url ||
    (urls[0] && !isVideoUrl(urls[0]) ? urls[0] : null) ||
    urls[0] ||
    job.video_url ||
    null;

  const isAlbum =
    job.media_type === "album" || (!isVideo && urls.length > 1);
  const extraCount = isAlbum && urls.length > 1 ? urls.length - 1 : 0;

  return {
    isVideo,
    videoSrc,
    poster,
    thumbUrl,
    isAlbum,
    extraCount,
  };
}
