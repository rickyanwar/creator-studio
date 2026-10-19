"use client";

import { useState, useRef } from "react";
import { Icon } from "@iconify/react";

interface CopyIdProps {
  id: string;
  label?: string;
  className?: string;
}

export function CopyId({ id, label = "Repliz ID:", className = "" }: CopyIdProps) {
  const [copied, setCopied] = useState(false);
  const textRef = useRef<HTMLSpanElement>(null);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    let success = false;
    if (navigator?.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(id);
        success = true;
      } catch {
        success = false;
      }
    }

    if (!success) {
      // Fallback: copy via temporary textarea
      try {
        const textarea = document.createElement("textarea");
        textarea.value = id;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        textarea.style.top = "0";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        success = document.execCommand("copy");
        document.body.removeChild(textarea);
      } catch {
        success = false;
      }
    }

    // On copy failure, select the text so user can copy manually
    if (!success && textRef.current) {
      const range = document.createRange();
      range.selectNodeContents(textRef.current);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }

    setCopied(true);
    setTimeout(() => {
      setCopied(false);
    }, 1500);
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-surface2 border border-line text-[11px] text-text font-mono select-none ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      {label && (
        <span className="font-sans text-[10px] text-text2 uppercase tracking-wider font-medium">
          {label}
        </span>
      )}
      <span
        ref={textRef}
        className="font-mono text-text select-all tracking-tight break-all"
        title={id}
      >
        {id}
      </span>
      <button
        type="button"
        onClick={handleCopy}
        title={copied ? "Copied to clipboard" : "Copy full Repliz ID"}
        aria-label={copied ? "Copied" : "Copy Repliz ID"}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-sans font-medium text-pri hover:bg-pri/10 transition-colors ml-0.5 cursor-pointer flex-shrink-0"
      >
        <Icon
          icon={copied ? "solar:check-read-bold" : "solar:copy-bold-duotone"}
          width={12}
          className={copied ? "text-good" : "text-pri"}
        />
        <span className={copied ? "text-good font-semibold" : "text-pri"}>
          {copied ? "Copied" : "Copy"}
        </span>
      </button>
    </span>
  );
}
