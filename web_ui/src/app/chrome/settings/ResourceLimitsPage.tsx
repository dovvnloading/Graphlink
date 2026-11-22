import { useExecutionLimits } from "../../canvas/ExecutionLimitsContext";

// ADR-012 stage 12.6: a read-only disclosure surface, not a settings page in
// the usual sense - there is nothing here to edit (per ADR-005 §4, these
// caps are a fixed, backend-computed fact, not a user-tunable preference),
// so unlike every other page in this file there is no transport prop and no
// fireIntent call anywhere below. Sourced from useExecutionLimits()
// (ExecutionLimitsContext.tsx) - the SAME context CodeExecutionApprovalPanel.tsx
// already reads before a human approves a pending Execution Sandbox run -
// rather than a second copy of the strings, so this page and that panel can
// never drift out of sync with each other or with the backend's own
// platform-conditional computation (backend/execution_limits.py). Exists so
// the disclosure is reachable on its own, ahead of time, rather than only
// surfacing the first time a user is mid-approval and already under time
// pressure to decide.
export function ResourceLimitsPage() {
  const executionLimits = useExecutionLimits();
  const hasAnyText = !!executionLimits.codeSandboxResourceLimitsText;

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        The resource caps applied to AI-driven code execution, computed by the backend for this system. The same
        text is shown in the Virtual Environment Runner approval prompt before you approve a run -
        this page just makes it reachable without waiting for one.
      </p>

      {!!executionLimits.codeSandboxResourceLimitsText && (
        <div className="settings-field">
          <span className="settings-field-label">Virtual Environment Runner</span>
          <p className="settings-update-status">{executionLimits.codeSandboxResourceLimitsText}</p>
        </div>
      )}

      {!hasAnyText && (
        <p className="settings-update-status">No resource limits have been reported by the backend yet.</p>
      )}
    </div>
  );
}
