import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CanvasSearchProvider, useCanvasSearchQuery, useSetCanvasSearchQuery } from "./CanvasSearchContext";

function Reader() {
  const query = useCanvasSearchQuery();
  return <span data-testid="query">{query}</span>;
}

function Writer() {
  const setQuery = useSetCanvasSearchQuery();
  return (
    <button type="button" onClick={() => setQuery("alpha")}>
      set
    </button>
  );
}

describe("CanvasSearchContext (ADR-012 stage 12.5)", () => {
  it("defaults to an empty query outside any Provider", () => {
    render(<Reader />);
    expect(screen.getByTestId("query")).toHaveTextContent("");
  });

  it("setQuery outside a Provider is a harmless no-op", async () => {
    const user = userEvent.setup();
    render(<Writer />);
    await user.click(screen.getByRole("button"));
  });

  it("propagates a query set by one descendant to another descendant reading it", async () => {
    const user = userEvent.setup();
    render(
      <CanvasSearchProvider>
        <Writer />
        <Reader />
      </CanvasSearchProvider>,
    );
    expect(screen.getByTestId("query")).toHaveTextContent("");
    await user.click(screen.getByRole("button"));
    expect(screen.getByTestId("query")).toHaveTextContent("alpha");
  });
});
