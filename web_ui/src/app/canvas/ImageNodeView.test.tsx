import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL
// invocation (mount or re-render) is countable - a React.memo bailout skips
// calling ImageNodeView's function body entirely, so this hook (called
// unconditionally on every real render) never fires during a bailed
// re-render. This is the same "mock-wrap-and-delegate" technique
// renderCountGate.test.tsx uses on ChatNodeView, applied at hook
// granularity instead of whole-component granularity.
const lodVisibilityCalls = { count: 0 };

vi.mock("./useLodVisibility", async (importOriginal) => {
  const original = await importOriginal<typeof import("./useLodVisibility")>();
  return {
    ...original,
    useLodVisibility: (...args: Parameters<typeof original.useLodVisibility>) => {
      lodVisibilityCalls.count += 1;
      return original.useLodVisibility(...args);
    },
  };
});

import { ImageNodeView, type ImageFlowNode } from "./ImageNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderImageNode(overrides: Partial<ImageFlowNode["data"]> = {}, id = "n0") {
  const onDelete = vi.fn();
  const onRegenerate = vi.fn();
  const onToggleBranchFocus = vi.fn();
  const props = {
    id,
    selected: false,
    data: {
      imageAssetId: "asset-123",
      prompt: "a red fox in the snow",
      onDelete,
      onRegenerate,
      isBranchFocusActive: false,
      onToggleBranchFocus,
      ...overrides,
    },
  } as unknown as NodeProps<ImageFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <ImageNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDelete, onRegenerate, onToggleBranchFocus, container };
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

  it("right-click opens a menu with real Copy Image/Export Image/Hide Other Branches/Regenerate Image/Delete Image", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderImageNode({ prompt: "a red fox in the snow" });

    const title = screen.getByText("a red fox in the snow");
    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    expect(screen.getByRole("menuitem", { name: "Copy Image" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Export Image" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Hide Other Branches" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Regenerate Image" })).toBeEnabled();

    await user.click(screen.getByRole("menuitem", { name: "Delete Image" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("Hide Other Branches calls onToggleBranchFocus and closes the menu when branch focus is inactive", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderImageNode({
      prompt: "a red fox in the snow",
      isBranchFocusActive: false,
    });

    fireEvent.contextMenu(screen.getByText("a red fox in the snow"));
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });

    await user.click(hideBranches);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("labels the item 'Show All Branches' and still calls onToggleBranchFocus when branch focus is active", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderImageNode({
      prompt: "a red fox in the snow",
      isBranchFocusActive: true,
    });

    fireEvent.contextMenu(screen.getByText("a red fox in the snow"));
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    const showAll = screen.getByRole("menuitem", { name: "Show All Branches" });

    await user.click(showAll);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Regenerate Image is a real, enabled item that calls onRegenerate then closes the menu", async () => {
    const user = userEvent.setup();
    const { onRegenerate } = renderImageNode({ prompt: "a red fox in the snow" });

    fireEvent.contextMenu(screen.getByText("a red fox in the snow"));
    const regenerate = screen.getByRole("menuitem", { name: "Regenerate Image" });
    expect(regenerate).not.toBeDisabled();

    await user.click(regenerate);
    expect(onRegenerate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull(); // onClose fires after onRegenerate
  });

  it("Regenerate Image is absent from the menu entirely for a prompt-less image node", () => {
    // Mirrors legacy's own menu-build-time gate (graphlink_node_image_menu.py:
    // `if self.node.parent_content_node and self.node.prompt:` - the action is
    // never added to the menu at all when prompt is falsy), reachable via a
    // user-attached image with no caption text. Absent from the DOM, not
    // merely disabled - same convention CodeNodeView's own parent-gated
    // Regenerate Response item uses.
    renderImageNode({ prompt: "" }, "n7");

    fireEvent.contextMenu(screen.getByText("Image"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Regenerate Image" })).toBeNull();
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

// ADR-011 stage 11.1: React.memo comparator correctness. `lodVisibilityCalls`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it
// must stay flat across an irrelevant/equivalent prop change (too-tight
// would fail this) and must increment on a change to a prop the view
// actually reads (too-loose would fail this).
describe("ImageNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function baseImageProps(overrides: Partial<ImageFlowNode["data"]> = {}) {
    const data = {
      imageAssetId: "asset-1",
      prompt: "a fox",
      onDelete: vi.fn(),
      onRegenerate: vi.fn(),
      isBranchFocusActive: false,
      onToggleBranchFocus: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<ImageFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseImageProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ImageNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    // A brand-new object, same primitives and same callback references -
    // exactly what toFlowNodes may mint on an unrelated snapshot. A naive
    // `data === nextData` reference compare (or React.memo's default
    // shallow-props compare, which would see a new `data` prop identity)
    // would wrongly re-render here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <ImageNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `prompt` (a field the view reads) changes", () => {
    const props = baseImageProps({ prompt: "a fox" });
    const { rerender } = render(
      <ReactFlowProvider>
        <ImageNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ImageNodeView {...{ ...props, data: { ...props.data, prompt: "a different fox" } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
    expect(screen.getByText("a different fox")).toBeInTheDocument();
  });

  it("re-renders when a callback prop is rebound to a new closure (e.g. onDelete)", () => {
    const props = baseImageProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ImageNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ImageNodeView {...{ ...props, data: { ...props.data, onDelete: vi.fn() } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
