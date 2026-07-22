import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ImageNodeView, type ImageFlowNode } from "./ImageNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderImageNode(overrides: Partial<ImageFlowNode["data"]> = {}, id = "n0") {
  const onDelete = vi.fn();
  const props = {
    id,
    selected: false,
    data: {
      imageAssetId: "asset-123",
      prompt: "a red fox in the snow",
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<ImageFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <ImageNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDelete, container };
}

// jsdom implements neither URL.createObjectURL/revokeObjectURL nor
// ClipboardItem nor navigator.clipboard - every test that exercises Copy
// Image/Export Image has to hand-install its own fakes for the run. These
// are plain property assignments (not vi.spyOn) because vi.spyOn requires
// the property to already exist on the object, and none of the above do in
// this jsdom version.
// A real `class`, not vi.fn().mockImplementation(arrow) - ClipboardItem is
// invoked with `new` in the component, and an arrow-function mock can't be a
// constructor (vitest surfaces that as a silent-looking TypeError caught by
// handleCopyImage's own try/catch, which made the failure confusing until
// traced: `write` was never reached because construction itself threw).
class FakeClipboardItem {
  items: Record<string, Blob>;
  constructor(items: Record<string, Blob>) {
    this.items = items;
  }
}

beforeEach(() => {
  URL.createObjectURL = vi.fn().mockReturnValue("blob:fake-object-url");
  URL.revokeObjectURL = vi.fn();
  (globalThis as unknown as { ClipboardItem: unknown }).ClipboardItem = FakeClipboardItem;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ImageNodeView", () => {
  it("renders the img with the correct src pointing at the asset endpoint", () => {
    const { container } = renderImageNode({ imageAssetId: "asset-123" });
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/api/assets/asset-123");
  });

  it("falls back to 'Generated image' alt text when prompt is empty", () => {
    const { container } = renderImageNode({ prompt: "" });
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("alt", "Generated image");
  });

  it("uses the prompt as alt text when present", () => {
    const { container } = renderImageNode({ prompt: "a red fox in the snow" });
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("alt", "a red fox in the snow");
  });

  it("onError shows the 'Image unavailable' placeholder and hides the broken img", () => {
    const { container } = renderImageNode();
    const img = container.querySelector("img");
    expect(img).not.toBeNull();

    fireEvent.error(img!);

    expect(screen.getByText("Image unavailable")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("right-click opens a menu with real Copy Image/Export Image/Delete Image and disabled Hide Other Branches/Regenerate Image", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderImageNode({ prompt: "a red fox in the snow" });

    const title = screen.getByText("a red fox in the snow");
    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    expect(screen.getByRole("menuitem", { name: "Copy Image" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Export Image" })).toBeEnabled();

    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(hideBranches).toBeDisabled();
    expect(hideBranches).toHaveAttribute("title", "Branch visibility isn't built yet");

    const regenerate = screen.getByRole("menuitem", { name: "Regenerate Image" });
    expect(regenerate).toBeDisabled();
    expect(regenerate).toHaveAttribute("title", "Agent regeneration lands in R4");

    await user.click(screen.getByRole("menuitem", { name: "Delete Image" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("clicking Copy Image fetches the asset and writes it to the clipboard as a ClipboardItem", async () => {
    const user = userEvent.setup();
    renderImageNode({ imageAssetId: "asset-abc", prompt: "sunset over the bay" });

    const fakeBlob = { type: "image/png" } as Blob;
    const fetchMock = vi.fn().mockResolvedValue({ blob: () => Promise.resolve(fakeBlob) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { write }, configurable: true });

    fireEvent.contextMenu(screen.getByText("sunset over the bay"));
    await user.click(screen.getByRole("menuitem", { name: "Copy Image" }));

    await waitFor(() => expect(write).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith("/api/assets/asset-abc");
    expect(write).toHaveBeenCalledWith([new FakeClipboardItem({ "image/png": fakeBlob })]);
  });

  it("does not throw when the clipboard write fails", async () => {
    const user = userEvent.setup();
    renderImageNode({ imageAssetId: "asset-abc", prompt: "sunset over the bay" });

    const fakeBlob = { type: "image/png" } as Blob;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ blob: () => Promise.resolve(fakeBlob) } as unknown as Response),
    );
    const write = vi.fn().mockRejectedValue(new Error("permission denied"));
    Object.defineProperty(navigator, "clipboard", { value: { write }, configurable: true });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    fireEvent.contextMenu(screen.getByText("sunset over the bay"));
    await user.click(screen.getByRole("menuitem", { name: "Copy Image" }));

    await waitFor(() => expect(write).toHaveBeenCalled());
    await waitFor(() => expect(consoleError).toHaveBeenCalled());
    consoleError.mockRestore();
  });

  it("clicking Export Image fetches the asset, creates an object URL, and clicks a temporary download anchor", async () => {
    const user = userEvent.setup();
    renderImageNode({ imageAssetId: "asset-xyz", prompt: "mountain lake" }, "n7");

    const fakeBlob = { type: "image/png" } as Blob;
    const fetchMock = vi.fn().mockResolvedValue({ blob: () => Promise.resolve(fakeBlob) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    // jsdom has no navigation implementation, so letting a real anchor.click()
    // run its default download/navigate behavior would either no-op silently
    // or spam "Not implemented: navigation" to the virtual console depending
    // on jsdom's version - neither tells us anything useful. Instead, spy on
    // HTMLAnchorElement.prototype.click itself (rather than document.
    // createElement) so we capture the exact anchor instance our code built,
    // without ever letting jsdom attempt real navigation.
    // A plain `let` reassigned only from inside the mockImplementation
    // closure below type-checks its later reads as `never` (TS can't narrow
    // a closure-captured let back to its declared type across the
    // intervening `await`) - an object wrapper sidesteps that entirely.
    const captured: { anchor: HTMLAnchorElement | null } = { anchor: null };
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        captured.anchor = this;
      });

    fireEvent.contextMenu(screen.getByText("mountain lake"));
    await user.click(screen.getByRole("menuitem", { name: "Export Image" }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith("/api/assets/asset-xyz");
    expect(URL.createObjectURL).toHaveBeenCalledWith(fakeBlob);
    expect(captured.anchor?.getAttribute("href")).toBe("blob:fake-object-url");
    expect(captured.anchor?.getAttribute("download")).toBe("mountain-lake.png");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake-object-url");
  });

  it("falls back to the node id for the download filename when prompt is empty", async () => {
    const user = userEvent.setup();
    renderImageNode({ imageAssetId: "asset-xyz", prompt: "" }, "n7");

    const fakeBlob = { type: "image/png" } as Blob;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ blob: () => Promise.resolve(fakeBlob) } as unknown as Response),
    );

    const captured: { anchor: HTMLAnchorElement | null } = { anchor: null };
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        captured.anchor = this;
      });

    fireEvent.contextMenu(screen.getByText("Image"));
    await user.click(screen.getByRole("menuitem", { name: "Export Image" }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(captured.anchor?.getAttribute("download")).toBe("n7.png");
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    renderImageNode({ prompt: "a red fox in the snow" });
    const title = screen.getByText("a red fox in the snow");

    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
