import { redirect } from "next/navigation";

export default function Page({
  params,
  searchParams,
}: {
  params: { id: string };
  searchParams?: { [key: string]: string | string[] | undefined };
}) {
  const query = searchParams ? new URLSearchParams(searchParams as Record<string, string>).toString() : "";
  redirect(`/brands/channels/${params.id}${query ? `?${query}` : ""}`);
}
