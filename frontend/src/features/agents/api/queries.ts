import { useQuery, type QueryClient } from "@tanstack/react-query";

import { queryPolicy } from "../../../lib/query";
import { fetchOpenCodeAgents } from "./client";
import { AGENTS_GC_TIME_MS, AGENTS_STALE_TIME_MS, agentKeys } from "./keys";

export { agentKeys } from "./keys";

export function useOpenCodeAgentsQuery() {
  return useQuery({
    queryKey: agentKeys.opencode(),
    queryFn: fetchOpenCodeAgents,
    ...queryPolicy(AGENTS_STALE_TIME_MS, AGENTS_GC_TIME_MS),
  });
}

export async function invalidateAgentQueries(queryClient: QueryClient): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: agentKeys.all });
}
