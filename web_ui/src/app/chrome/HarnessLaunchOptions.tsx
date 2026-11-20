import { useEffect, useState } from "react";
import type { SceneStore } from "../canvas/sceneStore";

// Keep in sync with backend/api/intents_harness.py's untrusted wire-input bound.
export const MAX_HARNESS_TASK_LENGTH = 20_000;
export type HarnessExecutionProfile = "host" | "isolated";

/** A first-run choice, checked against the backend host (not the browser).
 * CLI detection alone does not guarantee a ready daemon/image; execution
 * still fails closed without ever falling back to Host. */
export function HarnessExecutionProfileSelect({
  store, value, onChange, disabled = false,
}: {
  store: SceneStore;
  value: HarnessExecutionProfile;
  onChange: (profile: HarnessExecutionProfile) => void;
  disabled?: boolean;
}) {
  // undefined: checking; null: no Docker/Podman CLI on the backend PATH.
  const [engine, setEngine] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    void store.getHarnessIsolationEngine()
      .then((found) => { if (active) setEngine(found); })
      .catch(() => { if (active) setEngine(null); });
    return () => { active = false; };
  }, [store]);

  return (
    <div className="harness-launch-profile">
      <label>
        <span>Execution profile</span>
        <select
          aria-label="Execution profile for new agent"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value as HarnessExecutionProfile)}
        >
          <option value="host">Host (default)</option>
          <option value="isolated" disabled={!engine}>Isolated (Docker / Podman)</option>
        </select>
      </label>
      <span className="harness-launch-profile-status" role="status">
        {engine === undefined
          ? "Checking Docker / Podman availability…"
          : engine
            ? `${engine === "docker" ? "Docker" : "Podman"} detected on the backend; Isolated never falls back to Host.`
            : "Docker or Podman is unavailable on the backend; Isolated cannot start."}
      </span>
    </div>
  );
}
