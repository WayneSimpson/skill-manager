export {
  invalidateSettingsQueries,
  runtimeSkillsKeys,
  useDisconnectOpenCodeRuntimeSkillsMutation,
  settingsKeys,
  useHarnessSupportMutation,
  useOpenCodeRuntimeSkillsStatusQuery,
  useRefreshOpenCodeRuntimeSkillsMutation,
  useSettingsQuery,
} from "./api/queries";

export const settingsRoutes = {
  settings: "/settings",
} as const;
