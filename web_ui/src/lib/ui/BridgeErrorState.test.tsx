import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BridgeErrorState } from "./BridgeErrorState";

const baseRejection = { kind: "parse" as const, reason: "not json", details: [] };

describe("BridgeErrorState", () => {
  it("renders the title, reason, and applies the caller's className", () => {
    render(
      <BridgeErrorState
        title="Widget unavailable"
        rejection={baseRejection}
        className="widget-shell widget-error"
      />,
    );

    expect(screen.getByRole("alert")).toHaveClass("widget-shell", "widget-error");
    expect(screen.getByText("Widget unavailable")).toBeInTheDocument();
    expect(screen.getByText("not json")).toBeInTheDocument();
  });

  it("renders each detail as a list item when details are present", () => {
    render(
      <BridgeErrorState
        title="Widget unavailable"
        rejection={{ kind: "shape", reason: "bad shape", details: ["missing id", "wrong type"] }}
        className="widget-shell"
      />,
    );

    expect(screen.getByText("missing id")).toBeInTheDocument();
    expect(screen.getByText("wrong type")).toBeInTheDocument();
  });

  it("renders no list at all when details is empty", () => {
    render(
      <BridgeErrorState title="Widget unavailable" rejection={baseRejection} className="widget-shell" />,
    );

    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows the rebuild hint for a version rejection", () => {
    render(
      <BridgeErrorState
        title="Widget unavailable"
        rejection={{ kind: "version", reason: "too old", details: [] }}
        className="widget-shell"
      />,
    );

    expect(screen.getByText(/rebuilding the app's interface assets/i)).toBeInTheDocument();
  });

  it("shows the generic bug hint for a non-version rejection", () => {
    render(
      <BridgeErrorState title="Widget unavailable" rejection={baseRejection} className="widget-shell" />,
    );

    expect(screen.getByText(/this is a bug/i)).toBeInTheDocument();
  });
});
