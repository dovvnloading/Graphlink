import { Background, BackgroundVariant, useStore } from "@xyflow/react";
import type { GridControlState } from "../../lib/bridge-core/generated/grid-control-state";

const variants: Record<string, BackgroundVariant> = {
  Dots: BackgroundVariant.Dots,
  Lines: BackgroundVariant.Lines,
  Cross: BackgroundVariant.Cross,
};

/** Thin a dense grid in whole steps so its visible marks still coincide
 * with the user's snap interval as they zoom out. */
export function CanvasGrid({ grid }: { grid: GridControlState }) {
  const stride = useStore((state) => {
    const spacing = Math.max(1, grid.gridSize) * state.transform[2];
    return 2 ** Math.max(0, Math.ceil(Math.log2(12 / spacing)));
  });
  const gap = grid.gridSize * stride;
  const opacity = grid.gridOpacityPercent / 100;
  const variant = variants[grid.gridStyle] ?? BackgroundVariant.Dots;
  return (
    <Background
      id="grid"
      variant={variant}
      gap={gap}
      color={grid.gridColor}
      size={variant === BackgroundVariant.Cross ? 5 : 1.2}
      style={{ opacity }}
    />
  );
}
