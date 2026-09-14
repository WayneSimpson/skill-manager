import { type FormEvent, useState } from "react";
import { Loader2, RefreshCw, ShieldAlert, Unplug } from "lucide-react";

import {
  useDisconnectOpenCodeRuntimeSkillsMutation,
  useOpenCodeRuntimeSkillsStatusQuery,
  useRefreshOpenCodeRuntimeSkillsMutation,
} from "../api/queries";
import type { OpenCodeRuntimeSkillsRefreshRequest } from "../api/types";
import { useSettingsCopy } from "../i18n";

export function OpenCodeRuntimeSkillsPanel() {
  const copy = useSettingsCopy().runtimeSkills;
  const statusQuery = useOpenCodeRuntimeSkillsStatusQuery();
  const refreshMutation = useRefreshOpenCodeRuntimeSkillsMutation();
  const disconnectMutation = useDisconnectOpenCodeRuntimeSkillsMutation();
  const [serverUrl, setServerUrl] = useState("");
  const [directory, setDirectory] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);

  const hasUsername = username.trim().length > 0;
  const hasPassword = password.length > 0;
  const incompleteCredentials = hasUsername !== hasPassword;
  const formReady =
    consent &&
    serverUrl.trim().length > 0 &&
    directory.trim().length > 0 &&
    !incompleteCredentials &&
    !refreshMutation.isPending;
  const status = statusQuery.data;
  const statusError =
    status?.error ?? refreshMutation.error?.message ?? disconnectMutation.error?.message;
  const queryError = statusQuery.error instanceof Error ? statusQuery.error.message : copy.unableToLoad;
  const runtimeError = statusError ?? (statusQuery.isError ? queryError : null);
  const hasConnection = status?.status !== undefined && status.status !== "disconnected";

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!formReady) {
      return;
    }

    const payload: OpenCodeRuntimeSkillsRefreshRequest = {
      consent: true,
      serverUrl: serverUrl.trim(),
      directory: directory.trim(),
      ...(hasUsername && hasPassword ? { username: username.trim(), password } : {}),
    };

    refreshMutation.reset();
    disconnectMutation.reset();
    setUsername("");
    setPassword("");
    setConsent(false);
    try {
      await refreshMutation.mutateAsync(payload);
    } catch {
      // The mutation error is rendered below the snapshot status.
    }
  }

  async function handleDisconnect(): Promise<void> {
    disconnectMutation.reset();
    refreshMutation.reset();
    try {
      await disconnectMutation.mutateAsync();
    } catch {
      // The mutation error is rendered below the snapshot status.
    }
  }

  return (
    <section className="settings-section settings-runtime" aria-labelledby="runtime-skills-heading">
      <div className="settings-runtime__heading">
        <div>
          <h2 id="runtime-skills-heading" className="settings-section__heading">
            {copy.heading}
          </h2>
          <p className="settings-runtime__subtitle">{copy.subtitle}</p>
        </div>
        <div className="settings-runtime__status" aria-live="polite" aria-busy={statusQuery.isPending}>
          <span className="settings-runtime__status-label">{copy.statusLabel}</span>
          <span className="settings-runtime__status-value" data-status={status?.status ?? "loading"}>
            {status ? copy.statuses[status.status] : copy.checking}
          </span>
        </div>
      </div>

      <div className="settings-runtime__snapshot">
        <div className="settings-runtime__snapshot-copy">
          <strong>{copy.snapshotLabel}</strong>
          <p>
            {status
              ? status.status === "disconnected"
                ? copy.disconnectedBody
                : copy.snapshotCount(status.skillCount)
              : copy.checking}
          </p>
          {status?.lastRefreshed ? (
            <p>{copy.lastRefreshed} <time dateTime={status.lastRefreshed}>{status.lastRefreshed}</time></p>
          ) : null}
          {status?.stale ? <p role="status">{copy.staleSnapshot}</p> : null}
          {status?.serverUrl && status.directory ? (
            <div className="settings-runtime__connection">
              <span className="settings-path">{status.serverUrl}</span>
              <span className="settings-path">{status.directory}</span>
            </div>
          ) : null}
          {runtimeError ? (
            <p className="settings-runtime__error" role="alert">
              {runtimeError}
            </p>
          ) : null}
        </div>
        {hasConnection ? (
          <button
            type="button"
            className="action-pill"
            onClick={() => void handleDisconnect()}
            disabled={disconnectMutation.isPending || refreshMutation.isPending}
          >
            {disconnectMutation.isPending ? (
              <Loader2 size={13} className="card-action-spinner" aria-hidden="true" />
            ) : (
              <Unplug size={13} aria-hidden="true" />
            )}
            {disconnectMutation.isPending ? copy.clearing : copy.clearSnapshot}
          </button>
        ) : null}
      </div>

      <form className="settings-runtime__form" onSubmit={(event) => void handleSubmit(event)}>
        <div className="settings-runtime__form-grid">
          <div className="settings-runtime__field">
            <label htmlFor="runtime-server-url">{copy.serverUrlLabel}</label>
            <input
              id="runtime-server-url"
              type="url"
              value={serverUrl}
              placeholder={copy.serverUrlPlaceholder}
              required
              autoComplete="url"
              onChange={(event) => setServerUrl(event.target.value)}
            />
          </div>
          <div className="settings-runtime__field">
            <label htmlFor="runtime-directory">{copy.directoryLabel}</label>
            <input
              id="runtime-directory"
              type="text"
              value={directory}
              placeholder={copy.directoryPlaceholder}
              required
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setDirectory(event.target.value)}
            />
          </div>
          <div className="settings-runtime__field">
            <label htmlFor="runtime-username">{copy.usernameLabel}</label>
            <input
              id="runtime-username"
              type="text"
              value={username}
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="settings-runtime__field">
            <label htmlFor="runtime-password">{copy.passwordLabel}</label>
            <input
              id="runtime-password"
              type="password"
              value={password}
              autoComplete="current-password"
              aria-describedby="runtime-password-hint"
              onChange={(event) => setPassword(event.target.value)}
            />
            <small id="runtime-password-hint">{copy.passwordHint}</small>
          </div>
        </div>

        {incompleteCredentials ? <p className="settings-runtime__validation-error">{copy.incompleteCredentials}</p> : null}

        <label className="settings-runtime__consent">
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />
          <span>{copy.consentLabel}</span>
        </label>
        <p className="settings-runtime__warning">
          <ShieldAlert size={15} aria-hidden="true" />
          <span>{copy.consentWarning}</span>
        </p>

        <div className="settings-runtime__actions">
          <button
            type="submit"
            className="action-pill action-pill--accent"
            disabled={!formReady}
            aria-label={copy.refreshAria}
          >
            {refreshMutation.isPending ? (
              <Loader2 size={13} className="card-action-spinner" aria-hidden="true" />
            ) : (
              <RefreshCw size={13} aria-hidden="true" />
            )}
            {refreshMutation.isPending ? copy.refreshing : copy.refresh}
          </button>
        </div>
      </form>
    </section>
  );
}
