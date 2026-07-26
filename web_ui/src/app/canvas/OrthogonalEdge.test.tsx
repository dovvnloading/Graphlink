import { ReactFlowProvider } from "@xyflow/react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OrthogonalEdge } from "./OrthogonalEdge";

// R7.5b-2: verifies the exact mid-Y step-path formula this component ports
// from legacy's GroupSummaryConnectionItem shape (see this file's own module
// doc for why the mid-Y variant, not legacy's default mid-X variant, is the
// correct translation for this app's top-to-bottom node layout).
function renderEdge(props: Partial<React.ComponentProps<typeof OrthogonalEdge>> = {}) {
  const { container } = render(
    <ReactFlowProvider>
      <svg>
        <OrthogonalEdge
          {...({
            id: "e1",
            source: "a",
            target: "b",
            sourceX: 100,
            sourceY: 50,
            targetX: 300,
            targetY: 250,
            sourcePosition: "bottom",
            targetPosition: "top",
            ...props,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any)}
        />
      </svg>
    </ReactFlowProvider>,
  );
  return container.querySelector(".react-flow__edge-path") as SVGPathElement;
}

describe("OrthogonalEdge", () => {
  it("renders a 3-segment mid-Y step path: source -> midY -> midY -> target", () => {
    const path = renderEdge({ sourceX: 100, sourceY: 50, targetX: 300, targetY: 250 });
    // midY = 50 + (250-50)/2 = 150
    expect(path.getAttribute("d")).toBe("M 100,50 L 100,150 L 300,150 L 300,250");
  });

  it("recomputes the midpoint correctly for a different geometry", () => {
    const path = renderEdge({ sourceX: 0, sourceY: 0, targetX: 40, targetY: 400 });
    expect(path.getAttribute("d")).toBe("M 0,0 L 0,200 L 40,200 L 40,400");
  });

  it("forwards the style prop, so it composes with faded connections' opacity override", () => {
    const path = renderEdge({ style: { opacity: 0.08 } });
    expect(path.style.opacity).toBe("0.08");
  });
});
