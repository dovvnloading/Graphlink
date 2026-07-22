import { useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";

/**
 * The View popover (Qt-removal plan R2, audit P5): ONE surface consolidating
 * the drag-speed, grid-control, and font-control islands - their controls,
 * their presets (published by the backend), their intent names - instead of
 * three separately-positioned popover cards.
 */

export function ViewPopover({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);
  const dragConfig = useSyncExternalStore(store.subscribe, store.getDragConfig);
  const fontConfig = useSyncExternalStore(store.subscribe, store.getFontConfig);

  const dragPercent = Math.round(scene.dragFactor * 100);

  return (
    <Popover name="view" className="view-popover">
      <section className="view-section" aria-label="Drag speed">
        <p className="view-section-title">DRAG</p>
        <input
          type="range"
          className="view-slider"
          min={dragConfig.percentMin}
          max={dragConfig.percentMax}
          value={dragPercent}
          aria-label="Drag speed"
          onChange={(e) => store.setDragFactor(Number(e.target.value) / 100)}
        />
        <div className="view-row">
          {dragConfig.percentPresets.map((percent) => (
            <button
              key={percent}
              type="button"
              className={"view-preset-btn" + (percent === dragPercent ? " active" : "")}
              onClick={() => store.setDragFactor(percent / 100)}
            >
              {percent}%
            </button>
          ))}
        </div>
      </section>

      <section className="view-section" aria-label="Grid">
        <p className="view-section-title">GRID</p>
        <input
          type="range"
          className="view-slider"
          min={0}
          max={100}
          value={grid.gridOpacityPercent}
          aria-label="Grid opacity"
          onChange={(e) => store.setGridOpacityPercent(Number(e.target.value))}
        />
        <div className="view-row">
          {grid.sizePresets.map((size) => (
            <button
              key={size}
              type="button"
              className={"view-preset-btn" + (size === grid.gridSize ? " active" : "")}
              onClick={() => store.setGridSize(size)}
            >
              {size}px
            </button>
          ))}
        </div>
        <div className="view-row">
          {grid.stylePresets.map((style) => (
            <button
              key={style}
              type="button"
              className={"view-preset-btn" + (style === grid.gridStyle ? " active" : "")}
              onClick={() => store.setGridStyle(style)}
            >
              {style}
            </button>
          ))}
        </div>
        <div className="view-row">
          {grid.colorPresets.map((color) => (
            <button
              key={color}
              type="button"
              className={"view-color-swatch" + (color === grid.gridColor ? " active" : "")}
              style={{ backgroundColor: color }}
              aria-label={`Grid color ${color}`}
              onClick={() => store.setGridColor(color)}
            />
          ))}
        </div>
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={scene.snapToGrid}
            onChange={(e) => store.setSnapToGrid(e.target.checked)}
          />
          Snap to Grid
        </label>
      </section>

      <section className="view-section" aria-label="Font">
        <p className="view-section-title">FONT</p>
        <select
          className="view-select"
          value={scene.fontFamily}
          aria-label="Font family"
          onChange={(e) => store.setFontFamily(e.target.value)}
        >
          {fontConfig.fontFamilies.map((family) => (
            <option key={family} value={family}>
              {family}
            </option>
          ))}
        </select>
        <input
          type="range"
          className="view-slider"
          min={fontConfig.sizeMin}
          max={fontConfig.sizeMax}
          value={scene.fontSizePt}
          aria-label="Font size"
          onChange={(e) => store.setFontSize(Number(e.target.value))}
        />
        <div className="view-row">
          {fontConfig.colorPresets.map((color) => (
            <button
              key={color}
              type="button"
              className={"view-color-swatch" + (color === scene.fontColor ? " active" : "")}
              style={{ backgroundColor: color }}
              aria-label={`Font color ${color}`}
              onClick={() => store.setFontColor(color)}
            />
          ))}
        </div>
      </section>
    </Popover>
  );
}
