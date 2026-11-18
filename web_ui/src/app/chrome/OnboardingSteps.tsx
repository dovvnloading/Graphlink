// The first-run wizard's UI loads only when it actually opens. The launcher
// stays mounted and subscribed so the initial app-settings snapshot is never missed.
import type { Dispatch, SetStateAction } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { AppComposerRoute } from "../../lib/bridge-core/generated/app-composer-state";
import { Dialog, useOverlays } from "../overlays/overlays";

const STEP_COUNT = 4;

function providerCheckMessage(route: AppComposerRoute | null): string {
  if (route === null) return "Checking your configured provider…";
  if (route.available) return `${route.label} is ready - your first message will get a real response.`;
  return `${route.label} isn't ready yet - it needs a model or API key configured before it can respond.`;
}

export function OnboardingSteps({ step, setStep, providerMode, route, store, openProviderSettings }: {
  step: number;
  setStep: Dispatch<SetStateAction<number>>;
  providerMode: string;
  route: AppComposerRoute | null;
  store: SceneStore;
  openProviderSettings: (mode: string) => void;
}) {
  const overlays = useOverlays();
  return (
    <Dialog name="onboarding" title="Welcome to Graphlink" className="onboarding-dialog">
      <p className="onboarding-step-label">
        Step {step + 1} of {STEP_COUNT}
      </p>

      {step === 0 && (
        <div className="onboarding-step">
          <p>
            Graphlink is a visual AI workspace: every message becomes a node on a canvas, and you can branch a
            conversation from any earlier node instead of only ever continuing the last one. When a task needs more
            than one step, the Builder can plan and construct a whole branch for you, supervised at whatever level
            you choose.
          </p>
          <p>This short wizard checks your provider and offers a small sample workspace, then gets out of the way.</p>
        </div>
      )}

      {step === 1 && (
        <div className="onboarding-step">
          <p data-level={route && !route.available ? "warning" : undefined} className="settings-update-status">
            {providerCheckMessage(route)}
          </p>
          {route && !route.available && (
            <div className="settings-button-row">
              <button type="button" className="settings-button" onClick={() => openProviderSettings(providerMode)}>
                Open Settings
              </button>
              {providerMode !== "API Endpoint" && (
                <button type="button" className="settings-button" onClick={() => openProviderSettings("API Endpoint")}>
                  Connect a cloud provider
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="onboarding-step">
          <p>
            Load a small sample workspace - a note explaining Graphlink plus a short chat exchange - to see how nodes
            and branches look before you start your own.
          </p>
          <button type="button" className="settings-button" onClick={() => store.loadSampleWorkspace()}>
            Load Sample Workspace
          </button>
        </div>
      )}

      {step === 3 && (
        <div className="onboarding-step">
          <p>You're set. Type a message in the composer below to send your first real request.</p>
        </div>
      )}

      {/* Reuses .settings-button-row/.settings-button(-primary) wholesale
          (SettingsDialog.tsx's own CSS) rather than a parallel button
          language just for this dialog - the primary action's own
          margin-left:auto is what right-aligns it away from Back. */}
      <div className="settings-button-row">
        <button type="button" className="settings-button" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          Back
        </button>
        {step < STEP_COUNT - 1 ? (
          <button type="button" className="settings-button settings-button-primary" onClick={() => setStep((s) => s + 1)}>
            Next
          </button>
        ) : (
          <button type="button" className="settings-button settings-button-primary" onClick={overlays.close}>
            Done
          </button>
        )}
      </div>
    </Dialog>
  );
}
