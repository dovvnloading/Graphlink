import { describe, expect, it } from "vitest";
import type { AppPluginCategory } from "../../lib/bridge-core/generated/app-plugins-state";
import { searchPlugins } from "./pluginPickerSearch";

const categories: AppPluginCategory[] = [
  {
    name: "Build & Execution",
    description: "Write and run code.",
    plugins: [
      { name: "Virtual Environment Runner", description: "Python workspace." },
      { name: "Code Helper", description: "Review Python code." },
    ],
  },
  {
    name: "Branch Foundations",
    description: "Organize canvas nodes.",
    plugins: [
      { name: "Conversation Node", description: "A linear chat node." },
      { name: "Crème Helper", description: "Create annotated nodes." },
    ],
  },
];

describe("searchPlugins", () => {
  it("returns no hits for empty and whitespace queries", () => {
    expect(searchPlugins(categories, "")).toEqual([]);
    expect(searchPlugins(categories, "  \t ")).toEqual([]);
  });

  it("searches names, descriptions, and categories across the whole picker", () => {
    expect(searchPlugins(categories, "conversation").map((hit) => hit.plugin.name))
      .toEqual(["Conversation Node"]);
    expect(searchPlugins(categories, "workspace").map((hit) => hit.plugin.name))
      .toEqual(["Virtual Environment Runner"]);
    expect(searchPlugins(categories, "branch foundations").map((hit) => hit.plugin.name))
      .toEqual(["Conversation Node", "Crème Helper"]);
  });

  it("allows separate terms to match different fields, case-insensitively", () => {
    expect(searchPlugins(categories, "PYTHON helper").map((hit) => hit.plugin.name))
      .toEqual(["Code Helper"]);
    expect(searchPlugins(categories, "branch chat").map((hit) => hit.plugin.name))
      .toEqual(["Conversation Node"]);
  });

  it("handles diacritics and reports no matches without inventing results", () => {
    expect(searchPlugins(categories, "creme").map((hit) => hit.plugin.name))
      .toEqual(["Crème Helper"]);
    expect(searchPlugins(categories, "nonsense")).toEqual([]);
  });

  it("prioritizes name matches over description matches with stable ties", () => {
    expect(searchPlugins(categories, "code").map((hit) => hit.plugin.name))
      .toEqual(["Code Helper", "Virtual Environment Runner"]);
  });

  it("keeps the backend entry/category association for execution and display", () => {
    const [hit] = searchPlugins(categories, "conversation");
    expect(hit.categoryName).toBe("Branch Foundations");
    expect(hit.plugin.description).toBe("A linear chat node.");
  });
});
