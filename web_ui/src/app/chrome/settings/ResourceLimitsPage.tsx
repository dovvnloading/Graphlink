import { useExecutionLimits } from "../../canvas/ExecutionLimitsContext";
import { Card } from "../../ui/Card";
import { EmptyState } from "../../ui/EmptyState";

export function ResourceLimitsPage() {
  const { codeSandboxResourceLimitsText } = useExecutionLimits();
  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">Resource limits</h2>
      <p className="settings-page-description">
        System-computed limits applied to AI-driven code execution. These are also displayed before approving a run.
      </p>
      {codeSandboxResourceLimitsText ? (
        <Card title="Execution sandbox">
          <dl className="settings-key-value">
            <dt>Virtual environment runner</dt>
            <dd className="settings-preserve-lines">{codeSandboxResourceLimitsText}</dd>
          </dl>
        </Card>
      ) : (
        <EmptyState icon="shield" title="Limits unavailable"
          body="No resource limits have been reported for this system yet." />
      )}
    </div>
  );
}
