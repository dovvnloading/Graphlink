import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";

test.use({ viewport: { width: 1440, height: 1200 }, contextOptions: { reducedMotion: "reduce" } });

async function grantCore(page: Page, enabled: boolean) {
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  const previous = await settings.getByRole("region").getAttribute("aria-label");
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  const grant = settings.getByRole("checkbox", { name: "Granted: Scientific Core", exact: true });
  const before = await grant.isChecked();
  if (before !== enabled) await grant.click();
  await expect(grant).toBeChecked({ checked: enabled });
  if (previous && previous !== "Plugins") await settings.getByRole("button", { name: previous, exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
  return before;
}

test("discovers an authorized operation by keyboard and restores its draft across focused and collapsed canvas surfaces", async ({ page, baseURL }) => {
  // The ordinary suite uses its isolated loopback backend. A token-required
  // test server can exercise the desktop's real fragment boot path as well.
  const testToken = process.env.GRAPHLINK_E2E_TEST_TOKEN;
  if (testToken && baseURL) {
    await page.addInitScript(({ token, origin }) => {
      if (location.origin === origin && !location.hash) {
        history.replaceState(null, "", location.pathname + location.search + "#token=" + encodeURIComponent(token));
      }
    }, { token: testToken, origin: new URL(baseURL).origin });
  }
  await gotoApp(page);
  const originallyGranted = await grantCore(page, false);
  const picker = page.locator('[data-overlay-trigger="plugins"]');
  const search = page.getByRole("searchbox", { name: "Search plugins" });
  const operation = "Plot paired samples";
  const dialog = page.getByRole("dialog", { name: "Scientific Core workbench" });
  const node = page.locator(".react-flow__node-scientific");
  const fieldName = "Horizontal coordinates";
  const draft = "[100.125, 200.25, 300.375]";
  try {
    await picker.click();
    await search.fill(operation);
    await expect(page.getByRole("button", { name: /Plot paired samples/ })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await grantCore(page, true);
    await picker.click();
    await search.fill(operation);
    const match = page.getByRole("list", { name: "Plugin search results" })
      .getByRole("button", { name: /Plot paired samples.*Scientific Core/ });
    await expect(match).toHaveCount(1);
    await search.press("ArrowDown");
    await expect(match).toBeFocused();
    await match.press("Enter");
    await expect(dialog).toBeVisible();
    await expect(node).toHaveCount(1);
    await expect(dialog.getByLabel("Operation", { exact: true })).toHaveValue("plot_xy");
    await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    await expect(page.getByLabel(fieldName, { exact: true })).toHaveCount(1);
    await dialog.getByLabel(fieldName, { exact: true }).fill(draft);
    await expect(dialog.getByRole("region", { name: "Latest result" })).toHaveCount(0);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(picker).toBeFocused();
    await page.getByRole("button", { name: "Fit All" }).click();
    const inline = node.getByLabel(fieldName, { exact: true });
    await expect(inline).toHaveValue(draft);

    const zoomOut = page.getByRole("button", { name: "Zoom Out" });
    for (let i = 0; i < 12 && await inline.count(); i++) await zoomOut.click();
    await expect(node.locator(".scientific-node.collapsed")).toBeAttached();
    await expect(inline).toHaveCount(0);
    const opener = node.getByRole("button", { name: "Open focused workbench for Scientific Core" });
    await opener.focus();
    await opener.press("Enter");
    await expect(dialog.getByLabel(fieldName, { exact: true })).toHaveValue(draft);
    await expect(node.locator(".scene-node-body")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Run calculation", exact: true }).click();
    await expect(dialog.getByRole("region", { name: "Latest result" })).toContainText(operation);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await page.getByRole("button", { name: "Fit All" }).click();
    await expect(node.getByRole("region", { name: "Latest result" })).toContainText(operation);
    await expect(node).toHaveCount(1);
  } finally {
    await page.keyboard.press("Escape");
    await grantCore(page, originallyGranted);
  }
});

test("keeps reviewed import data and keyboard actions within the focused workbench at narrow widths", async ({ page }, testInfo) => {
  await gotoApp(page);
  const priorGrant = await grantCore(page, true);
  const dialog = page.getByRole("dialog", { name: "Scientific Core workbench" });
  try {
    await page.locator('[data-overlay-trigger="plugins"]').click();
    const search = page.getByRole("searchbox", { name: "Search plugins" });
    await search.fill("Plot paired samples");
    await search.press("Enter");
    await expect(dialog).toBeVisible();
    await dialog.getByText("Import a data table", { exact: true }).click();
    await dialog.getByLabel("Pasted table text").fill("Time (s),Distance (mm)\n1,10\n2,NA\n3,30");
    await dialog.getByRole("button", { name: "Preview table", exact: true }).click();
    await expect(dialog.getByRole("table", { name: "Pending import preview" })).toContainText("Missing");
    const apply = dialog.getByRole("button", { name: "Apply reviewed table" });
    for (const [width, height, forcedColors] of [[1440, 1200, "none"], [640, 360, "none"], [320, 720, "active"]] as const) {
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ forcedColors, reducedMotion: "reduce" });
      await apply.focus();
      await expect(apply).toBeFocused();
      await expect.poll(() => apply.evaluate((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.left >= -1 && box.right <= document.documentElement.clientWidth + 1
          && box.top >= -1 && box.bottom <= window.innerHeight + 1;
      })).toBe(true);
      const image = testInfo.outputPath(`reviewed-import-${width}.png`);
      await page.screenshot({ path: image });
      await testInfo.attach(`reviewed-import-${width}`, { path: image, contentType: "image/png" });
    }
    await apply.press("Enter");
    await expect(dialog.getByRole("status").filter({ hasText: "Pasted data · 3 rows · 2 columns" })).toBeVisible();
  } finally {
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1440, height: 1200 });
    await page.emulateMedia({ forcedColors: "none" });
    await grantCore(page, priorGrant);
  }
});
