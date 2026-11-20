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
  const [probeAttempt, setProbeAttempt] = useState(0);
  // Only the result for the current store and attempt can leave "checking".
  // A retry or store change resets the displayed status without effect-time setState.
  const [probe, setProbe] = useState<{
    store: SceneStore;
    attempt: number;
    engine: string | null;
    failed: boolean;
  } | null>(null);
  const currentProbe = probe?.store === store && probe.attempt === probeAttempt ? probe : null;
  const engine = currentProbe?.engine;
  const probeFailed = currentProbe?.failed ?? false;
  useEffect(() => {
    let active = true;
    void store.getHarnessIsolationEngine()
      .then((found) => {
        if (active) setProbe({ store, attempt: probeAttempt, engine: found, failed: false });
      })
      .catch(() => {
        if (active) setProbe({ store, attempt: probeAttempt, engine: null, failed: true });
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
