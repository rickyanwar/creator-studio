"use client";

import { Icon } from "@iconify/react";
import { isVideoUrl } from "./mediaUtils";

export interface MediaThumbProps {
  thumbUrl?: string | null;
  videoUrl?: string | null;
  isVideo?: boolean;
  isAlbum?: boolean;
  extraCount?: number;
  blurred?: boolean;
  onClick?: () => void;
  className?: string;
  alt?: string;
}

export function MediaThumb({
  thumbUrl,
  videoUrl,
  isVideo = false,
  isAlbum = false,
  extraCount = 0,
  blurred = false,
  onClick,
  className = "",
  alt = "",
}: MediaThumbProps) {
  const thumbIsVideo = isVideoUrl(thumbUrl);
  const mediaIsVideo =
    isVideo || thumbIsVideo || (!!videoUrl && isVideoUrl(videoUrl));
  const blurClass = blurred ? "blur-sm" : "";

  return (
    <div
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (onClick && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onClick();
        }
      }}
      className={`w-16 h-20 sm:w-[88px] sm:h-[110px] rounded-xl bg-surface2 border border-line flex-shrink-0 overflow-hidden relative select-none ${
        onClick ? "cursor-zoom-in" : ""
      } ${className}`}
      title={onClick ? "Click to view preview" : undefined}
    >
      {thumbIsVideo ? (
        <video
          src={thumbUrl!}
          preload="metadata"
          muted
          playsInline
          className={`w-full h-full object-cover pointer-events-none ${blurClass}`}
        />
      ) : thumbUrl ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={thumbUrl}
          alt={alt}
          className={`w-full h-full object-cover pointer-events-none ${blurClass}`}
        />
      ) : videoUrl ? (
        <video
          src={videoUrl}
          preload="metadata"
          muted
          playsInline
          className={`w-full h-full object-cover pointer-events-none ${blurClass}`}
        />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center text-text2 opacity-60 gap-1">
          <Icon
            icon={
              mediaIsVideo
                ? "solar:videocamera-bold-duotone"
                : "solar:gallery-bold-duotone"
            }
            width={24}
          />
          <span className="text-[10px] font-medium uppercase tracking-wider">
            {mediaIsVideo ? "video" : "card"}
          </span>
        </div>
      )}

      {/* Play badge for video */}
      {mediaIsVideo && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-full bg-black/65 text-white flex items-center justify-center shadow-md backdrop-blur-xs">
            <Icon
              icon="solar:play-bold"
              width={16}
              className="ml-0.5 text-white"
            />
          </div>
        </div>
      )}

      {/* Album badge */}
      {isAlbum && extraCount > 0 && (
        <div className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded-md bg-black/75 text-white text-[10px] font-semibold leading-none pointer-events-none backdrop-blur-xs">
          +{extraCount}
        </div>
      )}
    </div>
  );
}
