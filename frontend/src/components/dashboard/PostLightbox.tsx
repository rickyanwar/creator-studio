"use client";

import { useEffect, useMemo, useRef } from "react";
import type { LatestPost } from "@/lib/dashboardApi";
import { Icon } from "@iconify/react";

export interface PostLightboxProps {
  posts: LatestPost[];
  index: number | null;
  onClose: () => void;
  onIndexChange: (i: number) => void;
}

function fmtTimeAgo(isoString: string | null | undefined): string {
  if (!isoString) return "";
  try {
    const diffMs = Date.now() - new Date(isoString).getTime();
    if (diffMs < 0) return "just now";
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return "";
  }
}

export function PostLightbox({
  posts,
  index,
  onClose,
  onIndexChange,
}: PostLightboxProps) {
  const touchStartXRef = useRef<number | null>(null);

  const prevIndex = useMemo(() => {
    if (index === null) return null;
    for (let i = index - 1; i >= 0; i--) {
      if (posts[i]?.thumbnail_url) return i;
    }
    return null;
  }, [posts, index]);

  const nextIndex = useMemo(() => {
    if (index === null) return null;
    for (let i = index + 1; i < posts.length; i++) {
      if (posts[i]?.thumbnail_url) return i;
    }
    return null;
  }, [posts, index]);

  const validIndices = useMemo(() => {
    const ids: number[] = [];
    posts.forEach((p, i) => {
      if (p.thumbnail_url) ids.push(i);
    });
    return ids;
  }, [posts]);

  useEffect(() => {
    if (index === null) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "ArrowLeft") {
        if (prevIndex !== null) onIndexChange(prevIndex);
      } else if (e.key === "ArrowRight") {
        if (nextIndex !== null) onIndexChange(nextIndex);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [index, prevIndex, nextIndex, onClose, onIndexChange]);

  if (index === null || !posts[index]) return null;

  const currentPost = posts[index];
  if (!currentPost.thumbnail_url) return null;

  const currentPos = validIndices.indexOf(index);
  const counterText =
    currentPos >= 0
      ? `${currentPos + 1} / ${validIndices.length}`
      : `${index + 1} / ${posts.length}`;

  const cleanTitle = (currentPost.title || currentPost.content_type || "")
    .replace(/\*\*/g, "")
    .trim();
  const timeAgo = fmtTimeAgo(currentPost.published_at);

  const handleTouchStart = (e: React.TouchEvent) => {
    touchStartXRef.current = e.touches[0].clientX;
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (touchStartXRef.current === null) return;
    const touchEndX = e.changedTouches[0].clientX;
    const dx = touchEndX - touchStartXRef.current;
    touchStartXRef.current = null;
    if (dx > 50) {
      if (prevIndex !== null) onIndexChange(prevIndex);
    } else if (dx < -50) {
      if (nextIndex !== null) onIndexChange(nextIndex);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm p-4 select-none"
      onClick={onClose}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <div
        className="absolute top-4 left-4 px-3 py-1.5 rounded-full bg-white/10 text-white text-xs font-semibold"
        onClick={(e) => e.stopPropagation()}
      >
        {counterText}
      </div>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        title="Close (Esc)"
        className="absolute top-4 right-4 p-2 rounded-lg bg-white/10 text-white hover:bg-white/20 transition-colors"
      >
        <Icon icon="solar:close-circle-bold" width={28} />
      </button>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          if (prevIndex !== null) onIndexChange(prevIndex);
        }}
        disabled={prevIndex === null}
        title="Previous (←)"
        className={`absolute left-4 p-2 rounded-full transition-colors ${
          prevIndex !== null
            ? "bg-white/10 text-white hover:bg-white/20 cursor-pointer"
            : "bg-white/5 text-white/30 cursor-not-allowed"
        }`}
      >
        <Icon icon="solar:alt-arrow-left-bold" width={28} />
      </button>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          if (nextIndex !== null) onIndexChange(nextIndex);
        }}
        disabled={nextIndex === null}
        title="Next (→)"
        className={`absolute right-4 p-2 rounded-full transition-colors ${
          nextIndex !== null
            ? "bg-white/10 text-white hover:bg-white/20 cursor-pointer"
            : "bg-white/5 text-white/30 cursor-not-allowed"
        }`}
      >
        <Icon icon="solar:alt-arrow-right-bold" width={28} />
      </button>

      <div
        className="max-w-[90vw] max-h-[90vh] flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={currentPost.thumbnail_url}
          alt={cleanTitle || currentPost.fanpage_name}
          className="max-w-[90vw] max-h-[85vh] object-contain rounded-lg"
        />

        <div className="mt-3 text-center text-white/80 text-xs max-w-xl">
          <div className="flex items-center justify-center gap-2 flex-wrap">
            <span className="font-semibold text-white">
              {currentPost.fanpage_name}
            </span>
            {timeAgo && <span className="text-white/60">· {timeAgo}</span>}
            <span className="text-white/60">· {counterText}</span>
          </div>
          {cleanTitle && (
            <p className="mt-1 text-white/90 text-xs leading-normal">
              {cleanTitle}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
