import type { Node, NodeProps } from "@xyflow/react";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

export function PlaceholderNodeView({ data, selected }: NodeProps<Node<{ title: string }, "placeholder">>) {
  const collapsed = useLodVisibility();
  return (
    <NodeShell kindClassName="placeholder-node" selected={!!selected} collapsed={collapsed}
      header={<NodeHeading>{data.title}</NodeHeading>} bodyClassName="placeholder-node-body">
      Placeholder node
    </NodeShell>
  );
}
