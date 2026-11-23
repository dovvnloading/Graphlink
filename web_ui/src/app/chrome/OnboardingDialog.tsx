import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { validateAppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { validateAppComposerState } from "../../lib/bridge-core/generated/app-composer-state";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { AppComposerRoute, AppComposerState } from "../../lib/bridge-core/generated/app-composer-state";
import type { SceneStore } from "../canvas/sceneStore";
import { Dialog, useOverlays } from "../overlays/overlays";
import "./OnboardingDialog.css";

const LazyOnboardingSteps = lazy(() =>
  import("./OnboardingSteps").then((m) => ({ default: m.OnboardingSteps })),
);

/**
 * ADR-012 stage 12.6: the first-run onboarding wizard - HelpDialog.tsx/
 * AboutDialog.tsx's own "simple informational dialog through the shared
 * <Dialog> wrapper" shape, extended with a little local step state (closer
 * to HelpDialog's active-section pattern than AboutDialog's zero-state one).
 *
 * Four steps: (a) welcome, (b) provider check, (c) offer to load the sample
 * workspace, (d) done. Deliberately thin - it GUIDES the user to existing
 * machinery rather than reimplementing any of it:
 * - provider readiness reuses app-composer's own `route.available` (backend/
 *   composer.py's `_live_route` - the SAME "is a provider actually usable
 *   right now" signal the composer's route control is built on, resolved
 *   through api_provider.sync_ollama_models/the GGUF path check/the API-key
 *   assignment exactly as a real Send would), not a second query.
 * - "not ready" opens Settings on the right page via setActiveSection +
 *   providerMode (also already on the wire) rather than duplicating any
 *   provider-setup UI here.
 * - "load the sample workspace" fires the SAME loadSampleWorkspace intent
 *   SceneCanvas.tsx's empty-canvas hint button does (sceneStore.ts).
 *
 * Persistence: hasCompletedOnboarding (app-settings) defaults false on a
 * fresh machine, which is what auto-opens this dialog exactly once per page
 * load (autoOpenedRef below). Closing it - by ANY means (Escape, scrim
 * click, the close button, or a "Done" click - overlays.close() is the one
 * thing all four funnel through) sets it true, so it never auto-shows
 * again; SettingsDialog.tsx's General page has a "Show Onboarding" button
 * that reopens it manually afterward, matching how Help's own AppBar chip
 * is a standing way back into that dialog.
 */

export function OnboardingDialog({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const isOpen = overlays.isOpen("onboarding");
  const [step, setStep] = useState(0);
  const [hasCompletedOnboarding, setHasCompletedOnboarding] = useState<boolean | null>(null);
  const [providerMode, setProviderMode] = useState("");
  const [ollamaServerStatus, setOllamaServerStatus] = useState("");
  const [route, setRoute] = useState<AppComposerRoute | null>(null);
  // Guards the auto-open to exactly once per page load - without it, every
  // later app-settings snapshot that still (briefly, before the dismissal
  // intent round-trips back) reads hasCompletedOnboarding===false would
  // reopen a dialog the user just closed.
  const autoOpenedRef = useRef(false);
  // Tracks whether THIS component saw the dialog open, so the effect below
  // can tell "isOpen just went false" (a real dismissal) apart from
  // "isOpen was already false" (ordinary re-renders, including the very
  // first one) - see that effect's own comment.
  const wasOpenRef = useRef(false);

  useEffect(() => {
    const offSettings = transport.subscribe("app-settings", (payload) => {
      const validated = validateAppSettingsState(payload);
      if (validated.ok) {
        const value = validated.value as AppSettingsState;
        setHasCompletedOnboarding(value.hasCompletedOnboarding);
        setProviderMode(value.providerMode);
        setOllamaServerStatus(value.ollamaServerStatus);
      } else {
        console.error("[app-settings] rejected snapshot:", validated.errors);
      }
    });
    const offComposer = transport.subscribe("app-composer", (payload) => {
      const validated = validateAppComposerState(payload);
      if (validated.ok) setRoute((validated.value as AppComposerState).route);
      else console.error("[app-composer] rejected snapshot:", validated.errors);
    });
    return () => {
      offSettings();
      offComposer();
    };
  }, [transport]);

  useEffect(() => {
    if (hasCompletedOnboarding === false && !autoOpenedRef.current) {
      autoOpenedRef.current = true;
      overlays.open("onboarding", "dialog");
    }
  }, [hasCompletedOnboarding, overlays]);

  useEffect(() => {
    if (isOpen) {
      wasOpenRef.current = true;
      return;
    }
    if (!wasOpenRef.current) return; // was already closed - nothing dismissed
    wasOpenRef.current = false;
    setStep(0); // fresh start if reopened later from Settings
    transport.fireIntent("app-settings", "setHasCompletedOnboarding", [true], undefined, true);
  }, [isOpen, transport]);

  const openProviderSettings = (mode: string) => {
    // providerMode is one of "Ollama (Local)" / "Llama.cpp (Local)" /
    // "API Endpoint" - lowercasing it matches SettingsDialog.tsx's own
    // sectionKey() output for exactly those 3 pages, so this lands on
    // whichever page the ACTIVE mode already needs, without duplicating
    // that mapping here.
    transport.fireIntent("app-settings", "setActiveSection", [mode.toLowerCase()], undefined, true);
    overlays.open("settings", "dialog"); // single-open: this also closes onboarding
  };

  return (
    isOpen ? (
      <Suspense fallback={
        <Dialog name="onboarding" title="Welcome to Graphlink" className="onboarding-dialog">
          <p role="status">Loading onboarding…</p>
        </Dialog>
      }>
        <LazyOnboardingSteps
          step={step} setStep={setStep} providerMode={providerMode} route={route}
          store={store} openProviderSettings={openProviderSettings}
          ollamaServerStatus={ollamaServerStatus}
          startOllama={() => transport.fireIntent("app-settings", "startOllama", [])}
        />
      </Suspense>
    ) : null
  );
}
