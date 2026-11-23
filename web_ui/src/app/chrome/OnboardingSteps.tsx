// Loaded only when onboarding opens; provider and scene actions stay in the shared shell.
import type { Dispatch, SetStateAction } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { AppComposerRoute } from "../../lib/bridge-core/generated/app-composer-state";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Icon } from "../ui/icons/Icon";

const STEPS = ["Welcome", "Model", "Sample", "Ready"] as const;

export function OnboardingSteps({ step, setStep, providerMode, route, store, openProviderSettings, startOllama, ollamaServerStatus }: {
  step: number;
  setStep: Dispatch<SetStateAction<number>>;
  providerMode: string;
  route: AppComposerRoute | null;
  store: SceneStore;
  openProviderSettings: (mode: string) => void;
  startOllama: () => void;
  ollamaServerStatus: string;
}) {
  const overlays = useOverlays();
  const modelReady = route?.available === true;

  return (
    <Dialog name="onboarding" title="Welcome to Graphlink" size="md" className="onboarding-dialog"
      status={<Button size="sm" variant="ghost" onClick={overlays.close}>Skip setup</Button>}
      footer={<>
        {step > 0 && <Button variant="secondary" onClick={() => setStep((value) => value - 1)}>Back</Button>}
        {step < STEPS.length - 1
          ? <Button variant="primary" onClick={() => setStep((value) => value + 1)}>Next</Button>
          : <Button variant="primary" onClick={overlays.close}>Finish</Button>}
      </>}>
      <ol className="onboarding-stepper" aria-label="Setup progress">
        {STEPS.map((label, index) => (
          <li key={label} className={index === step ? "onboarding-step-active" : ""}
            aria-current={index === step ? "step" : undefined}>
            <span className="onboarding-step-number">{index < step ? <Icon name="check" size="sm" /> : index + 1}</span>
            <span>{label}</span>
          </li>
        ))}
      </ol>
      <section className="onboarding-step" aria-label={STEPS[step]}>
        {step === 0 && <>
          <h3>A canvas for ideas that grow</h3>
          <p>Every message becomes a node you can revisit, branch, and build on. Explore the canvas, connect knowledge, or ask the Builder to plan a multi-step job.</p>
          <p>This setup checks your model and offers a sample workspace. You can skip at any time.</p>
        </>}
        {step === 1 && <>
          <h3>Choose a model</h3>
          <Card title={route?.label ?? (providerMode || "Model connection")}
            status={route === null ? "Running" : modelReady ? "Done" : "Failed"}>
            <p role="status">{route === null
              ? "Checking your configured provider…"
              : modelReady
                ? `${route.label} is ready. Your first message can get a response.`
                : `${route.label} isn't ready yet. Configure a model or API key before sending.`}</p>
            <div className="onboarding-model-actions">
              {!modelReady && <>
                <Button variant="secondary" size="sm" iconStart="play" onClick={startOllama}
                  disabled={ollamaServerStatus !== "installed"}>
                  Start Ollama
                </Button>
                <Button variant="secondary" size="sm" iconStart="cloud"
                  onClick={() => openProviderSettings("API Endpoint")}>Connect a cloud provider</Button>
              </>}
              <Button variant="ghost" size="sm" iconStart="settings"
                onClick={() => openProviderSettings(providerMode)}>Open settings</Button>
            </div>
            {!modelReady && ollamaServerStatus !== "installed" && <p>
              To start Ollama from Graphlink, install it first and check its status in settings.
            </p>}
          </Card>
        </>}
        {step === 2 && <>
          <h3>Try the sample workspace</h3>
          <p>Load a short example with a note and a chat exchange. You can see how nodes and branches work before making your own.</p>
          <Button variant="secondary" iconStart="workspace" onClick={() => store.loadSampleWorkspace()}>
            Load sample workspace
          </Button>
        </>}
        {step === 3 && <>
          <h3>You're ready to explore</h3>
          <p>{modelReady ? "You're set." : "You can explore now. Add a model before you send your first message."}</p>
          {!modelReady && <Button variant="secondary" iconStart="settings" onClick={() => openProviderSettings(providerMode)}>
            Set up a model
          </Button>}
        </>}
      </section>
    </Dialog>
  );
}
