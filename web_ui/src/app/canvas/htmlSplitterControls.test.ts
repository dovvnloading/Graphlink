import { describe, expect, it } from "vitest";
import { HTML_SPLIT_MAX, HTML_SPLIT_MIN } from "./canvasConstants";
import { nextHtmlSplitterKeyboardValue } from "./htmlSplitterControls";

describe("nextHtmlSplitterKeyboardValue", () => {
  it("resizes by 5% using the arrow keys, with source height growing downward", () => {
    expect(nextHtmlSplitterKeyboardValue(0.5, "ArrowDown")).toBe(0.55);
    expect(nextHtmlSplitterKeyboardValue(0.5, "ArrowUp")).toBe(0.45);
    expect(nextHtmlSplitterKeyboardValue(0.55, "ArrowUp")).toBe(0.5);
  });

  it("supports PageUp/PageDown and Home/End boundaries", () => {
    expect(nextHtmlSplitterKeyboardValue(0.5, "PageDown")).toBe(0.7);
    expect(nextHtmlSplitterKeyboardValue(0.5, "PageUp")).toBe(0.3);
    expect(nextHtmlSplitterKeyboardValue(0.5, "Home")).toBe(HTML_SPLIT_MIN);
    expect(nextHtmlSplitterKeyboardValue(0.5, "End")).toBe(HTML_SPLIT_MAX);
  });

  it("clamps and returns the same value at a bound rather than overstepping it", () => {
    expect(nextHtmlSplitterKeyboardValue(HTML_SPLIT_MIN, "ArrowUp")).toBe(HTML_SPLIT_MIN);
    expect(nextHtmlSplitterKeyboardValue(HTML_SPLIT_MAX, "PageDown")).toBe(HTML_SPLIT_MAX);
    expect(nextHtmlSplitterKeyboardValue(HTML_SPLIT_MAX - 0.01, "ArrowDown")).toBe(HTML_SPLIT_MAX);
  });

  it("returns null for unrelated keys", () => {
    expect(nextHtmlSplitterKeyboardValue(0.5, "Tab")).toBeNull();
    expect(nextHtmlSplitterKeyboardValue(0.5, "Enter")).toBeNull();
    expect(nextHtmlSplitterKeyboardValue(0.5, "Escape")).toBeNull();
  });
});
