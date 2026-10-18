import { redirect } from "next/navigation";

export default function NewsSourcesRedirect() {
  redirect("/sources?tab=news");
}
