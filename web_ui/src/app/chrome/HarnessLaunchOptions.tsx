import { useEffect, useState } from "react";
import type { SceneStore } from "../canvas/sceneStore";

export { MAX_HARNESS_TASK_LENGTH, exceedsHarnessTaskLimit } from "../../lib/harnessTaskLimit";
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
  // undefined: checking; null: no CLI found or the probe failed.
  const [engine, setEngine] = useState<string | null | undefined>(undefined);
  const [probeFailed, setProbeFailed] = useState(false);
  const [probeAttempt, setProbeAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setEngine(undefined);
    setProbeFailed(false);
    void store.getHarnessIsolationEngine()
      .then((found) => { if (active) setEngine(found); })
      .catch(() => {
        if (active) {
          setEngine(null);
          setProbeFailed(true);
        }
      });
    return () => { active = false; };
  }, [store, probeAttempt]);

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
            : probeFailed
              ? "Could not check Docker or Podman availability. Check the connection and retry."
              : "Docker or Podman is unavailable on the backend; Isolated cannot start."}
      </span>
      {engine !== undefined && !engine && (
        <button
          type="button"
          className="builder-launch-secondary"
          disabled={disabled}
          onClick={() => setProbeAttempt((attempt) => attempt + 1)}
        >
          Recheck Docker / Podman
        </button>
      )}
    </div>
  );
}
