import Link from "next/link";
import { Icon } from "@iconify/react";

export function BrandExplainerBox() {
  return (
    <aside className="bg-sky-500/10 border border-sky-500/30 rounded-xl p-4 flex gap-3.5 items-start text-sky-300">
      <Icon
        icon="solar:info-circle-bold-duotone"
        width={22}
        className="text-sky-400 flex-shrink-0 mt-0.5"
      />
      <div className="flex flex-col gap-1.5 text-xs sm:text-sm text-text-secondary leading-relaxed">
        <strong className="text-text-primary text-sm font-semibold">
          Facebook Page ≠ Facebook Profile
        </strong>
        <p>
          A <strong className="text-text-primary">Page</strong> is a brand channel,
          like this one. On a phone it is posted by a{" "}
          <strong className="text-text-primary">Profile</strong> that manages the
          Page, signed in on that phone. Profiles are your publishing accounts,
          not brand channels — manage them under{" "}
          <Link
            href="/brands/accounts"
            className="text-pri hover:underline font-medium"
          >
            Publishing accounts
          </Link>
          . A Profile&apos;s own timeline can still be added to a brand as its own channel.
        </p>
      </div>
    </aside>
  );
}
