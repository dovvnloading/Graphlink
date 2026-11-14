import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

test("discovers models, switches chat model and receives its streamed reply", async ({ page }) => {
  const models = ["lab/default-chat", "lab/second-chat"];
  const requests: string[] = [];
  const server = createServer(async (req, res) => {
    if (req.headers.authorization !== "Bearer graphlink-test") {
      res.writeHead(401).end();
      return;
    }
    if (req.method === "GET" && req.url === "/v1/models") {
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
        object: "list", data: models.map((id) => ({ id, object: "model" })),
      }));
      return;
    }
    if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
      res.writeHead(404).end();
      return;
    }
    const body: Buffer[] = [];
    for await (const chunk of req) body.push(chunk);
    const { model, stream } = JSON.parse(Buffer.concat(body).toString());
    requests.push(model);
    const answer = `Reply from ${model}`;
    if (stream) {
      res.writeHead(200, { "content-type": "text/event-stream" });
      for (const [delta, finish_reason] of [[{ content: answer }, null], [{}, "stop"]]) {
        res.write(`data: ${JSON.stringify({ id: "test", model, choices: [{ index: 0, delta, finish_reason }] })}\n\n`);
      }
      res.end("data: [DONE]\n\n");
    } else {
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
        model, choices: [{ message: { role: "assistant", content: answer }, finish_reason: "stop" }],
      }));
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const endpoint = `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`;
  try {
    await gotoApp(page);
    const settings = page.getByRole("dialog", { name: "Settings" });
    await page.locator('[data-overlay-trigger="settings"]').click();
    await settings.getByRole("button", { name: "API Endpoint", exact: true }).click();
    await settings.getByRole("button", { name: /Choose provider/ }).click();
    const search = settings.getByRole("combobox", { name: "Search providers" });
    await search.fill("Grok");
    await search.press("Enter");
    await expect(settings.getByRole("button", { name: "Choose provider, xAI" })).toBeVisible();
    await expect(settings.getByText("https://api.x.ai/v1", { exact: true })).toBeVisible();
    await settings.getByRole("button", { name: /Choose provider/ }).click();
    await search.fill("Custom endpoint");
    await search.press("Enter");
    await settings.getByLabel("Base URL", { exact: true }).fill(endpoint);
    await settings.getByLabel("API Key", { exact: true }).fill("graphlink-test");
    await settings.getByRole("button", { name: "Load available models" }).click();
    await expect(settings.locator("datalist option").first()).toHaveAttribute("value", models[0]);
    await settings.getByLabel("Default text model", { exact: true }).fill(models[0]);
    await settings.getByRole("button", { name: "Save and activate" }).click();
    await expect(settings.locator(".cloud-route strong")).toHaveText("Custom endpoint");
    await page.keyboard.press("Escape");
    const picker = page.locator('[data-overlay-trigger="model"]');
    await expect(picker).toContainText(models[0]);
    await picker.click();
    await page.getByRole("dialog", { name: "Choose a model" }).getByRole("button", { name: models[1] }).click();
    await expect(picker).toContainText(models[1]);
    await page.getByRole("textbox", { name: "Message composer" }).fill("Hello");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect.poll(() => requests).toContain(models[1]);
    await expect(page.getByRole("group", { name: "Canvas overview, 2 nodes" })).toBeVisible();
    await page.getByRole("button", { name: "Fit All", exact: true }).click();
    await expect(page.locator(".react-flow__node").filter({ hasText: `Reply from ${models[1]}` }).first()).toBeVisible();

    // Restore the shared E2E session for the other specs.
    await page.locator('[data-overlay-trigger="settings"]').click();
    await settings.getByRole("button", { name: "API Endpoint", exact: true }).click();
    await settings.getByRole("button", { name: "Reset API Settings", exact: true }).click();
    await settings.getByRole("button", { name: "Confirm Reset", exact: true }).click();
    await expect(settings.locator(".cloud-route strong")).toHaveText("Ollama (Local)");
    await settings.getByRole("button", { name: "General", exact: true }).click();
    await expect(settings.getByRole("region", { name: "General" })).toBeVisible();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
