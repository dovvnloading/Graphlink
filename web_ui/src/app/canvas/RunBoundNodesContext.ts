import { createContext } from "react";

/** Opens Graph Runner on a specific set of bound nodes. This never executes them. */
export const RunBoundNodesContext = createContext<((ids: string[]) => void) | null>(null);
