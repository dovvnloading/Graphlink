import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL
// invocation (mount or re-render) is countable - a React.memo bailout skips
// calling DocumentNodeView's function body entirely, so this hook (called
// unconditionally on every real render) never fires during a bailed
// re-render. Same "mock-wrap-and-delegate" technique ImageNodeView.test.tsx
// established.
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

import {
  DocumentNodeView,
  formatByteSize,
  formatDuration,
  shouldShowAudioPreview,
  type DocumentFlowNode,
} from "./DocumentNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderDocumentNode(overrides: Partial<DocumentFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDock = vi.fn();
  const onDelete = vi.fn();
  const onToggleBranchFocus = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      title: "notes.pdf",
      content: "Quarterly figures attached.",
      attachmentKind: "document",
      filePath: "",
      mimeType: "",
      durationSeconds: null,
      byteSize: null,
      previewLabel: "",
      isCollapsed: false,
      onToggleCollapse,
      onDock,
      onDelete,
      isBranchFocusActive: false,
      onToggleBranchFocus,
      ...overrides,
    },
  } as unknown as NodeProps<DocumentFlowNode>;

  render(
    <ReactFlowProvider>
      <DocumentNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDock, onDelete, onToggleBranchFocus };
}

describe("formatByteSize (ported DocumentNode._format_byte_size)", () => {
  it("is Unknown for null/zero", () => {
    expect(formatByteSize(null)).toBe("Unknown");
    expect(formatByteSize(0)).toBe("Unknown");
  });

  it("renders whole bytes with no decimal", () => {
    expect(formatByteSize(512)).toBe("512 B");
  });

  it("renders KB/MB with one decimal place", () => {
    expect(formatByteSize(2048)).toBe("2.0 KB");
    expect(formatByteSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("formatDuration (ported graphlink_audio.format_duration)", () => {
  it("is Unknown for null", () => {
    expect(formatDuration(null)).toBe("Unknown");
  });

  it("renders M:SS under an hour", () => {
    expect(formatDuration(65)).toBe("1:05");
  });

  it("renders H:MM:SS once an hour is reached", () => {
    expect(formatDuration(3725)).toBe("1:02:05");
  });
});

describe("shouldShowAudioPreview (ported DocumentNode._should_show_audio_preview)", () => {
  it("shows a real transcript that differs from the audio-details block", () => {
    const audioDetails = "Audio attachment\nDuration: 1:05\nFormat: audio/mpeg";
    expect(shouldShowAudioPreview("Here is what the speaker said...", audioDetails)).toBe(true);
  });

  it("suppresses when content is empty", () => {
    expect(shouldShowAudioPreview("   ", "Audio attachment")).toBe(false);
  });

  it("suppresses when content exactly matches the freshly-built audio details", () => {
    const audioDetails = "Audio attachment\nDuration: 1:05";
    expect(shouldShowAudioPreview(audioDetails, audioDetails)).toBe(false);
  });

  it("legacy-compat: suppresses an old saved session's persisted metadata block even if it no longer matches verbatim", () => {
    // Old session persisted just "Audio attachment\nDuration: 0:45" as
    // content; today's freshly-built details string has grown a Format
    // line the old session never recorded, so they no longer match
    // byte-for-byte - the special-case rule must still catch this.
    const legacyContent = "Audio attachment\nDuration: 0:45";
    const freshAudioDetails = "Audio attachment\nDuration: 0:45\nFormat: audio/wav";
    expect(shouldShowAudioPreview(legacyContent, freshAudioDetails)).toBe(false);
  });
});

describe("DocumentNodeView", () => {
  it("renders the title and correctly formatted metadata rows", () => {
    renderDocumentNode({
      title: "quarterly-report.pdf",
      attachmentKind: "document",
      mimeType: "application/pdf",
      byteSize: 2048,
      filePath: "C:/docs/quarterly-report.pdf",
      durationSeconds: null,
    });
    expect(screen.getByText("quarterly-report.pdf")).toBeInTheDocument();
    expect(screen.getByText("Document")).toBeInTheDocument();
    expect(screen.getByText("application/pdf")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("C:/docs/quarterly-report.pdf")).toBeInTheDocument();
    // Duration is gated on durationSeconds being populated, not on kind.
    expect(screen.queryByText("Duration")).toBeNull();
  });

  it("shows Duration and Audio file type for audio metadata", () => {
    renderDocumentNode({
      title: "clip.mp3",
      attachmentKind: "audio",
      content: "Full transcript of the recording goes here.",
      durationSeconds: 65,
      mimeType: "audio/mpeg",
    });
    expect(screen.getByText("Audio file")).toBeInTheDocument();
    expect(screen.getByText("1:05")).toBeInTheDocument();
  });

  it("shows the content preview panel for document kind", () => {
    renderDocumentNode({ attachmentKind: "document", content: "Quarterly figures attached." });
    expect(screen.getByText("Contents")).toBeInTheDocument();
    expect(screen.getByText("Quarterly figures attached.")).toBeInTheDocument();
  });

  it("shows the content preview for audio kind when content is a real transcript", () => {
    renderDocumentNode({
      attachmentKind: "audio",
      content: "Speaker: hello, this is the actual transcript.",
      durationSeconds: 65,
    });
    expect(screen.getByText("Contents")).toBeInTheDocument();
    expect(screen.getByText("Speaker: hello, this is the actual transcript.")).toBeInTheDocument();
  });

  it("suppresses the content preview for audio kind per the legacy-compat rule", () => {
    renderDocumentNode({
      attachmentKind: "audio",
      // Old saved session persisted just the bare legacy metadata block as
      // content; today's freshly-built audio-details string has since grown
      // a Format line the old session never recorded (mimeType below), so a
      // plain equality check would NOT catch this - only the startsWith
      // "audio attachment" + "duration:" special-case rule does.
      content: "Audio attachment\nDuration: 1:05",
      durationSeconds: 65,
      mimeType: "audio/mpeg",
    });
    expect(screen.queryByText("Contents")).toBeNull();
  });

  it("the inline collapse button calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const { onToggleCollapse } = renderDocumentNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("hides the body when isCollapsed is true", () => {
    renderDocumentNode({ isCollapsed: true, content: "Quarterly figures attached." });
    expect(screen.queryByText("Quarterly figures attached.")).toBeNull();
  });

  it("right-click opens a menu with real Copy Details/Dock/Collapse/Delete Attachment and honest disabled placeholders", async () => {
    const user = userEvent.setup();
    const { onDelete, onDock } = renderDocumentNode({ attachmentKind: "document", filePath: "" });

    // ADR-011 stage 11.1: Copy Details now chains a .catch() onto the
    // clipboard write (D11), so the mock must resolve like a real
    // Promise-returning clipboard API - a bare vi.fn() would make that
    // .catch() call throw synchronously on `undefined`.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const title = screen.getByText("notes.pdf");
    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Dock into Parent Node" }));
    expect(onDock).toHaveBeenCalledOnce();

    fireEvent.contextMenu(title);
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(hideBranches).toBeEnabled();

    const exportItem = screen.getByRole("menuitem", { name: "Export" });
    expect(exportItem).toBeDisabled();
    expect(exportItem).toHaveAttribute("title", "Document export isn't available yet");

    // filePath is empty -> Open File must be entirely absent, matching the
    // legacy menu's own conditional (only added when file_path is set).
    expect(screen.queryByRole("menuitem", { name: "Open File" })).toBeNull();

    await user.click(screen.getByRole("menuitem", { name: "Copy Details" }));
    expect(writeText).toHaveBeenCalledWith("Quarterly figures attached.");

    fireEvent.contextMenu(title);
    await user.click(screen.getByRole("menuitem", { name: "Collapse to Pill" }));
    expect(screen.queryByRole("menu")).toBeNull(); // the menu closes after any item fires

    fireEvent.contextMenu(title);
    await user.click(screen.getByRole("menuitem", { name: "Delete Attachment" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("shows Open File when filePath is set, and reads 'Delete Audio Attachment' + no Export for audio kind", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderDocumentNode({
      attachmentKind: "audio",
      filePath: "C:/audio/clip.mp3",
      content: "Speaker: a real transcript.",
      durationSeconds: 65,
    });

    const title = screen.getByText("notes.pdf");
    fireEvent.contextMenu(title);

    const openFile = screen.getByRole("menuitem", { name: "Open File" });
    expect(openFile).toBeDisabled();
    expect(openFile).toHaveAttribute(
      "title",
      "Opening local files needs a new backend endpoint - browsers can't open arbitrary local paths",
    );

    // attachmentKind "audio" -> Export must be entirely absent, matching the
    // legacy menu's own conditional (export submenu only added for "document").
    expect(screen.queryByRole("menuitem", { name: "Export" })).toBeNull();

    await user.click(screen.getByRole("menuitem", { name: "Delete Audio Attachment" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("hides Export for a non-audio, non-document kind (legacy gate is `== \"document\"`, not `!= \"audio\"`)", async () => {
    renderDocumentNode({ attachmentKind: "unknown", filePath: "" });
    const title = screen.getByText("notes.pdf");
    fireEvent.contextMenu(title);
    expect(screen.queryByRole("menuitem", { name: "Export" })).toBeNull();
  });

  it("Hide Other Branches calls onToggleBranchFocus and closes the menu when branch focus is inactive", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderDocumentNode({ isBranchFocusActive: false });
    const title = screen.getByText("notes.pdf");

    fireEvent.contextMenu(title);
    await user.click(screen.getByRole("menuitem", { name: "Hide Other Branches" }));
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("reads 'Show All Branches' when branch focus is active, and still calls the same onToggleBranchFocus", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderDocumentNode({ isBranchFocusActive: true });
    const title = screen.getByText("notes.pdf");

    fireEvent.contextMenu(title);
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "Show All Branches" }));
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    renderDocumentNode();
    const title = screen.getByText("notes.pdf");

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
// never on a bailed-out one - so it's the oracle for both directions: it must
// stay flat across an irrelevant/equivalent prop change (too-tight would fail
// this) and must increment on a change to a prop the view actually reads
// (too-loose would fail this).
describe("DocumentNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function baseDocumentProps(overrides: Partial<DocumentFlowNode["data"]> = {}) {
    const data = {
      title: "notes.pdf",
      content: "Quarterly figures attached.",
      attachmentKind: "document",
      filePath: "",
      mimeType: "",
      durationSeconds: null,
      byteSize: null,
      previewLabel: "",
      isCollapsed: false,
      onToggleCollapse: vi.fn(),
      onDock: vi.fn(),
      onDelete: vi.fn(),
      isBranchFocusActive: false,
      onToggleBranchFocus: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<DocumentFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseDocumentProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <DocumentNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    // A brand-new object, same primitives and same callback references -
    // exactly what toFlowNodes may mint on an unrelated snapshot. A naive
    // `data === nextData` reference compare (or React.memo's default shallow
    // props compare) would wrongly re-render here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <DocumentNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("skips re-rendering when only `previewLabel` (a field this view never reads) changes", () => {
    // Pins the deliberate omission documented on documentNodeDataAreEqual:
    // previewLabel isn't surfaced in this increment's render at all, so a
    // change to it alone must never trigger a re-render.
    const props = baseDocumentProps({ previewLabel: "old label" });
    const { rerender } = render(
      <ReactFlowProvider>
        <DocumentNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <DocumentNodeView {...{ ...props, data: { ...props.data, previewLabel: "a different label" } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `content` (a field the view reads) changes", () => {
    const props = baseDocumentProps({ content: "Quarterly figures attached." });
    const { rerender } = render(
      <ReactFlowProvider>
        <DocumentNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <DocumentNodeView {...{ ...props, data: { ...props.data, content: "Updated figures attached." } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
    expect(screen.getByText("Updated figures attached.")).toBeInTheDocument();
  });

  it("re-renders when a callback prop is rebound to a new closure (e.g. onDelete)", () => {
    const props = baseDocumentProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <DocumentNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <DocumentNodeView {...{ ...props, data: { ...props.data, onDelete: vi.fn() } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
