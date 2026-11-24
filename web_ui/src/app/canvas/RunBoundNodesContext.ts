import { createContext } from "react";
import type { WorkflowScope } from "./workflowRunTypes";

/** Opens Graph Runner on a specific set of bound nodes. This never executes them. */
export const RunBoundNodesContext = createContext<((ids: string[], scope?: WorkflowScope) => void) | null>(null);
