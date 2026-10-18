import { redirect } from "next/navigation";

interface Props {
  params: { id: string };
  searchParams?: Record<string, string | string[] | undefined>;
}

export default function SocialMediaBrandRedirectPage({ params, searchParams }: Props) {
  const query = new URLSearchParams();
  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (Array.isArray(v)) {
        for (const item of v) query.append(k, item);
      } else if (typeof v === "string") {
        query.set(k, v);
      }
    }
  }
  if (!query.has("tab")) {
    query.set("tab", "channels");
  }
  const qs = query.toString();
  redirect(`/brands/${params.id}${qs ? `?${qs}` : ""}`);
}
