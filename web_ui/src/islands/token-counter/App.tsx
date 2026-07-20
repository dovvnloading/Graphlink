import { useEffect, useRef, useState } from "react";
import { TokenCounterState, initialTokenCounterState } from "./bridgeTypes";
import { BridgeRejection, TokenCounterBridge, createTokenCounterBridge } from "./bridge";
import { TokenCounterErrorState } from "./TokenCounterErrorState";

type CountField = "inputTokens" | "outputTokens" | "contextTokens" | "totalTokens";

const ROWS: Array<{ key: CountField; label: string }> = [
  { key: "inputTokens", label: "Input:" },
  { key: "outputTokens", label: "Output:" },
  { key: "contextTokens", label: "Context:" },
  { key: "totalTokens", label: "Total:" },
];

function App() {
  const [state, setState] = useState<TokenCounterState>(initialTokenCounterState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<TokenCounterBridge | null>(null);

  useEffect(() => {
    const bridge = createTokenCounterBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return <TokenCounterErrorState rejection={rejection} />;
  }

  return (
    <div className="token-counter-shell">
      {ROWS.map(({ key, label }) => (
        <div className="token-counter-row" key={key}>
          <span className="token-counter-label">{label}</span>
          <span className="token-counter-value">{state[key].toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

export default App;
