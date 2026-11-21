/* eslint-disable jsx-a11y/no-noninteractive-tabindex, jsx-a11y/no-noninteractive-element-interactions -- two labelled read-only Document View regions must accept keyboard focus and Alt+Arrow pane switching. */
import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { Dialog, useOverlays } from "../overlays/overlays";
import type { ChatVersionComparison, ChatVersionSnapshot, SceneStore } from "./sceneStore";
import type { ChatDiffChunk, ChatWordDiff } from "./chatVersionWordDiff";

export interface ChatVersionCompareTarget {
  nodeId: string;
  versionCount: number;
  selectedIndex: number;
  requestId: number;
}

function versionTime(createdAt: string): string {
  if (!createdAt) return "Time not recorded";
  const parsed = new Date(createdAt);
  return Number.isNaN(parsed.getTime()) ? "Time not recorded" : parsed.toLocaleString();
}

function DiffContents({ chunks }: { chunks: ChatDiffChunk[] }) {
  return (
    <>
      {chunks.map((chunk, index) => {
        if (chunk.kind === "same") return <span key={index}>{chunk.text}</span>;
        if (chunk.kind === "removed") {
          return (
            <span key={index}>
              <span className="visually-hidden">Deleted text: </span>
              <del className="chat-version-compare-deleted">{chunk.text}</del>
              <span className="visually-hidden"> End deletion. </span>
            </span>
          );
        }
        return (
          <span key={index}>
            <span className="visually-hidden">Inserted text: </span>
            <ins className="chat-version-compare-inserted">{chunk.text}</ins>
            <span className="visually-hidden"> End insertion. </span>
          </span>
        );
      })}
    </>
  );
}

function VersionPane({
  side, index, version, chunks, unavailable, paneRef, onKeyDown,
}: {
  side: "Left" | "Right";
  index: number;
  version: ChatVersionSnapshot | null;
  chunks: ChatDiffChunk[] | null;
  unavailable: boolean;
  paneRef: RefObject<HTMLDivElement | null>;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
}) {
  return (
    <section className="chat-version-compare-pane">
      <div
        className="document-view-panel-scroll chat-node-content chat-version-compare-text"
        role="region"
        aria-label={side + " comparison pane, version " + (index + 1)}
        tabIndex={0}
        ref={paneRef}
        onKeyDown={onKeyDown}
      >
        <div className="chat-version-compare-pane-heading">
          <h3>Version {index + 1}</h3>
          <p className="document-view-panel-subtitle">
            {version
              ? (version.provider || "Provider not recorded") + " / " +
                (version.model || "Model not recorded") + " · " + versionTime(version.createdAt)
              : "Provider, model and time loading…"}
          </p>
        </div>
        <div className="chat-version-compare-text-body">
          {version ? (chunks ? <DiffContents chunks={chunks} /> : version.content)
            : (unavailable ? "Comparison unavailable." : "Loading version…")}
        </div>
      </div>
    </section>
  );
}

/** A read-only, two-pane Document View reader inside the shared modal Dialog.
 * The worker owns every diff calculation; this component only renders its
 * result. Version selectors never call SceneStore.setChatVersion.
 */
export function ChatVersionCompareDialog({
  store, target,
}: {
  store: SceneStore;
  target: ChatVersionCompareTarget;
}) {
  const overlays = useOverlays();
  const isOpen = overlays.isOpen("chat-version-compare");
  const [leftIndex, setLeftIndex] = useState(Math.max(0, target.selectedIndex - 1));
  const [rightIndex, setRightIndex] = useState(
    target.selectedIndex === 0 ? 1 : target.selectedIndex,
  );
  const [loaded, setLoaded] = useState<{ key: string; pair: ChatVersionComparison } | null>(null);
  const [computed, setComputed] = useState<{ key: string; diff: ChatWordDiff } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const leftId = useId();
  const rightId = useId();
  const requestKey = target.nodeId + ":" + leftIndex + ":" + rightIndex;

  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    let worker: Worker | null = null;

    void store.compareChatVersions(target.nodeId, leftIndex, rightIndex).then((pair) => {
      if (!active) return;
      setLoaded({ key: requestKey, pair });
      try {
        worker = new Worker(new URL("./chatVersionWordDiff.worker.ts", import.meta.url), { type: "module" });
        worker.onmessage = (event: MessageEvent<ChatWordDiff>) => {
          if (active) setComputed({ key: requestKey, diff: event.data });
        };
        worker.onerror = () => {
          if (active) setFailure({ key: requestKey, message: "Unable to calculate word changes." });
        };
        worker.postMessage({ left: pair.left.content, right: pair.right.content });
      } catch {
        if (active) setFailure({ key: requestKey, message: "Word comparison is unavailable in this browser." });
      }
    }).catch(() => {
      if (active) setFailure({ key: requestKey, message: "Unable to load these chat versions." });
    });

    return () => {
      active = false;
      worker?.terminate();
    };
  }, [isOpen, store, target.nodeId, leftIndex, rightIndex, requestKey]);

  const pair = loaded?.key === requestKey ? loaded.pair : null;
  const diff = computed?.key === requestKey ? computed.diff : null;
  const error = failure?.key === requestKey ? failure.message : "";
  const count = pair?.versionCount ?? target.versionCount;

  const switchPane = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!event.altKey || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    if (event.key === "ArrowLeft") leftRef.current?.focus();
    else rightRef.current?.focus();
  };

  return (
    <Dialog name="chat-version-compare" title="Document View · Compare reply versions" className="chat-version-compare-dialog">
      <p className="chat-version-compare-help">
        Choose two saved replies. This comparison is read-only and never changes the active version.
        Use Alt+Left/Right Arrow to switch reading panes, or Tab to the pane focus buttons. Escape closes.
      </p>
      <div className="chat-version-compare-selectors">
        <label htmlFor={leftId}>
          Left version
          <select id={leftId} value={leftIndex} onChange={(e) => setLeftIndex(Number(e.target.value))}>
            {Array.from({ length: count }, (_, index) => (
              <option key={index} value={index} disabled={index === rightIndex}>Version {index + 1}</option>
            ))}
          </select>
        </label>
        <label htmlFor={rightId}>
          Right version
          <select id={rightId} value={rightIndex} onChange={(e) => setRightIndex(Number(e.target.value))}>
            {Array.from({ length: count }, (_, index) => (
              <option key={index} value={index} disabled={index === leftIndex}>Version {index + 1}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="chat-version-compare-legend" role="group" aria-label="Text difference legend">
        <span><span aria-hidden="true">− </span>Deleted: <del>struck through</del></span>
        <span><span aria-hidden="true">+ </span>Inserted: <ins>underlined</ins></span>
        <span>Raw reply text, including Markdown syntax</span>
      </div>
      <div className="chat-version-compare-focus-actions">
        <button type="button" onClick={() => leftRef.current?.focus()}>Focus left pane</button>
        <button type="button" onClick={() => rightRef.current?.focus()}>Focus right pane</button>
      </div>
      <p role="status" className="chat-version-compare-status">
        {error || (!pair ? "Loading versions…" : !diff ? "Calculating word changes…" : "Word comparison ready.")}
      </p>
      <div className="chat-version-compare-panes">
        <VersionPane
          side="Left" index={leftIndex} version={pair?.left ?? null} chunks={diff?.left ?? null}
          unavailable={!!error} paneRef={leftRef} onKeyDown={switchPane}
        />
        <VersionPane
          side="Right" index={rightIndex} version={pair?.right ?? null} chunks={diff?.right ?? null}
          unavailable={!!error} paneRef={rightRef} onKeyDown={switchPane}
        />
      </div>
    </Dialog>
  );
}
