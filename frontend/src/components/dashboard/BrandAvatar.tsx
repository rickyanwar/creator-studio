"use client";

import { useState, useEffect } from "react";

export interface BrandAvatarProps {
  name: string;
  initials?: string;
  color?: string;
  colorClass?: string;
  pictureUrl?: string | null;
  className?: string;
  onError?: () => void;
}

export function BrandAvatar({
  name,
  initials,
  color,
  colorClass,
  pictureUrl,
  className = "",
  onError,
}: BrandAvatarProps) {
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [pictureUrl]);

  const handleImgError = () => {
    setImgError(true);
    onError?.();
  };

  if (pictureUrl && !imgError) {
    return (
      /* eslint-disable-next-line @next/next/no-img-element */
      <img
        src={pictureUrl}
        alt={name}
        onError={handleImgError}
        className={`w-[30px] h-[30px] rounded-lg object-cover shrink-0 ${className}`}
      />
    );
  }

  const isHexOrRgb = color?.startsWith("#") || color?.startsWith("rgb");

  return (
    <span
      aria-hidden="true"
      className={`w-[30px] h-[30px] rounded-lg text-white flex items-center justify-center text-xs font-bold shrink-0 ${
        colorClass || (!isHexOrRgb && color ? color : "")
      } ${className}`}
      style={isHexOrRgb ? { background: color } : undefined}
    >
      {initials || "FP"}
    </span>
  );
}
