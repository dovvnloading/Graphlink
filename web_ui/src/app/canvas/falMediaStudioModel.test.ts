import { describe, expect, it } from "vitest";
import {
  MEDIA_OPERATIONS, comparableGroup, editJsonValue, formatMediaPrice,
  isMediaBusy, parseMediaCatalog, parseMediaSchema, parseMediaState,
  sortMediaModels, type MediaModel,
} from "./falMediaStudioModel";

const model = (id: string, price: number | null, unit = "image", currency = "USD"): MediaModel => ({
  id, name: id, price, unit, currency, category: "text-to-image",
  description: "", thumbnailUrl: "", tags: [],
});

describe("Fal Media Studio view models", () => {
  it("has independent valid input operations for all three modalities", () => {
    expect(MEDIA_OPERATIONS.image.map((x) => x.id)).toEqual(["text-to-image", "image-to-image"]);
    expect(MEDIA_OPERATIONS.video.map((x) => x.id)).toContain("image-to-video");
    expect(MEDIA_OPERATIONS.audio.map((x) => x.id)).toContain("text-to-speech");
  });

  it("sorts economy to premium inside comparable billing cohorts and always puts unknown at end", () => {
    const input = [
      model("premium", .24),
      model("mp-economy", .002, "megapixel"),
      model("economy", .008),
      model("unpriced", null),
    ];
    expect(sortMediaModels(input, "economy").map((m) => m.id))
      .toEqual(["economy", "premium", "mp-economy", "unpriced"]);
    expect(sortMediaModels(input, "premium").map((m) => m.id))
      .toEqual(["premium", "economy", "mp-economy", "unpriced"]);
    expect(input[0].id).toBe("premium"); // nonmutating
    expect(comparableGroup(model("x", .03, "second"))).toBe("USD / second");
    expect(comparableGroup(model("x", null))).toBe("Unpriced");
  });

  it("never represents an unpriced model as free", () => {
    expect(formatMediaPrice(null)).toBe("Price unavailable");
    expect(formatMediaPrice(undefined)).toBe("Price unavailable");
    expect(formatMediaPrice(Number.NaN)).toBe("Price unavailable");
    expect(formatMediaPrice(0)).toBe("$0");
    expect(formatMediaPrice(.005)).toContain("0.005");
  });

  it("parses a studio snapshot without trusting malformed input shapes", () => {
    expect(parseMediaState("{bad").status).toBe("idle");
    const changed = parseMediaState(JSON.stringify({
      mode: "video", operation: "image-to-video", prompt: "Orbiting city",
      history: [{ requestId: "abc", outputs: [] }], status: "queued",
    }));
    expect(changed.mode).toBe("video");
    expect(changed.operation).toBe("image-to-video");
    expect(changed.history).toHaveLength(1);
    expect(changed.status).toBe("queued");
    expect(parseMediaState('{"mode":"hacked","operation":"other"}').mode).toBe("image");
  });

  it("parses category results and favorites independently of selected model state", () => {
    const parsed = parseMediaCatalog(JSON.stringify({
      status: "ready", mode: "image", operation: "text-to-image", query: "flux",
      models: [{ id: "fal-ai/flux/schnell", name: "Schnell" }],
      favorites: ["fal-ai/flux/schnell"],
    }));
    expect(parsed.models).toHaveLength(1);
    expect(parsed.favorites).toEqual(["fal-ai/flux/schnell"]);
    expect(parseMediaCatalog("[]").status).toBe("idle");
    expect(parseMediaSchema(JSON.stringify({
      status: "ready", modelId: "fal-ai/model", fields: [{name:"seed",type:"integer"}],
    })).fields).toHaveLength(1);
  });

  it("schema form changes update advanced JSON without discarding other parameters", () => {
    expect(editJsonValue('{"seed":7,"num_images":2}', "seed", 42))
      .toEqual(JSON.stringify({ seed: 42, num_images: 2 }, null, 2));
    expect(editJsonValue("{broken", "seed", 42)).toBeNull();
    expect(editJsonValue("[]", "seed", 42)).toBeNull();
  });

  it("only in-flight remote job states are busy", () => {
    for (const state of ["submitting", "queued", "running", "cancelling", "stalled"]) expect(isMediaBusy(state)).toBe(true);
    for (const state of ["idle", "failed", "completed", "cancelled"]) expect(isMediaBusy(state)).toBe(false);
  });
});
