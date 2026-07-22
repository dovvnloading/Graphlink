import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
      ...overrides,
    },
  } as unknown as NodeProps<DocumentFlowNode>;

  render(
    <ReactFlowProvider>
      <DocumentNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDock, onDelete };
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

    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const title = screen.getByText("notes.pdf");
    fireEvent.contextMenu(title);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Dock into Parent Node" }));
    expect(onDock).toHaveBeenCalledOnce();

    fireEvent.contextMenu(title);
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(hideBranches).toBeDisabled();
    expect(hideBranches).toHaveAttribute("title", "Branch visibility isn't built yet");

    const exportItem = screen.getByRole("menuitem", { name: "Export" });
    expect(exportItem).toBeDisabled();
    expect(exportItem).toHaveAttribute("title", "Export lands in R6");

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
