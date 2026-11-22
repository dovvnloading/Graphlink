import { expect, test, type Locator } from "@playwright/test";
import { gotoApp } from "./helpers";

async function bounds(locator: Locator) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (!box) throw new Error("Expected a visible layout box");
  return box;
}

test("starts a graph and navigates it using the canvas controls", async ({ page }) => {
  await gotoApp(page);
  const navigation = page.getByRole("group", { name: "Canvas navigation" });
  const fit = navigation.getByRole("button", { name: "Fit All" });
  await expect(fit).toBeDisabled();
  await page.getByRole("button", { name: "Write a message" }).click();
  await expect(page.locator("#composer-message-input")).toBeFocused();
  await page.getByRole("button", { name: "Add a note" }).click();
  await expect(page.locator(".react-flow__node-note")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Start a graph" })).toHaveCount(0);
  await expect(fit).toBeEnabled();
  await fit.click();

  const scale = navigation.getByRole("button", { name: "Reset zoom to 100%" });
  await expect(scale).toHaveText("100%");
  await navigation.getByRole("button", { name: "Zoom Out" }).click();
  await expect(scale).toHaveText("83%");
  await scale.click();
  await expect(scale).toHaveText("100%");
  const stage = page.locator(".graph-stage");
  const footer = page.locator(".app-composer-layer");
  const input = footer.getByRole("textbox", { name: "Message composer" });
  const minimap = page.getByRole("group", { name: /^Canvas overview/ });
  const draft = ["Map the evidence", "Check the first source", "Trace its dependencies", "Review the conclusion"].join("\n");

  for (const width of [1280, 640]) {
    await test.step(`keep the growing composer separate from the graph at ${width}px`, async () => {
      const viewport = { width, height: 720 };
      await page.setViewportSize(viewport);
      await input.fill("");
      const emptyStage = await bounds(stage);
      await input.fill(draft);
      await expect(input).toHaveValue(draft);

      const graphBox = await bounds(stage);
      const footerBox = await bounds(footer);
      expect(graphBox.height).toBeLessThan(emptyStage.height);
      expect(graphBox.y + graphBox.height).toBeLessThanOrEqual(footerBox.y + 1);
      expect(footerBox.y + footerBox.height).toBeLessThanOrEqual(viewport.height + 1);
      const [navigationBox, minimapBox, cardBox] = await Promise.all([
        bounds(navigation), bounds(minimap), bounds(footer.locator(".composer-dock")),
      ]);
      expect(navigationBox.x + navigationBox.width).toBeLessThanOrEqual(minimapBox.x + 1);
      for (const box of [navigationBox, minimapBox]) {
        expect(box.x).toBeGreaterThanOrEqual(graphBox.x - 1);
        expect(box.x + box.width).toBeLessThanOrEqual(graphBox.x + graphBox.width + 1);
        expect(box.y).toBeGreaterThanOrEqual(graphBox.y - 1);
        if (width >= 1280) {
          // Wide canvas: the zoom dock and minimap share the composer card's
          // baseline, beside the card rather than over it.
          expect(Math.abs(box.y + box.height - (cardBox.y + cardBox.height))).toBeLessThanOrEqual(1);
        } else {
          // Narrow canvas: they sit inside the graph, just above the composer.
          expect(box.y + box.height).toBeLessThanOrEqual(graphBox.y + graphBox.height + 1);
        }
      }
      if (width >= 1280) {
        expect(navigationBox.x + navigationBox.width).toBeLessThanOrEqual(cardBox.x + 1);
        expect(cardBox.x + cardBox.width).toBeLessThanOrEqual(minimapBox.x + 1);
      }
      await expect(input).toBeInViewport({ ratio: 1 });
      await expect(footer.getByRole("button", { name: "Send message" })).toBeInViewport({ ratio: 1 });

      for (const [name, label] of [["reasoning", "Choose a reasoning level"], ["model", "Choose a model"]]) {
        const trigger = footer.locator(`[data-overlay-trigger="${name}"]`);
        // The reasoning chip only renders when the route exposes reasoning levels.
        if (await trigger.count() === 0) continue;
        await expect(trigger).toBeInViewport({ ratio: 1 });
        if (await trigger.isDisabled()) continue; // Options depend on the provider and installed models.
        await trigger.click();
        const picker = page.getByRole("dialog", { name: label });
        await expect(picker).toBeInViewport({ ratio: 1 });
        if (name === "model") {
          await expect(picker.getByText("Loading local and cloud models…")).toHaveCount(0);
          const choices = picker.locator(".model-picker-option:enabled");
          if (await choices.count()) {
            await choices.first().click();
          } else {
            await expect(picker.getByRole("status")).toContainText(/No models available|None of these models supports chat/);
            await expect(picker.getByRole("button", { name: "Set up a local model" })).toBeVisible();
            await page.keyboard.press("Escape");
          }
        } else {
          await picker.getByRole("button").first().click();
        }
        await expect(picker).toBeHidden();
        await expect(input).toHaveValue(draft);
      }
    });
  }

  await page.getByRole("button", { name: "Collapse minimap" }).click();
  await expect(page.getByRole("button", { name: "Expand minimap" })).toHaveAttribute("aria-expanded", "false");
});

test("keeps the canvas panels apart when a document split leaves 300px of canvas", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await gotoApp(page);
  await page.getByRole("button", { name: "Add a note" }).click();
  await expect(page.locator(".react-flow__node-note")).toHaveCount(1);

  // Document View is a flex sibling of the canvas. Squeeze the canvas itself,
  // not the browser viewport, so a window media query cannot pass this test.
  const canvas = page.locator(".app-canvas-content");
  await canvas.evaluate((element) => {
    const style = (element as HTMLElement).style;
    style.flex = "none";
    style.width = "300px";
  });
  const canvasBox = await bounds(canvas);
  expect(canvasBox.width).toBe(300);

  const navigation = page.getByRole("group", { name: "Canvas navigation" });
  const minimap = page.getByRole("group", { name: /^Canvas overview/ });
  await expect(minimap.getByRole("button", { name: "Collapse minimap" })).toBeVisible();
  const dockBox = await bounds(navigation);
  const mapBox = await bounds(minimap);

  // Side by side or stacked is fine; their bounding rectangles must not intersect.
  expect(
    dockBox.x + dockBox.width <= mapBox.x ||
    mapBox.x + mapBox.width <= dockBox.x ||
    dockBox.y + dockBox.height <= mapBox.y ||
    mapBox.y + mapBox.height <= dockBox.y
  ).toBe(true);
  const stageBox = await bounds(page.locator(".graph-stage"));
  for (const box of [dockBox, mapBox]) {
    expect(box.x).toBeGreaterThanOrEqual(canvasBox.x - 1);
    expect(box.x + box.width).toBeLessThanOrEqual(canvasBox.x + canvasBox.width + 1);
    expect(box.y).toBeGreaterThanOrEqual(stageBox.y - 1);
    expect(box.y + box.height).toBeLessThanOrEqual(stageBox.y + stageBox.height + 1);
  }

  // Both controls still accept clicks at the narrow canvas width.
  await navigation.getByRole("button", { name: "Zoom Out" }).click();
  await minimap.getByRole("button", { name: "Collapse minimap" }).click();
});
