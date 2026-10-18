import { redirect } from "next/navigation";

export default function Page({
  searchParams,
}: {
  searchParams?: { [key: string]: string | string[] | undefined };
}) {
  const query = searchParams ? new URLSearchParams(searchParams as Record<string, string>).toString() : "";
  redirect(`/brands/channels/new${query ? `?${query}` : ""}`);
}
