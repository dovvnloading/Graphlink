import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { gotoApp } from "./helpers";

test.use({ viewport: { width: 1440, height: 1200 } });

const serverName = "Project records (MCP browser test)";
const fixturePath = fileURLToPath(new URL("../../tests_e2e/fixture_mcp_server.py", import.meta.url));

/** Real SPA, backend, subprocess discovery, tool call and saved result. */
test("MCP Connect discovers a tool, reviews inputs and saves a table with provenance", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await gotoApp(page);
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "General", exact: true }).click();
  const theme = settings.getByRole("radiogroup", { name: "Theme", exact: true });
  const initialTheme = await theme.getByRole("radio", { checked: true }).innerText();
  await theme.getByRole("radio", { name: "Dark", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  const grant = settings.getByRole("switch", { name: "Granted: MCP Connect", exact: true });
  await expect(grant).toBeVisible();
  if (await grant.getAttribute("aria-checked") !== "true") await grant.click();
  await expect(grant).toHaveAttribute("aria-checked", "true");
  await settings.getByRole("button", { name: "MCP Servers", exact: true }).click();
  await expect(settings.getByRole("heading", { name: "MCP servers", exact: true })).toBeVisible();
  // New chat resets the scene, but settings survive retries and reuse of a
  // local backend. Remove only entries owned by this browser journey.
  const serverRows = settings.locator(".mcp-settings-server").filter({
    has: page.getByRole("switch", { name: serverName, exact: true }),
  });
  for (let remaining = await serverRows.count(); remaining > 0; remaining--) {
    await serverRows.first().getByRole("button", { name: `Actions for ${serverName}`, exact: true }).click();
    await page.getByRole("menu", { name: `Actions for ${serverName}`, exact: true })
      .getByRole("menuitem", { name: "Remove", exact: true }).click();
    const remove = page.getByRole("dialog", { name: "Remove MCP server?", exact: true });
    await remove.getByRole("button", { name: "Remove server", exact: true }).click();
    await expect(serverRows).toHaveCount(remaining - 1);
  }
  await settings.getByLabel("Server name", { exact: true }).fill(serverName);
  await settings.getByLabel("Command", { exact: true }).fill(process.env.GRAPHLINK_E2E_PYTHON || "python");
  await settings.getByLabel("Arguments (optional, space-separated)", { exact: true })
    .fill(JSON.stringify([fixturePath]));
  await settings.getByRole("button", { name: "Add server", exact: true }).click();
  await expect(serverRows).toHaveCount(1);
  await serverRows.getByRole("button", { name: "Test connection and discover tools", exact: true }).click();
  await expect(serverRows.getByRole("checkbox", { name: "Look up records", exact: true })).toBeChecked();
  await serverRows.scrollIntoViewIfNeeded();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const settingsImage = testInfo.outputPath("mcp-settings-dark.png");
  await page.screenshot({ path: settingsImage, animations: "disabled" });
  await testInfo.attach("MCP server settings", { path: settingsImage, contentType: "image/png" });
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();

  await page.locator('[data-overlay-trigger="plugins"]').click();
  const picker = page.getByRole("dialog", { name: "Plugins" });
  await picker.getByRole("searchbox", { name: "Search plugins" }).fill("MCP Connect");
  await picker.getByRole("button", { name: /^MCP Connect/ }).click();
  const card = page.locator(".mcp-tool-node");
  await expect(card).toBeVisible();
  // Use the kit's keyboard path for the server selector.
  const serverSelect = card.getByRole("button", { name: "MCP server", exact: true });
  await serverSelect.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("listbox", { name: "MCP server", exact: true }).getByRole("option")
    .filter({ has: page.getByText(serverName, { exact: true }) }).click();
  await card.getByRole("button", { name: "Discover tools", exact: true }).click();
  await card.getByRole("button", { name: "Tool", exact: true }).click();
  await page.getByRole("listbox", { name: "Tool", exact: true }).getByRole("option", { name: /^Look up records/ }).click();
  // The schema makes the card taller than its initial empty state. Fit the
  // actual card before interacting with controls in its scrolling body.
  await expect(card.getByLabel("Query", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Fit All", exact: true }).click();
  await card.getByLabel("Query", { exact: true }).fill("Graphlink");
  await card.getByRole("button", { name: "Preview tool", exact: true }).click();
  const preview = card.getByRole("region", { name: "Tool preview" });
  await expect(preview).toContainText('"query": "Graphlink"');
  await expect(card.getByRole("region", { name: "Saved tool result" })).toHaveCount(0);
  await card.getByRole("button", { name: "Run tool", exact: true }).click();
  const result = card.getByRole("region", { name: "Saved tool result" });
  await expect(result).toBeVisible();
  await expect(result.getByRole("table", { name: "Tool output" })).toContainText("Graphlink 1");
  await expect(result).toContainText("Version 1");
  await result.scrollIntoViewIfNeeded();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const dark = testInfo.outputPath("mcp-result-dark.png");
  await page.screenshot({ path: dark, animations: "disabled" });
  await testInfo.attach("MCP tool table in dark theme", { path: dark, contentType: "image/png" });
  await result.getByRole("tab", { name: "Provenance", exact: true }).click();
  await expect(result).toContainText("Request");
  await expect(result).toContainText(/graph-[a-f0-9]{32}/);
  await result.getByRole("tab", { name: "JSON", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(result).toContainText('"summary"');
  // Apply the real preference: every settings snapshot reapplies it, so a
  // direct DOM override would silently revert during the journey.
  await page.locator('[data-overlay-trigger="settings"]').click();
  await settings.getByRole("button", { name: "General", exact: true }).click();
  await theme.getByRole("radio", { name: "Light", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
  await result.scrollIntoViewIfNeeded();
  const light = testInfo.outputPath("mcp-result-light.png");
  await page.screenshot({ path: light, animations: "disabled" });
  await testInfo.attach("MCP tool JSON in light theme", { path: light, contentType: "image/png" });
  await page.reload();
  await expect(page.locator('.app-shell[data-connection-status="open"]')).toBeAttached();
  await expect(page.locator(".mcp-tool-node").getByRole("region", { name: "Saved tool result" })).toContainText("Version 1");
  await page.locator('[data-overlay-trigger="settings"]').click();
  await settings.getByRole("button", { name: "General", exact: true }).click();
  await theme.getByRole("radio", { name: initialTheme, exact: true }).click();
  await expect(theme.getByRole("radio", { name: initialTheme, exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
});
