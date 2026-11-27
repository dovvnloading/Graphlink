/** Keyboard edit entry (#1143) and stale-edit protection (#1144) for notes. */
import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NoteNodeView, type NoteFlowNode } from "./NoteNodeView";

type Settle = (success: boolean) => void;

function setup(content = "A") {
  const settles: Settle[] = [];
  const onSetContent = vi.fn((_content: string, _expected: string, onSettled: Settle) => {
    settles.push(onSettled);
  });
  const base = {
    content, color: null, headerColor: null, isSystemPrompt: false, isSummaryNote: false,
    isBranchComparison: false, compareSourceNodeIds: [], onSetContent, onSetColor: vi.fn(), onDelete: vi.fn(),
  };
  const view = (data: object) => (
    <ReactFlowProvider>
      <NoteNodeView {...({ id: "n0", selected: false, data: { ...base, ...data } } as unknown as NodeProps<NoteFlowNode>)} />
    </ReactFlowProvider>
  );
  const utils = render(view({}));
  // Same callbacks, new authoritative content: a remote edit or run result.
  const pushContent = (next: string) => utils.rerender(view({ content: next }));
  return { onSetContent, settles, pushContent };
}

describe("NoteNodeView editing", () => {
  it("Edit note opens a named multiline editor from the keyboard and Escape returns focus (#1143)", async () => {
    const user = userEvent.setup();
    setup("first line");
    const edit = screen.getByRole("button", { name: "Edit note" });
    edit.focus();
    await user.keyboard("{Enter}");
    const editor = screen.getByRole("textbox", { name: "Note text" });
    expect(editor).toHaveFocus();
    expect(editor).toHaveAccessibleDescription(/Escape cancels/);
    await user.keyboard("{End}{Enter}- second");
    expect(editor).toHaveValue("first line\n- second");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("button", { name: "Edit note" })).toHaveFocus();
  });

  it("Tab out of the editor commits a multiline Markdown edit (#1143)", async () => {
    const user = userEvent.setup();
    const { onSetContent } = setup("A");
    await user.click(screen.getByRole("button", { name: "Edit note" }));
    await user.keyboard("{End}{Enter}**B**");
    await user.tab();
    expect(onSetContent).toHaveBeenCalledWith("A\n**B**", "A", expect.any(Function));
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("an unchanged editor never writes, on blur or Ctrl+S, even after newer content arrives (#1144)", () => {
    const { onSetContent, pushContent } = setup("A");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    pushContent("B");
    const editor = screen.getByRole("textbox", { name: "Note text" });
    fireEvent.keyDown(editor, { key: "s", ctrlKey: true });
    fireEvent.blur(editor);
    expect(onSetContent).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("a dirty edit over newer content asks first, keeping the draft (#1144)", () => {
    const { onSetContent, pushContent } = setup("A");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note text" }), { target: { value: "my draft" } });
    pushContent("B");
    fireEvent.blur(screen.getByRole("textbox", { name: "Note text" }));
    expect(onSetContent).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent(/changed while you were editing/);
    expect(screen.getByRole("textbox", { name: "Note text" })).toHaveValue("my draft");

    fireEvent.click(screen.getByRole("button", { name: "Keep my edit" }));
    expect(onSetContent).toHaveBeenCalledWith("my draft", "B", expect.any(Function));
  });

  it("Use the new version discards the draft without writing (#1144)", () => {
    const { onSetContent, pushContent } = setup("A");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note text" }), { target: { value: "my draft" } });
    pushContent("B");
    fireEvent.blur(screen.getByRole("textbox", { name: "Note text" }));
    fireEvent.click(screen.getByRole("button", { name: "Use the new version" }));
    expect(onSetContent).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("a write the backend refuses reopens the editor with the draft intact (#1144)", () => {
    const { onSetContent, settles } = setup("A");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note text" }), { target: { value: "my draft" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Note text" }));
    expect(onSetContent).toHaveBeenCalledWith("my draft", "A", expect.any(Function));
    act(() => settles[0](false));
    expect(screen.getByRole("textbox", { name: "Note text" })).toHaveValue("my draft");
    expect(screen.getByRole("status")).toHaveTextContent(/wasn't saved/);
  });

  it("an uncontested edit saves once and closes (#1144)", () => {
    const { onSetContent, settles } = setup("A");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note text" }), { target: { value: "A2" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Note text" }));
    act(() => settles[0](true));
    expect(onSetContent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
