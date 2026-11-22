/** An overlay targets an existing node; choosing an operation never executes it. */
const PREFIX = "scientific-workbench/";

export function scientificFocusName(nodeId: string, operationId = ""): string {
  return PREFIX + encodeURIComponent(nodeId) + "/" + encodeURIComponent(operationId);
}

export function readScientificFocus(name: string | null) {
  if (!name?.startsWith(PREFIX)) return null;
  const [nodeId, operationId] = name.slice(PREFIX.length).split("/");
  return { nodeId: decodeURIComponent(nodeId), operationId: decodeURIComponent(operationId || "") };
}
