import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { ExecutionLimitsState } from "../../lib/bridge-core/generated/execution-limits-state";

/**
 * ADR-005 stage 5.4 (disclosure half): the real, backend-computed resource
 * caps CodeExecutionApprovalPanel.tsx discloses before a human approves code
 * execution - see backend/execution_limits.py's own docstring for why this
 * must be backend-computed rather than a hardcoded frontend string.
 *
 * Threaded via Context rather than prop-drilling through
 * PyCoderNodeView/CodeSandboxNodeView and @xyflow/react's own per-node
 * `data` mapping (SceneCanvas.tsx's toFlowNodes): the caps are NOT per-node
 * state (every pycoder/code_sandbox node shares the identical, session-wide
 * limits), so carrying them on every node's own `data` object would
 * duplicate the same static strings across however many nodes exist, for a
 * value that is really global. Mirrors AboutDialog.tsx's own "no client
 * store class needed for a single read-only, never-mutated topic" subscribe
 * pattern exactly - just exposed via Context instead of being read directly
 * inside one dialog component, since this value needs to reach a component
 * nested deep inside the canvas tree instead.
 */

const initialState: ExecutionLimitsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  pycoderResourceLimitsText: "",
  codeSandboxResourceLimitsText: "",
};

const ExecutionLimitsContext = createContext<ExecutionLimitsState>(initialState);

export function ExecutionLimitsProvider({
  transport,
  children,
}: {
  transport: WsTransport;
  children: ReactNode;
}) {
  const [state, setState] = useState<ExecutionLimitsState>(initialState);

  useEffect(() => {
    return transport.subscribe("execution-limits", (payload) => {
      const validated = TOPIC_VALIDATORS["execution-limits"](payload);
      if (validated.ok) setState(validated.value as ExecutionLimitsState);
      else console.error("[execution-limits] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  return <ExecutionLimitsContext.Provider value={state}>{children}</ExecutionLimitsContext.Provider>;
}

/** Falls back to blank strings (initialState) when read outside a Provider -
 * e.g. CodeExecutionApprovalPanel.test.tsx's existing standalone renders,
 * which mount the panel with no ancestor tree at all. CodeExecutionApprovalPanel
 * itself only renders the resource-limits paragraph when the text is
 * non-blank (mirrors its own existing showRequirements pattern), so a
 * missing Provider degrades to "no addendum shown" rather than an error. */
export function useExecutionLimits(): ExecutionLimitsState {
  return useContext(ExecutionLimitsContext);
}
