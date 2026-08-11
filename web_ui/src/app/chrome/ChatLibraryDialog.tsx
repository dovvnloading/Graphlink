import { useEffect, useMemo, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppChatLibraryRow, AppChatLibraryState, AppWorkspaceRow } from "../../lib/bridge-core/generated/app-chat-library-state";
import type { AppComposerState } from "../../lib/bridge-core/generated/app-composer-state";
import { Dialog, useOverlays } from "../overlays/overlays";
import { CustomSelect } from "./CustomSelect";
import { initialComposerState } from "./composerStore";

/**
 * The chat library dialog (Qt-removal plan R2.5e + R6.4 + R6.5, R8a full
 * redesign). List/search/rename/delete/load/new are all real (backend/
 * chat_library.py reads/writes the same ~/.graphlink/chats.db the legacy
 * app uses).
 *
 * R8a replaced the old select-a-row-then-click-a-shared-toolbar-button
 * model (which is what made this "the worst UI element" - a permanently
 * boxed flat list, no content preview, four buttons that mutated based on
 * "whichever row is currently selected") with a real chat-history list:
 * date-grouped rows (Today/Yesterday/Previous 7 Days/Previous 30 Days/
 * Older), a one-line preview of each chat's last message, and per-row
 * rename/delete that act on THAT row, never a shared selection. Clicking a
 * row's body IS Load Chat now - there is no separate select step. Reuses
 * the composer island's exact "flat/borderless until interaction, one
 * filled accent element" language rather than inventing a new visual
 * system - see styles.css's own comment on the .library-* rules.
 *
 * ADR-020 stage 20.2 adds the real Chat Library UI on top of stage 20.1's
 * workspaces/graphs schema: a workspace switcher (tabs mirroring
 * ViewPopover.tsx's single-select preset-button idiom), tag filter chips
 * (mirroring ViewPopover's multi-select toggle-chip filter section),
 * favorite/archive icon buttons (added to the existing always-visible
 * .library-row-actions row), and inline tag editing (the exact rename
 * input-swap interaction pattern, applied to a different field). All
 * filtering (workspace/tags/archived/search) is client-side, layered on
 * top of the existing search-filter pipeline - the backend always sends
 * every non-deleted graph across every workspace in one payload (see
 * backend/chat_library.py's get_all_chats).
 *
 * ADR-020 stage 20.3 adds a small settings affordance per REAL workspace
 * tab (never "All", which isn't a real workspace row) - a gear icon that
 * reveals an inline expansion below the tabs strip showing that
 * workspace's current default model and a control to change it
 * (workspace.defaultModelProvider/defaultModelId, "" on both meaning "no
 * default set" - see contracts/graphlink_app_chat_library_payload.py's own
 * docstring on AppWorkspaceRowPayload). The reveal itself follows this
 * file's own established inline-expansion idiom (the same "click an icon,
 * a panel opens in place, no new modal" shape as inline rename/tag-edit
 * above), but the WIDGET inside it is CustomSelect.tsx (Settings' own
 * "pick one value from a small catalog" control), not Composer.tsx's
 * Popover-based ModelPicker - deliberately: CustomSelect's own module
 * docstring documents exactly why it stays outside the useOverlays()
 * registry (a Popover opening would flip the registry's openSurface away
 * from "library", which THIS dialog's own <Dialog name="library"> would
 * read as "I'm not open anymore" and unmount - the identical class of bug
 * that docstring already names for Settings). The model catalog itself is
 * NOT a second data-fetching path - this dialog adds a second, independent
 * subscription to the existing "app-composer" topic (same dual-subscription
 * shape SettingsDialog.tsx already uses for app-settings + app-plugins) and
 * reads route.modelOptions/route.provider, the exact same fields Composer's
 * own model picker renders from. Since this app has exactly one ACTIVE
 * provider at a time (see SettingsDialog.tsx's own ProviderModeSwitch), a
 * workspace default is "pick a model from the currently active provider's
 * catalog" - the chosen option's id becomes defaultModelId and the
 * currently active route.provider tags along as defaultModelProvider,
 * mirroring how Composer's own selectModel(modelId) never takes a separate
 * provider argument either.
 */

const initialState: AppChatLibraryState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  workspaces: [],
  notice: null,
};

type BucketKey = "Today" | "Yesterday" | "Previous 7 Days" | "Previous 30 Days" | "Older";
const BUCKET_ORDER: BucketKey[] = ["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"];
const DAY_MS = 24 * 60 * 60 * 1000;

function startOfLocalDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function effectiveIso(row: AppChatLibraryRow): string | null {
  return row.updatedAtIso ?? row.createdAtIso ?? null;
}

// Rows with no usable timestamp (an old pre-migration row, or a corrupt
// one) always land in Older, sorted after every dated Older row - never
// guessed into Today/Yesterday just because they're undated.
function bucketFor(row: AppChatLibraryRow, todayStart: number): BucketKey {
  const iso = effectiveIso(row);
  if (!iso) return "Older";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Older";
  const diffDays = Math.round((todayStart - startOfLocalDay(parsed)) / DAY_MS);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays <= 7) return "Previous 7 Days";
  if (diffDays <= 30) return "Previous 30 Days";
  return "Older";
}

function sortWithinBucket(rows: AppChatLibraryRow[]): AppChatLibraryRow[] {
  return [...rows].sort((a, b) => {
    const aIso = effectiveIso(a);
    const bIso = effectiveIso(b);
    if (aIso && bIso) return aIso < bIso ? 1 : aIso > bIso ? -1 : 0;
    if (aIso) return -1;
    if (bIso) return 1;
    return b.id - a.id;
  });
}

function groupRows(rows: AppChatLibraryRow[]): Array<{ key: BucketKey; rows: AppChatLibraryRow[] }> {
  const todayStart = startOfLocalDay(new Date());
  const buckets = new Map<BucketKey, AppChatLibraryRow[]>();
  for (const row of rows) {
    const key = bucketFor(row, todayStart);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  return BUCKET_ORDER.filter((key) => buckets.has(key)).map((key) => ({
    key,
    rows: sortWithinBucket(buckets.get(key) as AppChatLibraryRow[]),
  }));
}

function Icon({
  name,
}: {
  name: "search" | "pencil" | "trash" | "check" | "x" | "chat" | "star" | "star-filled" | "archive" | "tag" | "gear";
}) {
  switch (name) {
    case "search":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m20 20-4.8-4.8" />
        </svg>
      );
    case "pencil":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M14.5 4.5 19.5 9.5 8 21H3v-5Z" />
        </svg>
      );
    case "trash":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 7h16" />
          <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
          <path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
        </svg>
      );
    case "check":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="m5 13 5 5 9-11" />
        </svg>
      );
    case "x":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      );
    case "chat":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 5h16v11H8l-4 4Z" />
        </svg>
      );
    // ADR-020 stage 20.2: same 10-point star polygon for both the outline
    // and filled states (an un-favorited row shows the outline, a
    // favorited one the filled variant) - only the `fill` differs, so the
    // two always read as the exact same glyph, never a visually distinct
    // "different icon" swap.
    case "star":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M12 2.5 14.18 9.01 21.04 9.06 15.52 13.14 17.58 19.69 12 15.7 6.42 19.69 8.48 13.14 2.96 9.06 9.82 9.01Z" />
        </svg>
      );
    case "star-filled":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path
            d="M12 2.5 14.18 9.01 21.04 9.06 15.52 13.14 17.58 19.69 12 15.7 6.42 19.69 8.48 13.14 2.96 9.06 9.82 9.01Z"
            fill="currentColor"
          />
        </svg>
      );
    case "archive":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M3 4.5h18v4H3Z" />
          <path d="M4.5 8.5v10a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-10" />
          <path d="M9.5 12.5h5" />
        </svg>
      );
    case "tag":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M20 13.5 12.5 21a1.5 1.5 0 0 1-2.12 0L3 13.6V5a1.5 1.5 0 0 1 1.5-1.5h8.6a1.5 1.5 0 0 1 1.06.44L20 10.4a1.5 1.5 0 0 1 0 2.12Z" />
          <circle cx="8" cy="8.5" r="1.4" />
        </svg>
      );
    // ADR-020 stage 20.3: a plain 8-spoke gear/settings glyph, same
    // stroke-only construction (circle + straight paths) as every other
    // icon in this component - not a filled Material-style cog.
    case "gear":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="12" cy="12" r="3.2" />
          <path d="M12 3v3.2M12 17.8V21M21 12h-3.2M6.2 12H3M18.36 5.64l-2.26 2.26M7.9 16.1l-2.26 2.26M18.36 18.36l-2.26-2.26M7.9 7.9 5.64 5.64" />
        </svg>
      );
  }
}

export function ChatLibraryDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [query, setQuery] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  // ADR-020 stage 20.2: workspace switcher / tag filter / archived-toggle /
  // inline tag-edit local UI state - none of this is scene state or
  // persisted anywhere; it lives exactly like `query` above, dialog-local
  // and reset whenever the dialog closes and reopens (a fresh mount).
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [showArchived, setShowArchived] = useState(false);
  const [isCreatingWorkspace, setIsCreatingWorkspace] = useState(false);
  const [newWorkspaceDraft, setNewWorkspaceDraft] = useState("");
  const [editingTagsId, setEditingTagsId] = useState<number | null>(null);
  const [tagsDraft, setTagsDraft] = useState("");
  // ADR-020 stage 20.3: which real workspace's gear-icon settings panel is
  // currently expanded - dialog-local, same "not scene state, reset on
  // remount" posture as every other piece of local UI state above.
  const [workspaceSettingsId, setWorkspaceSettingsId] = useState<number | null>(null);
  // ADR-020 stage 20.3: a second, independent subscription to "app-composer"
  // (the same topic Composer.tsx/composerStore.ts already read) - this
  // dialog reads route.modelOptions/route.provider from it for the
  // workspace default-model picker, rather than opening a second
  // data-fetching path. Mirrors SettingsDialog.tsx's own dual "app-settings"
  // + "app-plugins" subscription shape.
  const [composerState, setComposerState] = useState<AppComposerState>(initialComposerState);
  const renameRef = useRef<HTMLInputElement>(null);
  const deleteCancelRef = useRef<HTMLButtonElement>(null);
  const newWorkspaceRef = useRef<HTMLInputElement>(null);
  const tagsEditRef = useRef<HTMLInputElement>(null);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    return transport.subscribe("app-chat-library", (payload) => {
      const validated = TOPIC_VALIDATORS["app-chat-library"](payload);
      if (validated.ok) setState(validated.value as AppChatLibraryState);
      else console.error("[app-chat-library] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  // ADR-020 stage 20.3: see this file's own module docstring for why this
  // second subscription reuses "app-composer" rather than opening a new
  // data-fetching path.
  useEffect(() => {
    return transport.subscribe("app-composer", (payload) => {
      const validated = TOPIC_VALIDATORS["app-composer"](payload);
      if (validated.ok) setComposerState(validated.value as AppComposerState);
      else console.error("[app-composer] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  useEffect(() => {
    if (renamingId !== null) {
      renameRef.current?.focus();
      renameRef.current?.select();
    }
  }, [renamingId]);

  useEffect(() => {
    if (confirmingDeleteId !== null) deleteCancelRef.current?.focus();
  }, [confirmingDeleteId]);

  useEffect(() => {
    if (isCreatingWorkspace) newWorkspaceRef.current?.focus();
  }, [isCreatingWorkspace]);

  useEffect(() => {
    if (editingTagsId !== null) {
      tagsEditRef.current?.focus();
      tagsEditRef.current?.select();
    }
  }, [editingTagsId]);

  // A republish after delete/rename elsewhere can drop the row a pending
  // confirm/rename/tag-edit targeted - reset-during-render on a revision
  // change, same pattern CommandPalette uses for its own wasOpen tracking.
  // ADR-020 stage 20.2: also resets selectedWorkspaceId if the workspace it
  // pointed at just got archived (archiveWorkspace publishes this same
  // topic) - staying selected on a now-hidden tab would otherwise strand
  // the view on a permanently empty list with no visible way back.
  const visibleWorkspaceIds = new Set(state.workspaces.filter((w) => !w.archived).map((w) => w.id));
  const [seenRevision, setSeenRevision] = useState(state.revision);
  if (seenRevision !== state.revision) {
    setSeenRevision(state.revision);
    setConfirmingDeleteId(null);
    setRenamingId(null);
    setEditingTagsId(null);
    if (selectedWorkspaceId !== null && !visibleWorkspaceIds.has(selectedWorkspaceId)) {
      setSelectedWorkspaceId(null);
    }
    // ADR-020 stage 20.3: same "don't strand the panel on a now-hidden tab"
    // guard selectedWorkspaceId already gets above - an archived workspace
    // loses its own settings panel too, not just its selected-tab status.
    if (workspaceSettingsId !== null && !visibleWorkspaceIds.has(workspaceSettingsId)) {
      setWorkspaceSettingsId(null);
    }
  }

  // ADR-020 stage 20.2: workspace -> archived -> tags -> search, each stage
  // narrowing the previous one - "empty selection means no filter" at every
  // stage, matching sceneStore's own filterKinds/filterStatuses convention
  // (see ViewPopover.tsx's FILTER section comment).
  const workspaceRows = useMemo(() => {
    if (selectedWorkspaceId === null) return state.rows;
    return state.rows.filter((row) => row.workspaceId === selectedWorkspaceId);
  }, [state.rows, selectedWorkspaceId]);

  // Tag chips are scoped to the CURRENTLY VISIBLE workspace's graphs (not
  // narrowed further by the archived toggle or the tag selection itself -
  // narrowing by the very filter being rendered would make chips disappear
  // as you select them).
  const availableTags = useMemo(() => {
    const names = new Set<string>();
    for (const row of workspaceRows) for (const tag of row.tags) names.add(tag);
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [workspaceRows]);

  const archivedRows = useMemo(() => {
    if (showArchived) return workspaceRows;
    return workspaceRows.filter((row) => !row.archived);
  }, [workspaceRows, showArchived]);

  // AND semantics: a graph must carry EVERY selected tag, not just one of
  // them. Chosen over OR because these chips read as successive narrowing
  // facets of a single "tags" filter (click more chips -> fewer, more
  // specific results) rather than a "match any of these labels" broadening
  // filter - the opposite of what most users expect when they add a second
  // active chip to an already-filtered list.
  const tagRows = useMemo(() => {
    if (selectedTags.size === 0) return archivedRows;
    const required = [...selectedTags];
    return archivedRows.filter((row) => required.every((tag) => row.tags.includes(tag)));
  }, [archivedRows, selectedTags]);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return tagRows;
    return tagRows.filter((row) => `${row.title} ${row.preview}`.toLowerCase().includes(term));
  }, [query, tagRows]);

  const groups = useMemo(() => groupRows(filtered), [filtered]);

  function loadChat(id: number) {
    transport.fireIntent("app-chat-library", "loadChat", [id]);
    overlays.close();
  }

  function newChat() {
    // ADR-020 stage 20.2: newChat's new trailing workspaceId param is
    // optional/backward-compatible - omitted here whenever "All" is
    // selected (selectedWorkspaceId === null), which resolves server-side
    // to the Default workspace exactly like every pre-20.2 caller (e.g.
    // commands.ts's palette "new chat" command, unaffected by this change).
    const args = selectedWorkspaceId === null ? [] : [selectedWorkspaceId];
    transport.fireIntent("app-chat-library", "newChat", args);
    overlays.close();
  }

  function selectWorkspace(workspaceId: number | null) {
    setSelectedWorkspaceId(workspaceId);
    // Tags are scoped to the workspace they were chosen from - carrying a
    // stale selection into a workspace that doesn't have that tag at all
    // would silently filter to zero rows with no visible chip explaining
    // why.
    setSelectedTags(new Set());
  }

  function toggleTag(tag: string) {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  // ADR-020 stage 20.3: toggles the SAME workspace's panel closed (matching
  // startRename/startTagEdit's own "click again to close" absence - those
  // two don't have a re-click-to-close affordance because Save/Cancel
  // already close them, but a settings panel has no commit step, so the
  // gear icon itself is the only open/close control it has).
  function toggleWorkspaceSettings(workspaceId: number) {
    setWorkspaceSettingsId((prev) => (prev === workspaceId ? null : workspaceId));
  }

  // ADR-020 stage 20.3: modelId === "" clears the workspace default (both
  // wire fields go back to "", matching AppWorkspaceRowPayload's own "empty
  // string on BOTH means unset" contract) - otherwise the CURRENTLY ACTIVE
  // provider tags along with the chosen model id, the same implicit-
  // provider posture Composer's own selectModel(modelId) already has (see
  // this file's own module docstring).
  function setWorkspaceDefaultModel(workspaceId: number, modelId: string) {
    const provider = modelId === "" ? "" : composerState.route.provider;
    transport.fireIntent("app-chat-library", "setWorkspaceDefaultModel", [workspaceId, provider, modelId], undefined, true);
  }

  function startCreateWorkspace() {
    setIsCreatingWorkspace(true);
    setNewWorkspaceDraft("");
  }

  function commitCreateWorkspace() {
    const name = newWorkspaceDraft.trim();
    if (!name) return;
    transport.fireIntent("app-chat-library", "createWorkspace", [name]);
    setIsCreatingWorkspace(false);
  }

  function cancelCreateWorkspace() {
    setIsCreatingWorkspace(false);
  }

  function onNewWorkspaceKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitCreateWorkspace();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelCreateWorkspace();
    }
  }

  function toggleFavorite(row: AppChatLibraryRow) {
    transport.fireIntent("app-chat-library", "setGraphFavorite", [row.id, !row.favorite]);
  }

  function toggleArchived(row: AppChatLibraryRow) {
    transport.fireIntent("app-chat-library", "setGraphArchived", [row.id, !row.archived]);
  }

  function startRename(row: AppChatLibraryRow, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setRenamingId(row.id);
    setRenameDraft(row.title);
    setConfirmingDeleteId(null);
    setEditingTagsId(null);
  }

  function commitRename() {
    const title = renameDraft.trim();
    if (renamingId === null || !title) return;
    transport.fireIntent("app-chat-library", "renameChat", [renamingId, title], undefined, true);
    setRenamingId(null);
    lastTriggerRef.current?.focus();
  }

  function cancelRename() {
    setRenamingId(null);
    lastTriggerRef.current?.focus();
  }

  function onRenameKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  }

  // ADR-020 stage 20.2: the exact startRename/commitRename/cancelRename/
  // onRenameKeyDown shape immediately above, applied to `tags` instead of
  // `title` - same input-swaps-the-row-primary-slot idiom, same Enter-
  // commits/Escape-cancels contract. Draft is a plain comma-separated
  // string (pre-filled from row.tags.join(", ")); commit splits on comma,
  // trims each piece, and drops empties before firing setGraphTags - the
  // server independently trims/dedupes/case-collapses too (see
  // backend/chat_library.py's _normalize_tags), so this is a UX nicety for
  // immediate feedback, not the only place that normalization happens.
  function startTagEdit(row: AppChatLibraryRow, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setEditingTagsId(row.id);
    setTagsDraft(row.tags.join(", "));
    setConfirmingDeleteId(null);
    setRenamingId(null);
  }

  function commitTagEdit() {
    if (editingTagsId === null) return;
    const tags = tagsDraft
      .split(",")
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0);
    transport.fireIntent("app-chat-library", "setGraphTags", [editingTagsId, tags], undefined, true);
    setEditingTagsId(null);
    lastTriggerRef.current?.focus();
  }

  function cancelTagEdit() {
    setEditingTagsId(null);
    lastTriggerRef.current?.focus();
  }

  function onTagEditKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitTagEdit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelTagEdit();
    }
  }

  function startDelete(row: AppChatLibraryRow, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setConfirmingDeleteId(row.id);
    setRenamingId(null);
    setEditingTagsId(null);
  }

  function confirmDelete() {
    if (confirmingDeleteId === null) return;
    transport.fireIntent("app-chat-library", "deleteChat", [confirmingDeleteId]);
    setConfirmingDeleteId(null);
  }

  function cancelDelete() {
    setConfirmingDeleteId(null);
    lastTriggerRef.current?.focus();
  }

  // Escape-to-cancel while either delete-confirm button has focus. Attached
  // to the buttons themselves (both are native, already-focusable elements)
  // rather than the wrapping div, so no synthetic tabIndex/role interactivity
  // is needed just to catch the key.
  function onDeleteConfirmKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelDelete();
    }
  }

  function clearFilters() {
    setSelectedTags(new Set());
    setShowArchived(true);
  }

  const total = state.rows.length;
  const resultsAnnouncement =
    total === 0 ? "" : filtered.length === 0 ? "No chats match" : `${filtered.length} results`;
  const visibleWorkspaces = state.workspaces.filter((w) => !w.archived);
  // ADR-020 stage 20.3.
  const expandedWorkspace = visibleWorkspaces.find((w) => w.id === workspaceSettingsId) ?? null;
  const modelCatalogOptions = composerState.route.modelOptions;

  return (
    <Dialog name="library" title="Chat Library" className="library-dialog">
      <div className="library-shell">
        {visibleWorkspaces.length > 0 && (
          <div className="library-workspace-tabs" role="group" aria-label="Workspaces">
            <button
              type="button"
              className={"view-preset-btn" + (selectedWorkspaceId === null ? " active" : "")}
              onClick={() => selectWorkspace(null)}
            >
              All
            </button>
            {visibleWorkspaces.map((workspace: AppWorkspaceRow) => (
              // ADR-020 stage 20.3: wraps the tab + its gear icon so the two
              // stay visually paired if .library-workspace-tabs's own
              // flex-wrap ever breaks the row across lines.
              <div key={workspace.id} className="library-workspace-tab-group">
                <button
                  type="button"
                  className={"view-preset-btn" + (selectedWorkspaceId === workspace.id ? " active" : "")}
                  onClick={() => selectWorkspace(workspace.id)}
                >
                  {workspace.name}
                </button>
                <button
                  type="button"
                  className="library-icon-button library-workspace-settings-button"
                  aria-label={`${workspaceSettingsId === workspace.id ? "Hide" : "Show"} default model settings for "${workspace.name}"`}
                  aria-expanded={workspaceSettingsId === workspace.id}
                  onClick={() => toggleWorkspaceSettings(workspace.id)}
                >
                  <Icon name="gear" />
                </button>
              </div>
            ))}
            {isCreatingWorkspace ? (
              <div className="library-workspace-new-input-wrap">
                <input
                  ref={newWorkspaceRef}
                  className="library-row-rename-input"
                  type="text"
                  value={newWorkspaceDraft}
                  onChange={(event) => setNewWorkspaceDraft(event.target.value)}
                  onKeyDown={onNewWorkspaceKeyDown}
                  placeholder="Workspace name"
                  aria-label="New workspace name"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            ) : (
              <button type="button" className="library-new-chat-button" onClick={startCreateWorkspace}>
                + Workspace
              </button>
            )}
          </div>
        )}

        {expandedWorkspace && (
          // ADR-020 stage 20.3: the inline-reveal itself - a plain block
          // below the tabs strip, not a popover/modal (see this file's own
          // module docstring for why CustomSelect, not Composer's Popover-
          // based ModelPicker, is the widget inside it).
          <div
            className="library-workspace-settings-panel"
            role="region"
            aria-label={`Default model for "${expandedWorkspace.name}"`}
          >
            <div className="settings-field">
              <span className="settings-field-label">Default model for &quot;{expandedWorkspace.name}&quot;</span>
              <p className="settings-update-status">
                {expandedWorkspace.defaultModelProvider && expandedWorkspace.defaultModelId
                  ? `Currently: ${expandedWorkspace.defaultModelProvider} / ${expandedWorkspace.defaultModelId}`
                  : "No default set - dispatch falls through to any node/branch pin, then the auto policy."}
              </p>
              {modelCatalogOptions.length === 0 ? (
                <p className="settings-update-status">
                  No models found for {composerState.route.provider}. Run a scan on its Settings page.
                </p>
              ) : null}
              <CustomSelect
                ariaLabel={`Default model for "${expandedWorkspace.name}"`}
                value={expandedWorkspace.defaultModelId}
                options={[
                  { id: "", label: "No workspace default" },
                  ...modelCatalogOptions.map((option) => ({ id: option.id, label: option.label })),
                ]}
                onChange={(modelId) => setWorkspaceDefaultModel(expandedWorkspace.id, modelId)}
              />
            </div>
          </div>
        )}

        <div className="library-header">
          {total > 0 && (
            <div className="library-search-wrap">
              <Icon name="search" />
              <input
                className="library-search-input"
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search chats..."
                aria-label="Search chats"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          )}
          {total > 0 && (
            <button
              type="button"
              className={"view-preset-btn library-archived-toggle" + (showArchived ? " active" : "")}
              aria-pressed={showArchived}
              onClick={() => setShowArchived((prev) => !prev)}
            >
              Archived
            </button>
          )}
          <button type="button" className="library-new-chat-button" onClick={newChat}>
            New Chat
          </button>
        </div>

        {availableTags.length > 0 && (
          <div className="library-tag-filter-row">
            <div className="view-row" role="group" aria-label="Filter by tag">
              {availableTags.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  className={"view-preset-btn" + (selectedTags.has(tag) ? " active" : "")}
                  aria-pressed={selectedTags.has(tag)}
                  onClick={() => toggleTag(tag)}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="library-visually-hidden" role="status" aria-live="polite">
          {resultsAnnouncement}
        </div>

        {state.notice && (
          <p className="library-notice" role="status">
            {state.notice}
          </p>
        )}

        {total === 0 ? (
          <div className="library-empty-state">
            <Icon name="chat" />
            <p className="library-empty-state-title">No saved chats yet</p>
            <p className="library-empty-state-sub">Start a new chat to begin building your library.</p>
            <button type="button" className="library-new-chat-button" onClick={newChat}>
              New Chat
            </button>
          </div>
        ) : filtered.length === 0 ? (
          query.trim() ? (
            <div className="library-search-empty">
              <p>No chats match &quot;{query}&quot;.</p>
              <button type="button" className="library-search-empty-clear" onClick={() => setQuery("")}>
                Clear search
              </button>
            </div>
          ) : (
            <div className="library-search-empty">
              <p>No chats match the current filters.</p>
              <button type="button" className="library-search-empty-clear" onClick={clearFilters}>
                Clear filters
              </button>
            </div>
          )
        ) : (
          <div className="library-groups">
            {groups.map((group) => (
              <section key={group.key} className="library-group" aria-label={group.key}>
                <h3 className="library-group-header">{group.key}</h3>
                <ul className="library-group-rows">
                  {group.rows.map((row) => {
                    const isRenaming = renamingId === row.id;
                    const isConfirmingDelete = confirmingDeleteId === row.id;
                    const isEditingTags = editingTagsId === row.id;
                    const messageWord = row.messageCount === 1 ? "message" : "messages";
                    return (
                      <li key={row.id} className="library-row">
                        {isRenaming ? (
                          <div className="library-row-primary">
                            <input
                              ref={renameRef}
                              className="library-row-rename-input"
                              type="text"
                              value={renameDraft}
                              onChange={(event) => setRenameDraft(event.target.value)}
                              onKeyDown={onRenameKeyDown}
                              aria-label={`Rename "${row.title}"`}
                              autoComplete="off"
                              spellCheck={false}
                            />
                            <p className="library-row-preview hint">Enter to save · Esc to cancel</p>
                          </div>
                        ) : isEditingTags ? (
                          <div className="library-row-primary">
                            <input
                              ref={tagsEditRef}
                              className="library-row-rename-input"
                              type="text"
                              value={tagsDraft}
                              onChange={(event) => setTagsDraft(event.target.value)}
                              onKeyDown={onTagEditKeyDown}
                              placeholder="tag-one, tag-two"
                              aria-label={`Edit tags for "${row.title}"`}
                              autoComplete="off"
                              spellCheck={false}
                            />
                            <p className="library-row-preview hint">Comma-separated · Enter to save · Esc to cancel</p>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="library-row-primary"
                            aria-label={`Open chat "${row.title}", ${row.messageCount} ${messageWord}, updated ${row.updatedLabel}`}
                            onClick={() => loadChat(row.id)}
                          >
                            <span className="library-row-title" title={row.title}>
                              {row.title}
                            </span>
                            {row.preview ? (
                              <span className="library-row-preview">{row.preview}</span>
                            ) : (
                              <span className="library-row-preview placeholder">No messages yet</span>
                            )}
                          </button>
                        )}

                        {!isRenaming && !isConfirmingDelete && !isEditingTags && row.messageCount > 0 && (
                          <span className="library-row-count">{row.messageCount}</span>
                        )}

                        {isRenaming ? (
                          <div className="library-row-actions">
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Save "${row.title}"`}
                              onClick={commitRename}
                              disabled={renameDraft.trim().length === 0}
                            >
                              <Icon name="check" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label="Cancel rename"
                              onClick={cancelRename}
                            >
                              <Icon name="x" />
                            </button>
                          </div>
                        ) : isEditingTags ? (
                          <div className="library-row-actions">
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Save tags for "${row.title}"`}
                              onClick={commitTagEdit}
                            >
                              <Icon name="check" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label="Cancel tag edit"
                              onClick={cancelTagEdit}
                            >
                              <Icon name="x" />
                            </button>
                          </div>
                        ) : isConfirmingDelete ? (
                          <div className="library-row-confirm">
                            <span className="library-row-confirm-label">Delete?</span>
                            <button
                              type="button"
                              className="library-icon-button library-icon-button-confirm-delete"
                              aria-label={`Confirm delete "${row.title}"`}
                              onClick={confirmDelete}
                              onKeyDown={onDeleteConfirmKeyDown}
                            >
                              <Icon name="check" />
                            </button>
                            <button
                              ref={deleteCancelRef}
                              type="button"
                              className="library-icon-button"
                              aria-label="Cancel delete"
                              onClick={cancelDelete}
                              onKeyDown={onDeleteConfirmKeyDown}
                            >
                              <Icon name="x" />
                            </button>
                          </div>
                        ) : (
                          <div className="library-row-actions">
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={row.favorite ? `Remove "${row.title}" from favorites` : `Add "${row.title}" to favorites`}
                              onClick={() => toggleFavorite(row)}
                            >
                              <Icon name={row.favorite ? "star-filled" : "star"} />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={row.archived ? `Unarchive "${row.title}"` : `Archive "${row.title}"`}
                              onClick={() => toggleArchived(row)}
                            >
                              <Icon name="archive" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Edit tags for "${row.title}"`}
                              onClick={(event) => startTagEdit(row, event.currentTarget)}
                            >
                              <Icon name="tag" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Rename "${row.title}"`}
                              onClick={(event) => startRename(row, event.currentTarget)}
                            >
                              <Icon name="pencil" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button library-icon-button-delete"
                              aria-label={`Delete "${row.title}"`}
                              onClick={(event) => startDelete(row, event.currentTarget)}
                            >
                              <Icon name="trash" />
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
