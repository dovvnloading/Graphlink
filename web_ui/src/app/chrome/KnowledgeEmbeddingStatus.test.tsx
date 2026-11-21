import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeRequestOnlyTransport } from "../../lib/ws/transport.testUtils";
import { KnowledgeEmbeddingStatus } from "./KnowledgeEmbeddingStatus";

const SETTINGS = {
  knowledgeEmbeddingSource: "ollama",
  knowledgeEmbeddingModel: "nomic-embed-text",
};
const JOB = {
  jobId: "embed-5",
  workspaceId: 5,
  source: "ollama",
  model: "nomic-embed-text",
  status: "running",
  chunksTotal: 40,
  chunksEmbedded: 12,
  chunksRemaining: 28,
  cancelRequested: false,
  error: "",
};

function setup(readJobs?: () => Promise<unknown>) {
  const user = userEvent.setup();
  const fake = makeRequestOnlyTransport();
  const listeners = new Map<string, (payload: Record<string, unknown>) => void>();
  fake.transport.subscribe = (topic, listener) => {
    listeners.set(topic, listener);
    return () => { if (listeners.get(topic) === listener) listeners.delete(topic); };
  };
  fake.request.mockImplementation((_topic, intent) =>
    intent === "getEmbeddingJobs"
      ? (readJobs?.() ?? Promise.resolve({ workspaceId: 5, jobs: [] }))
      : Promise.resolve({}),
  );
  const view = render(<KnowledgeEmbeddingStatus transport={fake.transport} open />);
  const emit = (topic: string, payload: Record<string, unknown>) => {
    act(() => { listeners.get(topic)?.(payload); });
  };
  return { ...fake, user, emit, ...view };
}

describe("KnowledgeEmbeddingStatus", () => {
  it("shows the configured source/model and starts from Enter without inventing counts", async () => {
    const { user, emit, intents } = setup();
    emit("app-settings", SETTINGS);
    const start = screen.getByRole("button", { name: "Start embeddings" });
    await waitFor(() => expect(start).toBeEnabled());

    expect(screen.getByText(/Source: Ollama \(local\).*Model: nomic-embed-text/)).toBeInTheDocument();
    expect(screen.getByText("No embedding job for this workspace and model yet.")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    start.focus();
    await user.keyboard("{Enter}");
    expect(intents).toContainEqual(["knowledge", "resumeEmbedding", []]);
  });

  it("shows only this workspace's job counts and live progress, and cancels with Space", async () => {
    const { user, emit, intents } = setup();
    emit("app-settings", SETTINGS);
    emit("knowledge-embedding", {
      workspaceId: 5,
      jobs: [
        { ...JOB, jobId: "other-workspace", workspaceId: 9, chunksEmbedded: 500 },
        JOB,
      ],
    });

    expect(screen.getByText("12 embedded · 28 pending chunks (this run)")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", { name: "Knowledge embedding progress" });
    expect(progress).toHaveAttribute("max", "40");
    expect(progress).toHaveAttribute("value", "12");
    const cancel = screen.getByRole("button", { name: "Cancel embeddings" });
    cancel.focus();
    await user.keyboard(" ");
    expect(intents).toContainEqual(["knowledge", "cancelEmbedding", ["embed-5"]]);

    emit("knowledge-embedding", {
      workspaceId: 5,
      jobs: [{ ...JOB, cancelRequested: true, chunksEmbedded: 32, chunksRemaining: 8 }],
    });
    expect(screen.getByRole("button", { name: "Cancelling embeddings…" })).toBeDisabled();
    expect(progress).toHaveAttribute("value", "32");
  });

  it("resumes a failed job by keyboard, retaining the last error and its pending count", async () => {
    const { user, emit, intents } = setup();
    emit("app-settings", SETTINGS);
    const failed = {
      ...JOB, status: "failed", chunksEmbedded: 32, chunksRemaining: 8,
      error: "Knowledge embedding failed. Check the selected provider and model.",
    };
    emit("knowledge-embedding", { workspaceId: 5, jobs: [failed] });

    expect(screen.getByText("32 embedded · 8 pending chunks (this run)")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Last error: Knowledge embedding failed.");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    const resume = screen.getByRole("button", { name: "Resume embeddings" });
    resume.focus();
    await user.keyboard("{Enter}");
    expect(intents).toContainEqual(["knowledge", "resumeEmbedding", []]);

    emit("knowledge-embedding", {
      workspaceId: 5,
      jobs: [failed, { ...JOB, jobId: "retry", chunksTotal: 8, chunksEmbedded: 4, chunksRemaining: 4 }],
    });
    expect(screen.getByRole("progressbar", { name: "Knowledge embedding progress" }))
      .toHaveAttribute("value", "4");
    expect(screen.getByRole("alert")).toHaveTextContent("Last error:");
  });

  it("disables start while embeddings are off, regardless of historical jobs", async () => {
    const { emit } = setup();
    emit("app-settings", { knowledgeEmbeddingSource: "off", knowledgeEmbeddingModel: "" });
    emit("knowledge-embedding", { workspaceId: null, jobs: [JOB] });

    expect(screen.getByText(/Source: Off.*Model: None/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start embeddings" })).toBeDisabled();
    expect(screen.getByText(/Enable a source in Settings/)).toBeInTheDocument();
    expect(screen.queryByText(/12 embedded/)).not.toBeInTheDocument();
  });

  it("does not overwrite event progress with an older getEmbeddingJobs response", async () => {
    let resolveRead: (value: unknown) => void = () => {};
    const pending = new Promise<unknown>((resolve) => { resolveRead = resolve; });
    const { emit, request } = setup(() => pending);
    emit("app-settings", SETTINGS);
    await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge", "getEmbeddingJobs", []));
    emit("knowledge-embedding", {
      workspaceId: 5,
      jobs: [{ ...JOB, chunksEmbedded: 32, chunksRemaining: 8 }],
    });

    await act(async () => {
      resolveRead({ workspaceId: 5, jobs: [JOB] });
      await pending;
    });
    expect(screen.getByText("32 embedded · 8 pending chunks (this run)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Knowledge embedding progress" }))
      .toHaveAttribute("value", "32");
  });

  it("reports failed controls and refreshes the workspace context when reopened", async () => {
    const { user, emit, request, intents, rerender, transport } = setup();
    emit("app-settings", SETTINGS);
    await waitFor(() => expect(screen.getByRole("button", { name: "Start embeddings" })).toBeEnabled());
    request.mockRejectedValueOnce(new Error("Embeddings unavailable."));
    await user.click(screen.getByRole("button", { name: "Start embeddings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Embeddings unavailable.");

    rerender(<KnowledgeEmbeddingStatus transport={transport} open={false} />);
    request.mockImplementation(async (_topic, intent) =>
      intent === "getEmbeddingJobs"
        ? { workspaceId: 8, jobs: [{ ...JOB, jobId: "embed-8", workspaceId: 8 }] }
        : {},
    );
    rerender(<KnowledgeEmbeddingStatus transport={transport} open />);
    await waitFor(() => expect(intents.filter((row) => row[1] === "getEmbeddingJobs")).toHaveLength(2));
    expect(await screen.findByText("12 embedded · 28 pending chunks (this run)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel embeddings" })).toBeEnabled();
  });
});
