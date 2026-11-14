import { expect, test, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";

async function editNote(note: Locator, text: string) {
  await note.locator(".note-node-markdown").dblclick();
  const editor = note.locator(".note-node-editor");
  await editor.fill(text);
  await editor.press("Tab");
  await expect(note.locator(".note-node-markdown")).toHaveText(text);
}

async function addPair(page: Page) {
  await gotoApp(page);
  await page.getByRole("button", { name: "Add a note" }).click();
  const notes = page.locator(".react-flow__node-note");
  await editNote(notes.first(), "Review the deployment notes");
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await palette.getByRole("textbox", { name: "Search commands" }).fill("Add Note");
  await palette.getByRole("option", { name: "Add Note", exact: true }).click();
  await expect(notes).toHaveCount(2);
  await page.getByRole("button", { name: "Fit All" }).click();
  const source = notes.filter({ hasText: "Review the deployment notes" });
  const target = notes.filter({ hasText: "Add note..." });
  const sourceId = await source.getAttribute("data-id");
  const targetId = await target.getAttribute("data-id");
  if (!sourceId || !targetId) throw new Error("Expected two identified canvas nodes");
  return {
    source: page.locator(`.react-flow__node-note[data-id="${sourceId}"]`),
    target: page.locator(`.react-flow__node-note[data-id="${targetId}"]`),
    sourceId,
  };
}

test("runs a word count on demand and reconnects it when the saved graph reopens", async ({ page }) => {
  const { source, target, sourceId } = await addPair(page);
  await target.getByRole("button", { name: "Text input", exact: true }).click();
  await target.getByRole("combobox", { name: "Text source" }).selectOption(sourceId);
  await target.getByRole("combobox", { name: "Operation", exact: true }).selectOption("word_count");
  await target.getByRole("button", { name: "Connect", exact: true }).click();
  const output = target.locator(".note-node-markdown");
  await expect(output).toHaveText("Add note...");
  await expect(target.getByRole("status")).toHaveText("Ready to run");
  await target.getByRole("button", { name: "Run text input" }).click();
  await expect(output).toHaveText("4");
  await expect(target.getByRole("status")).toHaveText("Input up to date");
  await editNote(source, "Review the deployment notes again");
  await expect(output).toHaveText("4");
  await expect(target.getByRole("status")).toHaveText("Source changed · rerun to update");

  await page.getByRole("button", { name: "Save", exact: true }).click();
  const libraryTrigger = page.locator('[data-overlay-trigger="library"]');
  const library = page.getByRole("dialog", { name: "Chat Library" });
  await libraryTrigger.click();
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await library.getByRole("button", { name: "New Chat", exact: true }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await libraryTrigger.click();
  await library.locator(".library-row").first().getByRole("button", { name: /^Open chat/ }).click();

  // Find the restored destination by its binding. Runtime IDs can change on load.
  const restored = page.locator(".react-flow__node-note").filter({
    has: page.getByRole("button", { name: "Run text input" }),
  });
  await expect(restored.locator(".note-node-markdown")).toHaveText("4");
  await expect(restored.getByRole("status")).toHaveText("Source changed · rerun to update");
  await restored.getByRole("button", { name: "Run text input" }).click();
  await expect(restored.locator(".note-node-markdown")).toHaveText("5");
  await expect(restored.getByRole("status")).toHaveText("Input up to date");
});

test("connects canvas ports by dragging and copies the latest source text", async ({ page }) => {
  const { source, target } = await addPair(page);
  const outPort = source.locator('[data-handleid="text-output"]');
  const inPort = target.locator('[data-handleid="text-input"]');
  await outPort.dragTo(inPort);
  const run = target.getByRole("button", { name: "Run text input" });
  await run.click();
  await expect(target.locator(".note-node-markdown")).toHaveText("Review the deployment notes");
  await target.getByRole("button", { name: "Disconnect text input" }).click();
  await expect(run).toHaveCount(0);
  await expect(target.locator(".note-node-markdown")).toHaveText("Review the deployment notes");
});
