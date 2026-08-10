import { afterEach, describe, expect, it, vi } from "vitest";
import { handleKeyboardContextMenu, isMenuKey } from "./keyboardContextMenu";

function keydown(key: string, shiftKey = false): KeyboardEvent {
  return new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true });
}

describe("isMenuKey (ADR-012 stage 12.3)", () => {
  it("matches the ContextMenu key", () => {
    expect(isMenuKey({ key: "ContextMenu", shiftKey: false })).toBe(true);
  });

  it("matches Shift+F10", () => {
    expect(isMenuKey({ key: "F10", shiftKey: true })).toBe(true);
  });

  it("does not match bare F10 (no Shift)", () => {
    expect(isMenuKey({ key: "F10", shiftKey: false })).toBe(false);
  });

  it("does not match unrelated keys", () => {
    expect(isMenuKey({ key: "Enter", shiftKey: false })).toBe(false);
    expect(isMenuKey({ key: "a", shiftKey: true })).toBe(false);
  });
});

describe("handleKeyboardContextMenu (ADR-012 stage 12.3)", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("no-ops (returns false) for a non-menu key", () => {
    document.body.innerHTML = `<div tabindex="0"><div class="scene-node"></div></div>`;
    (document.querySelector('[tabindex="0"]') as HTMLElement).focus();
    expect(handleKeyboardContextMenu(keydown("a"))).toBe(false);
  });

  it("no-ops when focus isn't inside any .scene-node-containing element", () => {
    document.body.innerHTML = `<button>plain button</button>`;
    document.querySelector("button")!.focus();
    const event = keydown("ContextMenu");
    const prevented = vi.spyOn(event, "preventDefault");
    expect(handleKeyboardContextMenu(event)).toBe(false);
    expect(prevented).not.toHaveBeenCalled();
  });

  it("no-ops when nothing is focused (activeElement is body)", () => {
    document.body.innerHTML = `<div class="scene-node"></div>`;
    (document.activeElement as HTMLElement | null)?.blur?.();
    expect(handleKeyboardContextMenu(keydown("ContextMenu"))).toBe(false);
  });

  it("dispatches a real contextmenu event at the focused node's .scene-node, and prevents the keydown default", () => {
    document.body.innerHTML = `
      <div tabindex="0" class="react-flow-node-wrapper">
        <div class="scene-node">
          <div class="scene-node-title">Chat</div>
        </div>
      </div>
    `;
    const wrapper = document.querySelector('[tabindex="0"]') as HTMLElement;
    const sceneNode = document.querySelector(".scene-node") as HTMLElement;
    wrapper.focus();
    expect(document.activeElement).toBe(wrapper);

    let received: MouseEvent | null = null;
    sceneNode.addEventListener("contextmenu", (event) => {
      received = event as MouseEvent;
    });

    const keyEvent = keydown("ContextMenu");
    const prevented = vi.spyOn(keyEvent, "preventDefault");
    const handled = handleKeyboardContextMenu(keyEvent);

    expect(handled).toBe(true);
    expect(prevented).toHaveBeenCalledOnce();
    expect(received).not.toBeNull();
    expect(received!.bubbles).toBe(true);
  });

  it("also fires for Shift+F10, the alternate native shortcut", () => {
    document.body.innerHTML = `<div tabindex="0"><div class="scene-node"></div></div>`;
    const wrapper = document.querySelector('[tabindex="0"]') as HTMLElement;
    wrapper.focus();
    let fired = false;
    document.querySelector(".scene-node")!.addEventListener("contextmenu", () => (fired = true));
    expect(handleKeyboardContextMenu(keydown("F10", true))).toBe(true);
    expect(fired).toBe(true);
  });

  it("finds .scene-node even when it is nested deeper than a direct child", () => {
    document.body.innerHTML = `
      <div tabindex="0">
        <div class="some-wrapper">
          <div class="scene-node"></div>
        </div>
      </div>
    `;
    (document.querySelector('[tabindex="0"]') as HTMLElement).focus();
    let fired = false;
    document.querySelector(".scene-node")!.addEventListener("contextmenu", () => (fired = true));
    expect(handleKeyboardContextMenu(keydown("ContextMenu"))).toBe(true);
    expect(fired).toBe(true);
  });
});
