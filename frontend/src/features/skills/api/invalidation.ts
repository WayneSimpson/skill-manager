import type { QueryClient } from "@tanstack/react-query";

import { skillsKeys } from "./keys";

export async function invalidateSkillsQueries(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: skillsKeys.list() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.detailPrefix() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.sourceStatusPrefix() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.sourcePackages() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.managedPackages() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.packageContextPrefix() }),
    queryClient.invalidateQueries({ queryKey: skillsKeys.packageDeploymentsPrefix() }),
  ]);
}
