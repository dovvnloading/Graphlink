import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FalMediaNodeView, type FalMediaFlowNode } from "./FalMediaNodeView";

const catalog = {
  status: "ready", mode: "image", operation: "text-to-image", query: "",
  favorites: ["fal-ai/model-economy"],
  models: [
    { id: "fal-ai/model-premium", name: "Premium Model", category: "text-to-image",
      price: .3, unit: "image", currency: "USD", description: "Most detailed", thumbnailUrl: "", tags: [] },
    { id: "fal-ai/model-economy", name: "Economy Model", category: "text-to-image",
      price: .01, unit: "image", currency: "USD", description: "Fast results", thumbnailUrl: "", tags: [] },
    { id: "fal-ai/model-mp", name: "Megapixel Model", category: "text-to-image",
      price: .005, unit: "megapixel", currency: "USD", description: "", thumbnailUrl: "", tags: [] },
    { id: "fal-ai/model-unpriced", name: "Undisclosed Model", category: "text-to-image",
      price: null, unit: "", currency: "", description: "", thumbnailUrl: "", tags: [] },
  ], hasMore: false, nextCursor: "", pricingWarning: "", error: "",
};
const media = {
  mode: "image", operation: "text-to-image",
  modelId: "", modelName: "", prompt: "", argumentsJson: "{}",
  status: "idle", requestId: "", queuePosition: 0,
  error: "", history: [], quote: null,
};
const callbacks = () => ({
  onSaveDraft: vi.fn(), onSearch: vi.fn(), onInspect: vi.fn(), onFavorite: vi.fn(),
  onPrepare: vi.fn(), onGenerate: vi.fn(), onRefresh: vi.fn(),
  onCancel: vi.fn(), onDelete: vi.fn(),
});
function setup({
  mediaOverride = {}, catalogOverride = {}, schemaOverride = {},
}: {
  mediaOverride?: Record<string, unknown>;
  catalogOverride?: Record<string, unknown>;
  schemaOverride?: Record<string, unknown>;
} = {}) {
  const funcs = callbacks();
  const props = {
    id: "n7", selected: false,
    data: {
      mediaJson: JSON.stringify({ ...media, ...mediaOverride }),
      catalogJson: JSON.stringify({ ...catalog, ...catalogOverride }),
      schemaJson: JSON.stringify({status: "idle", modelId: "", fields: [], warning: "", ...schemaOverride}),
      ...funcs,
    },
  } as unknown as NodeProps<FalMediaFlowNode>;
  const user = userEvent.setup();
  const rendered = render(
    <ReactFlowProvider><FalMediaNodeView {...props}/></ReactFlowProvider>,
  );
  return { ...rendered, props, funcs, user };
}

afterEach(() => { vi.restoreAllMocks(); });

describe("Fal Media Studio node", () => {
  it("renders a premium native Studio with Image, Video and Audio tabs", () => {
    setup();
    expect(screen.getByRole("heading", { name: /Create without limits/ })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Media generation type" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Image/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Video/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Audio/ })).toBeEnabled();
    expect(screen.getByRole("region", { name: "Available Fal models" })).toBeInTheDocument();
  });

  it("orders economy image pricing before premium, with separate megapixel billing", () => {
    setup();
    const list = screen.getByRole("region", { name: "Available Fal models" });
    const rows = within(list).getAllByRole("button", { name: /^Select / });
    expect(rows.map((x) => x.getAttribute("aria-label"))).toEqual([
      "Select Economy Model", "Select Premium Model",
      "Select Megapixel Model", "Select Undisclosed Model",
    ]);
    expect(within(list).getByText("Billed in USD / image")).toBeInTheDocument();
    expect(within(list).getByText("Billed in USD / megapixel")).toBeInTheDocument();
    expect(within(list).getByText("Price unavailable")).toBeInTheDocument();
  });

  it("favorites are controlled, reversible and do not trigger a paid request", async () => {
    const { user, funcs } = setup();
    await user.click(screen.getByRole("button", { name: "Favorite Premium Model" }));
    expect(funcs.onFavorite).toHaveBeenCalledWith("fal-ai/model-premium", true);
    await user.click(screen.getByRole("button", { name: "Remove Economy Model from favorites" }));
    expect(funcs.onFavorite).toHaveBeenCalledWith("fal-ai/model-economy", false);
    await user.click(screen.getByRole("button", { name: /Favorites/ }));
    expect(screen.getByRole("button", { name: "Select Economy Model" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Select Premium Model" })).not.toBeInTheDocument();
    expect(funcs.onPrepare).not.toHaveBeenCalled();
    expect(funcs.onGenerate).not.toHaveBeenCalled();
  });

  it("switches modality without submitting or accidentally selecting a model", async () => {
    const { user, funcs } = setup();
    await user.click(screen.getByRole("button", { name: /Video/ }));
    expect(screen.getByRole("button", { name: "Animate image" })).toBeInTheDocument();
    await waitFor(() => expect(funcs.onSearch).toHaveBeenCalledWith("video", "text-to-video", ""));
    expect(funcs.onGenerate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Audio/ }));
    expect(screen.getByRole("button", { name: "Music & sound" })).toBeInTheDocument();
    await waitFor(() => expect(funcs.onSearch).toHaveBeenCalledWith("audio", "text-to-audio", ""));
  });

  it("selecting a model persists its ID and triggers model schema retrieval", async () => {
    const { user, funcs } = setup();
    await user.click(screen.getByRole("button", { name: "Select Economy Model" }));
    expect(funcs.onSaveDraft).toHaveBeenCalledWith(expect.objectContaining({
      modelId: "fal-ai/model-economy", modelName: "Economy Model", mode: "image",
    }));
    expect(funcs.onInspect).toHaveBeenCalledWith("fal-ai/model-economy");
    expect(screen.getByText("Selected model")).toBeInTheDocument();
    expect(funcs.onGenerate).not.toHaveBeenCalled();
  });

  it("cost review is a required separate step and generates only after a server-issued token", async () => {
    const { user, funcs } = setup({
      mediaOverride: {
        modelId: "fal-ai/model-economy", modelName: "Economy Model",
        prompt: "A vivid orange moon",
        quote: {status:"ready", id: "server-approved-nonce", modelId: "fal-ai/model-economy",
          expiresAt: Math.floor(Date.now() / 1000) + 200,
          price: .01, unit: "image", currency: "USD", estimate: .02},
      },
    });
    expect(screen.getByText("Estimated request")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Confirm & generate/ }));
    expect(funcs.onGenerate).toHaveBeenCalledOnce();
    expect(funcs.onGenerate).toHaveBeenCalledWith("server-approved-nonce");
    expect(funcs.onPrepare).not.toHaveBeenCalled();
  });

  it("never submits a quote that has expired", async () => {
    const { user, funcs } = setup({
      mediaOverride: {
        modelId: "fal-ai/model-economy", modelName: "Economy Model", prompt: "Meadow",
        quote: { status: "ready", id: "expired", modelId: "fal-ai/model-economy",
          expiresAt: 5, price: .01, unit: "image" },
      },
    });
    expect(screen.getByText(/Pricing review expired/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Review cost & generate/ }));
    expect(funcs.onPrepare).toHaveBeenCalledOnce();
    expect(funcs.onGenerate).not.toHaveBeenCalled();
  });

  it("invalid advanced JSON blocks cost review", async () => {
    const { user, funcs } = setup({
      mediaOverride: {modelId: "fal-ai/model-economy", modelName: "Economy Model", prompt: "Sunrise"},
    });
    const advanced = screen.getByText("Advanced model JSON");
    fireEvent.click(advanced);
    const input = screen.getByRole("textbox", { name: "Advanced Fal model JSON" });
    await user.clear(input);
    await user.type(input, "broken json");
    await user.click(screen.getByRole("button", { name: /Review cost & generate/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Fix the advanced JSON");
    expect(funcs.onPrepare).not.toHaveBeenCalled();
  });

  it("queues status refreshes but never automatically resubmits a paid generation", async () => {
    const { funcs } = setup({
      mediaOverride: { status: "queued", modelId: "fal-ai/model-economy", modelName: "Economy Model",
        requestId: "764cabcf-b745-4b3e-ae38-1200304cf45b" },
    });
    await waitFor(() => expect(funcs.onRefresh).toHaveBeenCalledOnce());
    expect(screen.getByText(/In the Fal queue/)).toBeInTheDocument();
    expect(funcs.onGenerate).not.toHaveBeenCalled();
  });

  it("renders image, video and audio with a same-origin image proxy and media playback", () => {
    const outputs = [
      {url: "https://v3.fal.media/files/image.png", kind: "image"},
      {url: "https://v3.fal.media/files/clip.mp4", kind: "video"},
      {url: "https://v3.fal.media/files/audio.mp3", kind: "audio"},
    ];
    const { container } = setup({
      mediaOverride: { status: "completed", history: [{
        modelId: "fal-ai/mixed", modelName: "Mixed Media", mode: "video",
        prompt: "Singing planet", requestId: "job-1", createdAt: "2026-11-13T00:00:00Z", outputs,
      }] },
    });
    const image = container.querySelector(".fal-studio-asset img");
    expect(image).toHaveAttribute("src", "/api/fal-media/preview/n7/0/0");
    expect(container.querySelector("video")).toHaveAttribute("src", outputs[1].url);
    expect(container.querySelector("audio")).toHaveAttribute("src", outputs[2].url);
    expect(screen.getAllByRole("link", { name: /Open generated/ })).toHaveLength(3);
  });

  it("safe delete requires confirmation when remote work is running", async () => {
    const { user, funcs } = setup({mediaOverride: {
      status: "running", requestId: "764cabcf-b745-4b3e-ae38-1200304cf45b",
    }});
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(screen.getByRole("button", { name: "Delete Fal Media Studio node" }));
    expect(funcs.onDelete).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "Delete Fal Media Studio node" }));
    expect(funcs.onDelete).toHaveBeenCalledOnce();
  });
});
