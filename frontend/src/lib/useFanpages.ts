import useSWR from "swr";
import { listFanpages } from "@/lib/api";
import type { Fanpage } from "@/lib/types";

const fetchFanpages = async (): Promise<Fanpage[]> => {
  const response = await listFanpages();
  return Array.isArray(response.data) ? (response.data as Fanpage[]) : [];
};

export function useFanpages() {
  return useSWR<Fanpage[]>("fanpages", fetchFanpages, {
    fallbackData: [],
  });
}
