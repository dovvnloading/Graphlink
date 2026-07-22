import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppAboutState } from "../../lib/bridge-core/generated/app-about-state";
import { Dialog } from "../overlays/overlays";

/**
 * The About dialog (Qt-removal plan R2.5) - about-web's SPA successor.
 *
 * The simplest surface in the migration (confirmed by recon): zero live
 * state, one action (open an external link). No client store class is
 * needed for a single read-only, never-mutated topic - this component
 * subscribes directly. Links use plain <a target="_blank"> - a browser tab,
 * not Python's webbrowser.open() (there is no Python-owned window here to
 * delegate to; the SPA already runs inside a real browser engine).
 */

const initialState: AppAboutState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  appName: "Graphlink",
  appVersion: "",
  repositoryUrl: "",
  developerName: "",
  developerWebsiteUrl: "",
  developerGithubUrl: "",
  copyrightText: "",
};

export function AboutDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<AppAboutState>(initialState);

  useEffect(() => {
    return transport.subscribe("app-about", (payload) => {
      const validated = TOPIC_VALIDATORS["app-about"](payload);
      if (validated.ok) setState(validated.value as AppAboutState);
      else console.error("[app-about] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  return (
    <Dialog name="about" title="About" className="about-dialog">
      <p className="about-app-name">{state.appName}</p>
      <p className="about-version">{state.appVersion}</p>
      <div className="about-links">
        <a href={state.repositoryUrl} target="_blank" rel="noreferrer">
          Repository
        </a>
        <a href={state.developerWebsiteUrl} target="_blank" rel="noreferrer">
          {state.developerName}
        </a>
        <a href={state.developerGithubUrl} target="_blank" rel="noreferrer">
          Developer GitHub
        </a>
      </div>
      <p className="about-copyright">{state.copyrightText}</p>
    </Dialog>
  );
}
