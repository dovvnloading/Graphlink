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
      const [navigationBox, minimapBox] = await Promise.all([bounds(navigation), bounds(minimap)]);
      expect(navigationBox.x + navigationBox.width).toBeLessThanOrEqual(minimapBox.x + 1);
      for (const box of [navigationBox, minimapBox]) {
        expect(box.x).toBeGreaterThanOrEqual(graphBox.x - 1);
        expect(box.x + box.width).toBeLessThanOrEqual(graphBox.x + graphBox.width + 1);
        expect(box.y).toBeGreaterThanOrEqual(graphBox.y - 1);
        expect(box.y + box.height).toBeLessThanOrEqual(graphBox.y + graphBox.height + 1);
      }
      await expect(input).toBeInViewport({ ratio: 1 });
      await expect(footer.getByRole("button", { name: "Send message" })).toBeInViewport({ ratio: 1 });

      for (const [name, label] of [["reasoning", "Choose a reasoning level"], ["model", "Choose a model"]]) {
        const trigger = footer.locator(`[data-overlay-trigger="${name}"]`);
        await expect(trigger).toBeInViewport({ ratio: 1 });
        if (await trigger.isDisabled()) continue; // Options depend on the provider and installed models.
        await trigger.click();
        const picker = page.getByRole("dialog", { name: label });
        await expect(picker).toBeInViewport({ ratio: 1 });
        await picker.getByRole("button").first().click();
        await expect(picker).toBeHidden();
        await expect(input).toHaveValue(draft);
      }
    });
  }

  await page.getByRole("button", { name: "Collapse minimap" }).click();
  await expect(page.getByRole("button", { name: "Expand minimap" })).toHaveAttribute("aria-expanded", "false");
});
