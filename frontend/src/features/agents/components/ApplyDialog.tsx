import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import { LoadingSpinner } from "../../../components/LoadingSpinner";
import { useAgentsCopy } from "../i18n";
import type { AgentApplyCapabilityDto } from "../api/types";
import {
  useAcknowledgeManualApplyMutation,
  useApplyAgentConfigMutation,
} from "../api/queries";

interface ApplyDialogProps {
  open: boolean;
  capability: AgentApplyCapabilityDto | undefined;
  onOpenChange: (open: boolean) => void;
}

export function ApplyDialog({ open, capability, onOpenChange }: ApplyDialogProps) {
  const copy = useAgentsCopy();
  const [error, setError] = useState("");
  const [understood, setUnderstood] = useState(false);
  const applyMutation = useApplyAgentConfigMutation();
  const acknowledgeMutation = useAcknowledgeManualApplyMutation();
  const pending = applyMutation.isPending || acknowledgeMutation.isPending;

  const mechanism = capability?.mechanism ?? "unavailable";
  // Server capability is authoritative: only safely-executable mechanisms offer Apply.
  const executable = Boolean(capability?.canExecute) &&
    (mechanism === "reload" || mechanism === "restart-managed");

  async function confirmApply() {
    setError("");
    try {
      await applyMutation.mutateAsync({ confirm: true });
      onOpenChange(false);
    } catch (applyError) {
      setError(message(applyError, copy.apply.errors.apply));
    }
  }

  async function confirmAcknowledgement() {
    setError("");
    try {
      await acknowledgeMutation.mutateAsync({ confirm: true });
      onOpenChange(false);
    } catch (acknowledgeError) {
      setError(message(acknowledgeError, copy.apply.errors.apply));
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content agent-apply" aria-label={copy.apply.title}>
          <Dialog.Description className="agent-apply__description">
            {copy.apply.description}
          </Dialog.Description>
          <div className="dialog-header">
            <Dialog.Title className="dialog-title">{copy.apply.title}</Dialog.Title>
          </div>

          <p className={`agent-apply__mechanism agent-apply__mechanism--${mechanism}`}>
            {mechanism === "reload"
              ? copy.apply.mechanisms.reload
              : mechanism === "restart-managed"
                ? copy.apply.mechanisms.managed
                : mechanism === "restart-manual"
                  ? copy.apply.mechanisms.manual
                  : copy.apply.mechanisms.unavailable}
          </p>

          {mechanism === "reload" ? (
            <p className="agent-apply__warning">{copy.apply.warnings.reload}</p>
          ) : null}
          {mechanism === "restart-managed" ? (
            <p className="agent-apply__warning agent-apply__warning--strong">
              {copy.apply.warnings.managed}
            </p>
          ) : null}
          {mechanism === "restart-manual" ? (
            <>
              <p className="agent-apply__manual">{copy.apply.warnings.manual}</p>
              <label className="agent-apply__acknowledge">
                <input
                  type="checkbox"
                  checked={understood}
                  onChange={(event) => setUnderstood(event.target.checked)}
                />
                <span>{copy.apply.manualAcknowledge}</span>
              </label>
            </>
          ) : null}
          {capability && !capability.canExecute && mechanism === "reload" ? (
            <p className="agent-apply__manual">{copy.apply.detectedNotConfigured}</p>
          ) : null}
          {capability ? <p className="agent-apply__detail">{capability.detail}</p> : null}
          {error ? <p className="agent-apply__error" role="alert">{error}</p> : null}

          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => onOpenChange(false)}>
              {copy.apply.close}
            </button>
            {executable ? (
              <button
                type="button"
                className="btn btn--primary"
                disabled={pending}
                onClick={() => void confirmApply()}
              >
                {pending ? <LoadingSpinner size="sm" label={copy.apply.applying} /> : null}
                {copy.apply.confirmApply}
              </button>
            ) : mechanism === "restart-manual" ? (
              <button
                type="button"
                className="btn"
                disabled={pending || !understood}
                onClick={() => void confirmAcknowledgement()}
              >
                {pending ? (
                  <LoadingSpinner size="sm" label={copy.apply.applying} />
                ) : null}
                {copy.apply.acknowledgeRestarted}
              </button>
            ) : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
