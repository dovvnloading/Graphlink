import { expect, test } from "@playwright/test";
import {
  action, collectIntents, confirmApply, evidence, openApp, openProposal, packet,
  prepare, startPython, state, wireIntent,
} from "./helpers";

test("Cancel leaves every target byte unchanged and never enters the writer", async ({ page, request }, info) => {
  const before = await prepare(request);
  const intents = collectIntents(page);
  await openApp(page);
  await openProposal(page);
  await page.getByRole("button", { name: "Apply selected (3)", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Apply Selected Changes?" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  const after = await state(request);
  expect(after.files).toEqual(before.files);
  expect(after.writes).toEqual([]);
  expect(intents.filter((row) => row.intent === "applyGitlinkChanges")).toEqual([]);
  await evidence(request, page, info, before, after, "Cancel sends no Apply intent and produces zero writes.");
});

test("approved subset writes exactly its paths and replay/double-submit adds no write", async ({ page, request }, info) => {
  const before = await prepare(request);
  const intents = collectIntents(page);
  await openApp(page);
  await openProposal(page);
  await page.getByRole("checkbox", { name: "Include second.py in Apply", exact: true }).uncheck();
  await action(request, "external-edit");
  const beforeApply = await state(request);
  await confirmApply(page);
  await expect.poll(async () => (await state(request)).change_state).toBe("applied");
  const approved = await state(request);
  expect(approved.writes.map((row) => row.path).sort()).toEqual(["checkout/first.py", "checkout/new.py"]);
  expect(approved.files["checkout/first.py"].hex).toBe(Buffer.from("first\n").toString("hex"));
  expect(approved.files["checkout/new.py"].hex).toBe(Buffer.from("reviewed new\n").toString("hex"));
  expect(approved.files["checkout/second.py"]).toEqual(beforeApply.files["checkout/second.py"]);
  expect(approved.files["outside-sentinel.txt"]).toEqual(before.files["outside-sentinel.txt"]);
  const submitted = intents.find((row) => row.intent === "applyGitlinkChanges");
  expect(submitted).toBeDefined();
  expect(submitted?.args).toEqual([before.node_id, before.fingerprint, ["first.py", "new.py"]]);
  await Promise.all([wireIntent(page, submitted!), wireIntent(page, submitted!)]);
  const after = await state(request);
  expect(after.writes).toEqual(approved.writes);
  expect(after.files).toEqual(approved.files);
  expect(after.error).toContain("no approved change set");
  await evidence(request, page, info, before, after,
    "Exactly two selected writes; unselected external edit preserved; two replayed envelopes produce zero extra writes.",
    { beforeApply, approved, submitted });
});

test("an open old confirmation and its old wire fingerprint cannot apply a replaced proposal", async ({ page, request }, info) => {
  const before = await prepare(request);
  await openApp(page);
  await openProposal(page);
  await page.getByRole("button", { name: "Apply selected (3)", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Apply Selected Changes?" });
  const replacement = await action(request, "replace-proposal");
  expect(replacement.fingerprint).not.toBe(before.fingerprint);
  await expect(dialog.getByRole("alert")).toContainText("proposal changed");
  await expect(dialog.getByRole("button", { name: "Yes", exact: true })).toBeDisabled();
  await wireIntent(page, packet("scene", "applyGitlinkChanges", [before.node_id, before.fingerprint, ["first.py"]]));
  const after = await state(request);
  expect(after.error).toContain("changed after approval");
  expect(after.files).toEqual(before.files);
  expect(after.writes).toEqual([]);
  await evidence(request, page, info, before, after,
    "The old dialog is disabled and old-fingerprint transport bypass is refused before writing.");
});

test("an external edit to the last target stops all selected writes", async ({ page, request }, info) => {
  await prepare(request);
  await openApp(page);
  await openProposal(page);
  const before = await action(request, "external-edit");
  await confirmApply(page);
  await expect.poll(async () => (await state(request)).error).toContain("changed since the preview");
  const after = await state(request);
  expect(after.files).toEqual(before.files);
  expect(after.writes).toEqual([]);
  await evidence(request, page, info, before, after, "Stale baseline refusal precedes the first write; independent bytes survive.");
});

test("unknown or escaping selection is rejected by real intent validation/dispatcher", async ({ page, request }, info) => {
  const before = await prepare(request);
  await openApp(page);
  const malformed = await wireIntent(page, {
    ...packet("scene", "applyGitlinkChanges", []), args: {},
  });
  expect(malformed.kind).toBe("error");
  expect(String(malformed.error)).toContain("Invalid arguments");
  await wireIntent(page, packet("scene", "applyGitlinkChanges", [before.node_id, before.fingerprint, ["../outside-sentinel.txt"]]));
  const after = await state(request);
  expect(after.error).toContain("no longer match");
  expect(after.files).toEqual(before.files);
  expect(after.writes).toEqual([]);
  await evidence(request, page, info, before, after, "Malformed/unknown selections cannot reach any file writer.");
});

test("newline-only bytes are disclosed in the real UI and an approved action writes those exact bytes", async ({ page, request }, info) => {
  const before = await prepare(request);
  await openApp(page);
  await openProposal(page);
  await page.getByRole("button", { name: "Review update first.py", exact: true }).click();
  const preview = page.getByLabel("Gitlink file preview", { exact: true });
  await expect(preview).toContainText("Bytes changed");
  await expect(preview).toContainText("Line endings");
  await expect(preview).toContainText("CRLF=1");
  await expect(preview).toContainText("LF=1");
  await expect(preview).not.toContainText("No textual diff available");
  await info.attach("newline-preview.png", { body: await page.screenshot(), contentType: "image/png" });
  await page.getByRole("checkbox", { name: "Include second.py in Apply", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Include new.py in Apply", exact: true }).uncheck();
  await confirmApply(page);
  await expect.poll(async () => (await state(request)).change_state).toBe("applied");
  const after = await state(request);
  expect(after.writes).toHaveLength(1);
  expect(after.files["checkout/first.py"].hex).toBe("66697273740a");
  expect(after.files["checkout/second.py"]).toEqual(before.files["checkout/second.py"]);
  expect(after.files["checkout/new.py"]).toBeUndefined();
  await evidence(request, page, info, before, after, "CRLF→LF is visibly disclosed; one approved change writes exact LF bytes.");
});

test("long Gitlink review scrolls/selects without moving the canvas and preserves exact spaced-path selection", async ({ page, request }, info) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const before = await prepare(request, "gitlink-long");
  const intents = collectIntents(page);
  await openApp(page);
  await openProposal(page);
  const singleLabel = '"a\\u0020b.py"';
  const doubleLabel = '"a\\u0020\\u0020b.py"';
  const singleReview = page.getByRole("button", { name: `Review update ${singleLabel}`, exact: true });
  const doubleReview = page.getByRole("button", { name: `Review update ${doubleLabel}`, exact: true });
  await expect(singleReview).toContainText(singleLabel);
  await expect(doubleReview).toContainText(doubleLabel);
  await page.getByRole("checkbox", { name: `Include ${doubleLabel} in Apply`, exact: true }).uncheck();
  await singleReview.click();
  const preview = page.getByRole("region", { name: "Gitlink file preview", exact: true });
  const body = page.locator(".gitlink-node-content");
  const viewport = page.locator(".react-flow__viewport");
  // Opening the long proposal expands the initially framed Setup card. Use
  // the real viewport control to keep its bottom clear of the floating
  // composer; body-relative bounds alone do not establish visible text.
  await page.getByRole("button", { name: "Fit All", exact: true }).click();
  await expect.poll(async () => body.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth
      && [rect.top + 16, rect.bottom - 16].every((y) =>
        [rect.left + 16, rect.right - 16].every((x) => element.contains(document.elementFromPoint(x, y))));
  })).toBe(true);
  const transform = await viewport.evaluate((element) => getComputedStyle(element).transform);
  await expect(preview).toContainText(`### ${singleLabel} [update]`);
  await expect(preview).toContainText("Bytes changed");
  await expect(preview).not.toContainText(`### ${doubleLabel}`);
  expect(await body.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await preview.focus();
  await preview.press("Home");
  await expect.poll(async () => body.evaluate((element) => element.scrollTop)).toBe(0);
  const visiblePoint = await preview.evaluate((element) => {
    const previewRect = element.getBoundingClientRect();
    const bodyRect = element.closest(".gitlink-node-content")!.getBoundingClientRect();
    return { x: previewRect.left + 40, y: Math.min(previewRect.top + 40, bodyRect.bottom - 40) };
  });
  await page.mouse.move(visiblePoint.x, visiblePoint.y);
  await page.mouse.wheel(0, 700);
  await expect.poll(async () => body.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await viewport.evaluate((element) => getComputedStyle(element).transform)).toBe(transform);
  await preview.press("End");
  await expect.poll(async () => body.evaluate((element) =>
    element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  const tail = await preview.evaluate((element) => {
    const textNode = element.querySelector("code")?.firstChild;
    const marker = "REVIEWED_SINGLE_SPACE_TAIL";
    const start = (textNode?.textContent ?? "").indexOf(marker);
    if (!textNode || start < 0) throw new Error("The reviewed tail is missing");
    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, start + marker.length);
    const bounds = range.getBoundingClientRect();
    const clip = element.closest(".gitlink-node-content")!.getBoundingClientRect();
    const previewBounds = element.getBoundingClientRect();
    const middleY = (bounds.top + bounds.bottom) / 2;
    return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom,
      clipLeft: Math.max(clip.left, previewBounds.left, 0),
      clipRight: Math.min(clip.right, previewBounds.right, innerWidth),
      clipTop: Math.max(clip.top, previewBounds.top, 0),
      clipBottom: Math.min(clip.bottom, previewBounds.bottom, innerHeight),
      startHitsPreview: element.contains(document.elementFromPoint(bounds.left, middleY)),
      endHitsPreview: element.contains(document.elementFromPoint(bounds.right, middleY)) };
  });
  expect(tail.left).toBeGreaterThanOrEqual(tail.clipLeft);
  expect(tail.right).toBeLessThanOrEqual(tail.clipRight);
  expect(tail.top).toBeGreaterThanOrEqual(tail.clipTop);
  expect(tail.bottom).toBeLessThanOrEqual(tail.clipBottom);
  expect(tail.startHitsPreview).toBe(true);
  expect(tail.endHitsPreview).toBe(true);
  const middleY = (tail.top + tail.bottom) / 2;
  await page.mouse.move(tail.left, middleY);
  await page.mouse.down();
  await page.mouse.move(tail.right, middleY, { steps: 8 });
  await page.mouse.up();
  expect(await page.evaluate(() => getSelection()?.toString())).toContain("REVIEWED_SINGLE_SPACE_TAIL");
  expect(await viewport.evaluate((element) => getComputedStyle(element).transform)).toBe(transform);
  await info.attach("gitlink-tail-selected.png", { body: await page.screenshot(), contentType: "image/png" });
  await confirmApply(page);
  await expect.poll(async () => (await state(request)).change_state).toBe("applied");
  const after = await state(request);
  const expectedText = Array.from({ length: 60 }, (_, index) => `reviewed single ${index}\n`).join("")
    + "REVIEWED_SINGLE_SPACE_TAIL\n";
  expect(after.writes.map((row) => row.path)).toEqual(["checkout/a b.py"]);
  expect(after.files["checkout/a b.py"].hex).toBe(Buffer.from(expectedText).toString("hex"));
  expect(after.files["checkout/a  b.py"]).toEqual(before.files["checkout/a  b.py"]);
  expect(after.files["outside-sentinel.txt"]).toEqual(before.files["outside-sentinel.txt"]);
  expect(intents.find((row) => row.intent === "applyGitlinkChanges")?.args)
    .toEqual([before.node_id, before.fingerprint, ["a b.py"]]);
  await evidence(request, page, info, before, after,
    "Long review is reachable by wheel/keyboard and selectable; escaped one-space/two-space labels remain distinct; exact selected raw path alone is written.",
    { reviewInteraction: { tail, transform, reducedMotion: true, framedWith: "Fit All" } });
});

test("full Python source, existing session grant, repeated source, stale approval replay, and denial", async ({ page, request }, info) => {
  const before = await prepare(request, "python");
  const intents = collectIntents(page);
  await openApp(page);
  await startPython(page);
  const panel = page.getByRole("group", { name: "Agent tool approval" });
  const summary = panel.locator(".plan-node-approval-summary");
  expect(before.source.length).toBeGreaterThan(400);
  expect(await summary.textContent()).toBe(`python.exec\n--- source\n${before.source}`);
  await expect(panel.getByRole("button", { name: "Always allow this tool" })).toHaveCount(0);
  expect((await state(request)).files).toEqual(before.files);
  await info.attach("full-python-source.png", { body: await page.screenshot(), contentType: "image/png" });
  // Reading DOM text alone would miss a canvas that steals the wheel or text
  // drag. Exercise actual pointer/keyboard input and inspect the visible tail.
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.evaluate((element) => getComputedStyle(element).transform);
  expect(await summary.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await summary.hover();
  await page.mouse.wheel(0, 800);
  await expect.poll(async () => summary.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await viewport.evaluate((element) => getComputedStyle(element).transform)).toBe(transform);
  await summary.focus();
  await summary.press("Home");
  await expect.poll(async () => summary.evaluate((element) => element.scrollTop)).toBe(0);
  await summary.press("End");
  await expect.poll(async () => summary.evaluate((element) =>
    element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  const tail = await summary.evaluate((element) => {
    const textNode = element.firstChild;
    const text = textNode?.textContent ?? "";
    const start = text.indexOf("print('APPROVED_TAIL'");
    if (!textNode || start < 0) throw new Error("Exact source tail is missing");
    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, text.indexOf("\n", start));
    const bounds = range.getBoundingClientRect();
    const container = element.getBoundingClientRect();
    return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom,
      containerTop: container.top, containerBottom: container.bottom };
  });
  expect(tail.top).toBeGreaterThanOrEqual(tail.containerTop);
  expect(tail.bottom).toBeLessThanOrEqual(tail.containerBottom);
  const middleY = (tail.top + tail.bottom) / 2;
  await page.mouse.move(tail.left + 1, middleY);
  await page.mouse.down();
  await page.mouse.move(tail.right - 1, middleY, { steps: 8 });
  await page.mouse.up();
  expect(await page.evaluate(() => getSelection()?.toString())).toContain("APPROVED_TAIL");
  expect(await viewport.evaluate((element) => getComputedStyle(element).transform)).toBe(transform);
  await info.attach("python-source-tail-selected.png", { body: await page.screenshot(), contentType: "image/png" });
  const first = await state(request);
  expect(first.approval_id).not.toBe("");
  await panel.getByRole("button", { name: "Approve once", exact: true }).click();
  await expect.poll(async () => {
    const current = (await state(request)).approval_id;
    return current !== "" && current !== first.approval_id;
  }).toBe(true);
  await expect(panel).toBeVisible();
  const second = await state(request);
  expect(second.approval_id).not.toBe("");
  expect(second.files["checkout/counter.txt"].hex).toBe("31");
  expect(await summary.textContent()).toBe(`python.exec\n--- source\n${before.source}`);
  const firstApproval = intents.find((row) => row.intent === "approveTool");
  expect(firstApproval?.args).toEqual([first.request_id, first.approval_id]);
  await wireIntent(page, firstApproval!);
  await wireIntent(page, packet("harness", "approveToolForSession", [second.request_id]));
  await wireIntent(page, packet("harness", "answer", [second.request_id, "yes"]));
  const stillWaiting = await state(request);
  expect(stillWaiting.approval_id).toBe(second.approval_id);
  expect(stillWaiting.awaiting_approval).toBe(true);
  expect(stillWaiting.pending_approval_resolved).toBe(false);
  expect(stillWaiting.files).toEqual(second.files);
  await panel.getByRole("button", { name: "Approve once", exact: true }).click();
  await expect(summary).toHaveText(`python.exec\n--- source\n${before.different_source}`);
  const third = await state(request);
  expect(third.approval_id).not.toBe(second.approval_id);
  expect(third.files["checkout/counter.txt"].hex).toBe("32");
  expect(third.files["checkout/different.txt"]).toBeUndefined();
  await panel.getByRole("button", { name: "Deny", exact: true }).click();
  await expect.poll(async () => (await state(request)).harness_status).toBe("done");
  const after = await state(request);
  expect(after.files).toEqual(third.files);
  expect(after.activity.map((row) => row.outcome)).toEqual(["ok", "ok", "error"]);
  expect(after.activity[2].summary).toContain("denied approval");
  await evidence(request, page, info, before, after,
    "Three fresh call authorizations despite session grant; full code exact; stale/alternate intents cannot approve; two approved identical calls observe counter 1→2; denied different code never runs.",
    { firstApproval, first, second, stillWaiting, third });
});

for (const choice of ["Deny", "Stop"] as const) {
  test(`Python ${choice} leaves files unchanged and never starts the proposed code`, async ({ page, request }, info) => {
    const before = await prepare(request, "python-single");
    await openApp(page);
    await startPython(page);
    await page.getByRole("button", { name: choice, exact: true }).click();
    await expect.poll(async () => (await state(request)).active_runs).toBe(0);
    const after = await state(request);
    expect(after.files).toEqual(before.files);
    expect(after.awaiting_approval).toBe(false);
    expect(after.harness_status).toBe(choice === "Deny" ? "done" : "stopped");
    await evidence(request, page, info, before, after, `${choice} prevents all proposed Python side effects.`);
  });
}

test("altered pending Python arguments cannot execute under the displayed approval", async ({ page, request }, info) => {
  const before = await prepare(request, "python-single");
  await openApp(page);
  await startPython(page);
  await action(request, "tamper-python");
  const panel = page.getByRole("group", { name: "Agent tool approval" });
  expect(await panel.locator(".plan-node-approval-summary").textContent()).toBe(`python.exec\n--- source\n${before.source}`);
  await panel.getByRole("button", { name: "Approve once", exact: true }).click();
  await expect.poll(async () => (await state(request)).active_runs).toBe(0);
  const after = await state(request);
  expect(after.files).toEqual(before.files);
  expect(after.activity).toHaveLength(1);
  expect(after.activity[0].outcome).toBe("error");
  expect(after.activity[0].summary).toMatch(/changed|approval/i);
  await evidence(request, page, info, before, after, "Changed pending source is refused despite approval of the original display.");
});
