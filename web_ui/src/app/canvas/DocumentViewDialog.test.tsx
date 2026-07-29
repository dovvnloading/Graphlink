import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DocumentViewDialog } from "./DocumentViewDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

// R8a: Document View dialog tests - mirrors the OverlayProvider + trigger
// pattern from SettingsDialog.test.tsx (see that file's OpenDocumentView
// helper's sibling, OpenSettingsButton), since Dialog only mounts its
// content while its named surface is the open one.

function OpenDocumentViewButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("document-view", "dialog")}>
      open document view
    </button>
  );
}

function setup(content: string | null) {
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <OpenDocumentViewButton />
      <DocumentViewDialog content={content} />
    </OverlayProvider>,
  );
  return { user };
}

describe("DocumentViewDialog", () => {
  it("renders nothing when closed", () => {
    setup("# Hello");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Hello")).toBeNull();
  });

  it("shows the dialog with the fixed title after opening via the trigger", async () => {
    const { user } = setup("some content");
    await user.click(screen.getByText("open document view"));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Document View")).toBeInTheDocument();
  });

  it("renders markdown content passed via the content prop", async () => {
    const { user } = setup("# Heading\n\nA paragraph of body text.");
    await user.click(screen.getByText("open document view"));

    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("A paragraph of body text.")).toBeInTheDocument();
  });

  it("does not crash and renders an empty body when content is null", async () => {
    const { user } = setup(null);
    await user.click(screen.getByText("open document view"));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Document View")).toBeInTheDocument();
  });
});
