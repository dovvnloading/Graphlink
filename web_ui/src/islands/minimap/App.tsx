import { useEffect, useRef, useState } from "react";
import { MinimapState, initialMinimapState } from "./bridgeTypes";
import { BridgeRejection, MinimapBridge, createMinimapBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<MinimapState>(initialMinimapState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<MinimapBridge | null>(null);

  useEffect(() => {
    const bridge = createMinimapBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="The minimap is unavailable"
        rejection={rejection}
        className="minimap-shell bridge-error"
      />
    );
  }

  return (
    <div className="minimap-shell">
      <div className="minimap-list" role="list" aria-label="Conversation minimap">
        {state.nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            role="listitem"
            className={"minimap-indicator" + (node.isUser ? " user" : " ai")}
            title={node.preview}
            aria-label={node.preview}
            onClick={() => bridgeRef.current?.selectNode(node.id)}
          />
        ))}
      </div>
    </div>
  );
}

export default App;
