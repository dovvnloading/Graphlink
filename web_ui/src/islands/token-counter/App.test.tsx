import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

// jsdom has no window.QWebChannel, so createTokenCounterBridge() falls
// through to the mock bridge automatically - same pattern as
// ComposerApp.test.tsx.

describe("App against the mock bridge", () => {
  it("renders all four rows starting at zero", () => {
    render(<App />);

    expect(screen.getByText("Input:")).toBeInTheDocument();
    expect(screen.getByText("Output:")).toBeInTheDocument();
    expect(screen.getByText("Context:")).toBeInTheDocument();
    expect(screen.getByText("Total:")).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(4);
  });
});
