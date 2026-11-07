import { describe, expect, it } from "vitest";
import { hiddenGroupMemberIds } from "./collapsedGroups";

type Row = { id: string; kind: string; isCollapsed: boolean; itemIds: string[] };
const node = (id: string, kind = "chat", itemIds: string[] = [], isCollapsed = false): Row =>
  ({ id, kind, itemIds, isCollapsed });

describe("hiddenGroupMemberIds (collapsed semantic groups)", () => {
  it("hides all transitive descendants including nested group wrappers", () => {
    const nodes = [
      node("outer", "container", ["inner", "a"], true),
      node("inner", "frame", ["b"]),
      node("a"),
      node("b"),
      node("outside"),
    ];
    expect(hiddenGroupMemberIds(nodes)).toEqual(new Set(["inner", "a", "b"]));
    expect(hiddenGroupMemberIds(nodes).has("outside")).toBe(false);
    expect(hiddenGroupMemberIds(nodes).has("outer")).toBe(false);
  });

  it("hides dual-membership leaves when either independent owner collapses", () => {
    const nodes = [
      node("frame", "frame", ["shared"], true),
      node("container", "container", ["shared", "other"], false),
      node("shared"),
      node("other"),
    ];
    expect(hiddenGroupMemberIds(nodes)).toEqual(new Set(["shared"]));
  });

  it("guards missing nodes and legacy cycles without looping", () => {
    const nodes = [
      node("a", "container", ["b", "ghost"], true),
      node("b", "container", ["a"]),
    ];
    expect(hiddenGroupMemberIds(nodes)).toEqual(new Set(["a", "b"]));
  });
});
