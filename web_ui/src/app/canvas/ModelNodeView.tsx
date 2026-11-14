import { type Node, type NodeProps } from "@xyflow/react";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";

export interface ModelNodeData extends Record<string, unknown> {
  title: string;
  sourceLabel: string;
  content: string;
}

export type ModelFlowNode = Node<ModelNodeData, "model">;

/** An actual executable Model node, not a cosmetic label on a plain note.
 *
 * The named ports and provider configuration are backend-owned. This card
 * exposes the last committed output, never an inferred or pending reply.
 * Execution is deliberately available only from Graph Runner's reviewed
 * preview/run flow, not from an incidental drag or content edit.
 */
export function ModelNodeView({ data, selected }: NodeProps<ModelFlowNode>) {
  return <NodeShell
    kindClassName="model-node"
    selected={!!selected}
    collapsed={false}
    modelPort
    textOutput
    header={<NodeHeading><span>{data.title}</span></NodeHeading>}
    bodyClassName="model-node-body"
  >
    <div className="model-node-port-summary">Text input · {data.sourceLabel || "Disconnected"}</div>
    <div className="model-node-output-label">Text output · Last committed result</div>
    <div className="model-node-output">{data.content
      ? data.content.slice(0, 800) + (data.content.length > 800 ? "…" : "")
      : "Not run yet. Preview and explicitly run this branch in Graph Runner."}</div>
  </NodeShell>;
}
