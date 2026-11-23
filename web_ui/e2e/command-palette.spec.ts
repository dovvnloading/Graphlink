import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: opens the command palette (chrome/CommandPalette.tsx)
 * via its real keyboard shortcut and asserts real, registered commands
 * render - not a mocked list. The shortcut itself comes straight from
 * chrome/shortcuts.ts's resolveShortcut ("k" -> "toggle-palette", Ctrl/Cmd),
 * and App.tsx's GlobalShortcuts wires that id to `overlays.toggle("palette",
 * "dialog")`, which is exactly what mounts this component.
 */
test("opens via Ctrl+K and lists real registered commands", async ({ page }) => {
  await gotoApp(page);

  await page.keyboard.press("Control+K");

  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Search commands" })).toBeFocused();

  const results = dialog.getByRole("listbox", { name: "Commands" });
  await expect(results.getByRole("option")).not.toHaveCount(0);

  // "Reset View" (chrome/commands.ts) is unconditionally enabled - unlike
  // most palette entries it needs no existing node/selection - so it is
  // present on a completely empty, freshly booted canvas, making it a
  // stable spot-check independent of any other test's canvas state.
  await expect(dialog.getByRole("option", { name: "Reset View" })).toBeVisible();

  // Escape is the overlay system's own universal dismiss (overlays.tsx).
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
