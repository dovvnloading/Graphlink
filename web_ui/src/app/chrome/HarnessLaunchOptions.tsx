import { useEffect, useState } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { InlineAlert } from "../ui/InlineAlert";
import { Select } from "../ui/Select";

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
      <Select label="Execution profile" ariaLabel="Execution profile for new agent"
        value={value} disabled={disabled}
        onChange={(next) => onChange(next as HarnessExecutionProfile)}
        options={[
          { value: "host", label: "Host (default)", description: "Run with per-action approval on this machine." },
          { value: "isolated", label: "Isolated (Docker / Podman)", description: "Require a container engine on the backend.", disabled: !engine },
        ]} />
      {engine === undefined ? <p role="status">Checking Docker / Podman availability…</p> :
        engine ? <p role="status">{engine === "docker" ? "Docker" : "Podman"} detected. Isolated never falls back to Host.</p> :
        <InlineAlert variant="warning" title="Isolation unavailable"
          body={probeFailed
            ? "Docker or Podman isn't available, so Isolated mode can't start. The connection check failed."
            : "Docker or Podman isn't available, so Isolated mode can't start."}
          action={{ label: "Check again", onClick: () => setProbeAttempt((attempt) => attempt + 1) }} />
      }
    </div>
  );
}
