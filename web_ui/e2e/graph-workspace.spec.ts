import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

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
  await page.getByRole("button", { name: "Collapse minimap" }).click();
  await expect(page.getByRole("button", { name: "Expand minimap" })).toHaveAttribute("aria-expanded", "false");
});
