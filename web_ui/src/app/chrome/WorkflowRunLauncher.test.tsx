import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { WorkflowRunLauncher } from "./WorkflowRunLauncher";
import type { DetachedRequest } from "./workflowApi";

const spec: WorkflowSpec = {
  schema_version: 1, workflow_id: "w", revision: "r", name: "Example",
  inputs: [
    { name: "draft", kind: "text", required: true },
    { name: "other", kind: "text", required: true },
    { name: "options", kind: "json", required: false },
    { name: "rows", kind: "table", required: false },
    { name: "attachment", kind: "file", required: false },
    { name: "picture", kind: "image", required: false },
  ],
  steps: [], outputs: [],
};
const asset = { asset_id: "a", sha256: "a".repeat(64), media_type: "text/plain", filename: "sample.txt", size_bytes: 5 };
const toggle = () => screen.getByRole("switch", { name: "Edit as JSON" });
const jsonEditor = () => screen.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" }) as HTMLTextAreaElement;
const field = (name: string) => screen.getByRole("textbox", { name });
const request = (parameters: DetachedRequest["parameters"]): DetachedRequest => ({ spec, parameters });
const editJson = (value: unknown) => fireEvent.change(jsonEditor(), { target: { value: JSON.stringify(value, null, 2) } });
const preview = { fingerprint: "f", estimated_cost_usd: 0, requires_approval: false, steps: [], control_flow: {} };
afterEach(() => vi.unstubAllGlobals());

it("preserves a partially filled form instead of restoring sample values", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(<WorkflowRunLauncher spec={spec} examples={{ draft: { kind: "text", text: "example" }, other: { kind: "text", text: "example other" } }}
    workspace={3} onStarted={vi.fn()} />);
  fireEvent.change(field("Draft"), { target: { value: "My unfinished draft" } });
  await user.clear(field("Other"));
  await user.click(toggle());
  expect(JSON.parse(jsonEditor().value)).toEqual(request({ draft: { kind: "text", text: "My unfinished draft" } }));
  await user.click(toggle());
  expect(field("Draft")).toHaveValue("My unfinished draft");
  expect(field("Other")).toHaveValue("");
  await user.click(screen.getByRole("button", { name: "Preview workflow" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Enter a value for “other”.");
  expect(fetchMock).not.toHaveBeenCalled();
  await user.click(toggle());
  expect(JSON.parse(jsonEditor().value)).toEqual(request({ draft: { kind: "text", text: "My unfinished draft" } }));
});

it("round-trips edited typed values, invalidates the old preview, and previews the latest form values", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => preview });
  vi.stubGlobal("fetch", fetchMock);
  render(<WorkflowRunLauncher spec={spec} workspace={3} onStarted={vi.fn()} />);
  fireEvent.change(field("Draft"), { target: { value: "old" } });
  fireEvent.change(field("Other"), { target: { value: "other" } });
  await user.click(screen.getByRole("button", { name: "Preview workflow" }));
  expect(await screen.findByRole("button", { name: "Start reviewed workflow" })).toBeEnabled();
  await user.click(toggle());
  expect(screen.queryByRole("button", { name: "Start reviewed workflow" })).toBeNull();
  const edited = request({
    draft: { kind: "text", text: "new" },
    other: { kind: "text", text: "updated" },
    options: { kind: "json", value: { limit: 5, active: true } },
    rows: { kind: "table", columns: ["name", "count", "identifier", "flag", "empty", "missing"],
      rows: [["Ops, east", 3, "001", true, "", null]] },
  });
  editJson(edited);
  await user.click(toggle());
  expect(toggle()).not.toBeChecked();
  expect(field("Draft")).toHaveValue("new");
  expect(field("Other")).toHaveValue("updated");
  expect(field("Options (optional)")).toHaveValue(JSON.stringify({ limit: 5, active: true }, null, 2));
  expect(field("Rows (optional)")).toHaveValue(JSON.stringify({
    columns: ["name", "count", "identifier", "flag", "empty", "missing"],
    rows: [["Ops, east", 3, "001", true, "", null]],
  }, null, 2));
  await user.click(toggle());
  expect(JSON.parse(jsonEditor().value)).toEqual(edited);
  await user.click(toggle());
  await user.click(screen.getByRole("button", { name: "Preview workflow" }));
  expect(await screen.findByRole("button", { name: "Start reviewed workflow" })).toBeEnabled();
  expect(fetchMock).toHaveBeenLastCalledWith("/api/workflows/3/preview", expect.objectContaining({
    method: "POST", body: JSON.stringify(edited),
  }));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("blocks switching from malformed form JSON and keeps all the drafts", async () => {
  const user = userEvent.setup();
  render(<WorkflowRunLauncher spec={spec} workspace={3} onStarted={vi.fn()} />);
  fireEvent.change(field("Draft"), { target: { value: "keep me" } });
  fireEvent.change(field("Options (optional)"), { target: { value: "{broken" } });
  await user.click(toggle());
  expect(toggle()).not.toBeChecked();
  expect(screen.getByRole("alert")).toHaveTextContent("“options” must be valid JSON.");
  expect(field("Draft")).toHaveValue("keep me");
  expect(field("Options (optional)")).toHaveValue("{broken");
  fireEvent.change(field("Options (optional)"), { target: { value: "{}" } });
  await user.click(toggle());
  expect(JSON.parse(jsonEditor().value)).toEqual(request({ draft: { kind: "text", text: "keep me" }, options: { kind: "json", value: {} } }));
});

it("keeps malformed request JSON in the editor until corrected", async () => {
  const user = userEvent.setup();
  render(<WorkflowRunLauncher spec={spec} workspace={3} onStarted={vi.fn()} />);
  await user.click(toggle());
  fireEvent.change(jsonEditor(), { target: { value: "{broken" } });
  await user.click(toggle());
  expect(toggle()).toBeChecked();
  expect(jsonEditor()).toHaveValue("{broken");
  expect(screen.getByRole("alert")).toHaveTextContent("The request isn't valid JSON.");
  editJson(request({ draft: { kind: "text", text: "corrected" } }));
  await user.click(toggle());
  expect(field("Draft")).toHaveValue("corrected");
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  ["file", request({ attachment: { kind: "file", asset } })],
  ["image", request({ picture: { kind: "image", asset: { ...asset, media_type: "image/png", filename: "picture.png" } } })],
  ["malformed table", request({ rows: { kind: "table", columns: ["identifier"], rows: [["001", "extra"]] } })],
  ["unknown input", request({ unexpected: { kind: "text", text: "extra" } })],
  ["extra request field", { ...request({}), extra: "keep" }],
  ["edited workflow", { ...request({}), spec: { ...spec, name: "Different workflow" } }],
] as const)("keeps the JSON editor open for an unrepresentable %s", async (_label, edited) => {
  const user = userEvent.setup();
  render(<WorkflowRunLauncher spec={spec} workspace={3} onStarted={vi.fn()} />);
  await user.click(toggle());
  editJson(edited);
  const before = jsonEditor().value;
  await user.click(toggle());
  expect(toggle()).toBeChecked();
  expect(jsonEditor()).toHaveValue(before);
  expect(screen.getByRole("alert")).toHaveTextContent("Keep editing as JSON");
  editJson(request({ draft: { kind: "text", text: "safe" } }));
  await user.click(toggle());
  expect(field("Draft")).toHaveValue("safe");
});

it("opens typed examples the form cannot represent directly in JSON", () => {
  const examples = { attachment: { kind: "file", asset }, draft: { kind: "text", text: "example" } } as const;
  render(<WorkflowRunLauncher spec={spec} examples={examples} workspace={3} onStarted={vi.fn()} />);
  expect(toggle()).toBeChecked();
  expect(JSON.parse(jsonEditor().value)).toEqual(request(examples));
});
