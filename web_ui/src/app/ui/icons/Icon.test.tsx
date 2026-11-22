import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Icon, type IconSize } from "./Icon";
import { iconDefinitions, iconNames, iconRegistry, type GlyphPrimitive } from "./registry";
import { chromeGlyphs } from "./glyphs/chrome";
import iconsSection from "../gallery/sections/Icons";

const allowedPrimitives = new Set(["path", "circle", "rect", "line", "polyline"]);
const coordinateNumbers = (s: string): number[] =>
  [...s.matchAll(/-?\d+(?:\.\d+)?/g)].map((match) => Number(match[0]));

function checkCoordinate(value: number) {
  expect(Number.isFinite(value)).toBe(true);
  expect(value).toBeGreaterThanOrEqual(1.5);
  expect(value).toBeLessThanOrEqual(18.5);
  expect(value * 2).toBe(Math.round(value * 2));
}

function checkPrimitive(primitive: GlyphPrimitive) {
  expect(allowedPrimitives.has(primitive.type)).toBe(true);
  switch (primitive.type) {
    case "path":
      expect(primitive.d).toMatch(/^M /);
      expect(primitive.d).not.toMatch(/[^MLHVQCZ0-9.\s-]/);
      coordinateNumbers(primitive.d).forEach(checkCoordinate);
      break;
    case "polyline":
      expect(coordinateNumbers(primitive.points).length % 2).toBe(0);
      coordinateNumbers(primitive.points).forEach(checkCoordinate);
      break;
    case "line":
      [primitive.x1, primitive.y1, primitive.x2, primitive.y2].forEach(checkCoordinate);
      break;
    case "circle":
      [primitive.cx, primitive.cy].forEach(checkCoordinate);
      expect(primitive.r).toBeGreaterThan(0);
      expect(primitive.cx - primitive.r).toBeGreaterThanOrEqual(1.5);
      expect(primitive.cy - primitive.r).toBeGreaterThanOrEqual(1.5);
      expect(primitive.cx + primitive.r).toBeLessThanOrEqual(18.5);
      expect(primitive.cy + primitive.r).toBeLessThanOrEqual(18.5);
      if (primitive.fill) expect(primitive.r).toBeLessThanOrEqual(1.25);
      break;
    case "rect":
      [primitive.x, primitive.y].forEach(checkCoordinate);
      expect(primitive.width).toBeGreaterThanOrEqual(2);
      expect(primitive.height).toBeGreaterThanOrEqual(2);
      checkCoordinate(primitive.x + primitive.width);
      checkCoordinate(primitive.y + primitive.height);
      expect(primitive.rx).toBe(Math.min(primitive.width, primitive.height) < 8 ? 1.5 : 2);
      expect(primitive.fill).toBeUndefined();
      break;
  }
}

describe("icon registry", () => {
  it("includes the chrome batch and allows later batches to extend the registry", () => {
    expect(Object.keys(chromeGlyphs).length).toBe(73); // 72 catalog names plus the send alias.
    expect(iconNames).toEqual(expect.arrayContaining(Object.keys(chromeGlyphs)));
    expect(new Set(iconNames).size).toBe(iconNames.length);
  });

  it.each(iconNames)("%s renders a 20-unit SVG from real primitives", (name) => {
    const { container } = render(<Icon name={name} />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 20 20");
    expect(svg).toHaveAttribute("fill", "none");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveAttribute("stroke-width", "1.5");
    expect(svg).toHaveAttribute("stroke-linecap", "round");
    expect(svg).toHaveAttribute("stroke-linejoin", "round");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveClass("gl-icon", "gl-icon--md");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).not.toHaveAttribute("role");
    expect(svg).not.toHaveAttribute("aria-label");
    expect(svg?.children.length).toBeGreaterThan(0);
    expect(svg?.children.length).toBe(iconRegistry[name].length);
    for (const child of Array.from(svg?.children ?? [])) {
      expect(allowedPrimitives.has(child.tagName.toLowerCase())).toBe(true);
    }
  });

  it("only stores the five primitive kinds, with coordinates inside the safe area", () => {
    for (const [name, glyph] of Object.entries(iconRegistry)) {
      expect(glyph.length, name).toBeGreaterThan(0);
      for (const primitive of glyph) checkPrimitive(primitive);
    }
  });

  it("stores geometry without raw colours, CSS properties or glyph-specific stroke weights", () => {
    const data = JSON.stringify(iconDefinitions);
    expect(data).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|currentColor|stroke|(?:black|white|red|blue)/i);
    for (const glyph of Object.values(iconRegistry)) {
      for (const primitive of glyph) {
        expect("stroke" in primitive || "color" in primitive || "strokeWidth" in primitive).toBe(false);
      }
    }
  });

  it("resolves every alias to its target's concrete primitive array", () => {
    const aliases = Object.entries(iconDefinitions).filter(([, definition]) => "alias" in definition);
    expect(aliases.length).toBeGreaterThan(0);
    for (const [name, definition] of aliases) {
      if (!("alias" in definition)) continue;
      expect(iconNames).toContain(definition.alias);
      expect(iconRegistry[name as keyof typeof iconRegistry])
        .toBe(iconRegistry[definition.alias as keyof typeof iconRegistry]);
    }
    expect(iconRegistry.send).toBe(iconRegistry["arrow-up"]);
  });
});

describe("Icon component", () => {
  it("keeps decorative icons hidden but makes explicitly labelled icons accessible", () => {
    const { rerender } = render(<Icon name="search" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    rerender(<Icon name="search" label="Search all chats" />);
    expect(screen.getByRole("img", { name: "Search all chats" })).toHaveAttribute("focusable", "false");
    expect(screen.getByRole("img", { name: "Search all chats" })).not.toHaveAttribute("aria-hidden");
  });

  it("passes custom classes and fills only explicit filled variants or small dots", () => {
    const { container, rerender } = render(<Icon name="star-filled" className="my-custom-class" />);
    expect(container.querySelector("svg")).toHaveClass("my-custom-class");
    expect(container.querySelector("path")).toHaveAttribute("fill", "currentColor");
    expect(container.querySelector("path")).toHaveAttribute("stroke", "none");
    rerender(<Icon name="star" />);
    expect(container.querySelector("path")).not.toHaveAttribute("fill", "currentColor");
    rerender(<Icon name="pin-filled" />);
    expect(container.querySelector("path")).toHaveAttribute("fill", "currentColor");
    rerender(<Icon name="more-horizontal" />);
    for (const dot of Array.from(container.querySelectorAll("circle"))) {
      expect(dot).toHaveAttribute("fill", "currentColor");
      expect(dot).toHaveAttribute("stroke", "none");
    }
  });

  const sizes: { size: IconSize; token: string; fallback: number }[] = [
    { size: "xs", token: "xs", fallback: 12 },
    { size: "sm", token: "sm", fallback: 14 },
    { size: "md", token: "md", fallback: 16 },
    { size: "lg", token: "lg", fallback: 20 },
    { size: "xl", token: "xl", fallback: 24 },
    { size: "2xl", token: "2xl", fallback: 32 },
    { size: "3xl", token: "3xl", fallback: 40 },
  ];
  const css = readFileSync(new URL("./icon.css", import.meta.url), "utf8");
  it.each(sizes)("uses the $size size class and --gl-icon-$token token with a $fallback px fallback", ({ size, token, fallback }) => {
    const { container } = render(<Icon name="check" size={size} />);
    expect(container.querySelector("svg")).toHaveClass("gl-icon--" + size);
    expect(css).toContain(".gl-icon--" + size + " {");
    expect(css).toContain("var(--gl-icon-" + token + ", " + fallback + "px)");
  });
});

describe("gallery icon baselines", () => {
  it("shows every icon at the review sizes and can overlay all four keylines", () => {
    const Component = iconsSection.Component;
    const { container } = render(<Component theme="dark" />);
    expect(container.querySelectorAll(".gl-icon-gallery-card").length).toBe(iconNames.length);
    expect(container.querySelectorAll(".gl-icon-gallery-size").length).toBe(iconNames.length * 4);
    expect(container.querySelectorAll(".gl-icon-guide-grid").length).toBe(0);

    const toggle = screen.getByRole("button", { name: "Show keylines" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide keylines" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelectorAll(".gl-icon-guide-grid").length).toBe(iconNames.length);
    expect(container.querySelectorAll(".gl-icon-guide-circle").length).toBe(iconNames.length);
    expect(container.querySelectorAll(".gl-icon-guide-square").length).toBe(iconNames.length);
    expect(container.querySelectorAll(".gl-icon-guide-portrait").length).toBe(iconNames.length);
    expect(container.querySelectorAll(".gl-icon-guide-landscape").length).toBe(iconNames.length);
  });
});
