import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import type { AppChatLibraryRow, AppChatLibraryState, AppWorkspaceRow } from "../../lib/bridge-core/generated/app-chat-library-state";
import type { AppComposerState } from "../../lib/bridge-core/generated/app-composer-state";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneStore } from "../canvas/sceneStore";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { IconButton } from "../ui/IconButton";
import { Icon } from "../ui/icons/Icon";
import { MenuButton, MenuItem } from "../ui/Menu";
import { SegmentedControl } from "../ui/SegmentedControl";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { ToggleButton } from "../ui/ToggleButton";
import { Badge } from "../ui/Badge";
import { useConfirm } from "../ui/ConfirmDialog";
import { formatCount, formatRelativeTime } from "../ui/format";
import { initialComposerState } from "./composerStore";
import "./chat-library.css";

const initialState: AppChatLibraryState = {
  schemaVersion: 1, minCompatibleSchemaVersion: 1, revision: 0,
  rows: [], workspaces: [], notice: null,
};
const NATIVE_DIALOG_TIMEOUT_MS = 5 * 60_000;
type BucketKey = "Today" | "Yesterday" | "Earlier";
const BUCKETS: BucketKey[] = ["Today", "Yesterday", "Earlier"];

function localDayStart(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function rowTime(row: AppChatLibraryRow): number | null {
  const iso = row.updatedAtIso ?? row.createdAtIso;
  if (!iso) return null;
  const value = Date.parse(iso);
  return Number.isFinite(value) ? value : null;
}

function groupRows(rows: AppChatLibraryRow[]): Array<{ key: BucketKey; rows: AppChatLibraryRow[] }> {
  const today = localDayStart(new Date());
  const buckets = new Map<BucketKey, AppChatLibraryRow[]>();
  for (const row of rows) {
    const time = rowTime(row);
    const day = time === null ? -Infinity : localDayStart(new Date(time));
    const key: BucketKey = day >= today ? "Today" : day >= localDayStart(new Date(today - 86400000)) ? "Yesterday" : "Earlier";
    const group = buckets.get(key) ?? [];
    group.push(row);
    buckets.set(key, group);
  }
  return BUCKETS.filter((key) => buckets.has(key)).map((key) => ({
    key,
    rows: (buckets.get(key) ?? []).sort((a, b) => (rowTime(b) ?? -Infinity) - (rowTime(a) ?? -Infinity) || b.id - a.id),
  }));
}

function chatMeta(row: AppChatLibraryRow): string {
  const timestamp = rowTime(row);
  return formatCount(row.messageCount) + " nodes · " +
    (timestamp === null ? row.updatedLabel : formatRelativeTime(timestamp));
}

export function ChatLibraryDialog({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const askConfirm = useConfirm();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [composerState, setComposerState] = useState<AppComposerState>(initialComposerState);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [managingWorkspaces, setManagingWorkspaces] = useState(false);
  const [workspaceSettingsId, setWorkspaceSettingsId] = useState<number | null>(null);
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [newWorkspaceDraft, setNewWorkspaceDraft] = useState("");
  const [importingWorkspaceId, setImportingWorkspaceId] = useState<number | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [editingTagsId, setEditingTagsId] = useState<number | null>(null);
  const [tagsDraft, setTagsDraft] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const unsubscribe = transport.subscribe("app-chat-library", (payload) => {
      const validated = TOPIC_VALIDATORS["app-chat-library"](payload);
      if (validated.ok) setState(validated.value as AppChatLibraryState);
      else console.error("[app-chat-library] rejected snapshot:", validated.errors);
    });
    // Another eager subscriber may already own the topic. Always request a
    // fresh snapshot when this lazy dialog mounts.
    transport.resubscribe("app-chat-library");
    return unsubscribe;
  }, [transport]);

  useEffect(() => transport.subscribe("app-composer", (payload) => {
    const validated = TOPIC_VALIDATORS["app-composer"](payload);
    if (validated.ok) setComposerState(validated.value as AppComposerState);
  }), [transport]);

  useEffect(() => {
    const input = document.querySelector<HTMLInputElement>(renamingId !== null ? "#chat-library-rename" : editingTagsId !== null ? "#chat-library-tags" : "#chat-library-workspace-name");
    if (renamingId !== null || editingTagsId !== null || creatingWorkspace) {
      input?.focus();
      input?.select();
    }
  }, [renamingId, editingTagsId, creatingWorkspace]);


  const workspaces = state.workspaces.filter((workspace) => !workspace.archived);
  const workspaceRows = useMemo(() =>
    selectedWorkspaceId === null ? state.rows : state.rows.filter((row) => row.workspaceId === selectedWorkspaceId),
    [state.rows, selectedWorkspaceId],
  );
  const availableTags = useMemo(() =>
    Array.from(new Set(workspaceRows.flatMap((row) => row.tags))).sort((a, b) => a.localeCompare(b)),
    [workspaceRows],
  );
  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return workspaceRows.filter((row) => {
      if (filter === "all" && row.archived) return false;
      if (filter === "starred" && (!row.favorite || row.archived)) return false;
      if (filter === "archived" && !row.archived) return false;
      if (selectedTags.size && ![...selectedTags].every((tag) => row.tags.includes(tag))) return false;
      return !term || (row.title + " " + row.preview).toLocaleLowerCase().includes(term);
    });
  }, [workspaceRows, query, filter, selectedTags]);
  const groups = useMemo(() => groupRows(filtered), [filtered]);
  const activeWorkspace = workspaces.find((workspace) => workspace.id === workspaceSettingsId);

  async function confirmNavigation(title: string): Promise<boolean> {
    const scene = store.getScene();
    if (scene.nodes.length === 0 && !scene.hasSavedChat) return true;
    return confirm({ title, body: "Your current edits will be saved before switching.", confirmLabel: "Continue" });
  }

  async function confirmDiscard(): Promise<boolean> {
    return confirm({
      title: "Discard incomplete chat?",
      body: "Some plugin nodes could not be restored. Discarding the incomplete on-screen copy will not save its changes. The original saved chat, including its missing plugin nodes, remains unchanged.",
      confirmLabel: "Discard incomplete copy",
      destructive: true,
    });
  }

  async function loadChat(id: number) {
    if (!(await confirmNavigation("Open another chat?"))) return;
    store.cancelWorkspacePreview();
    try {
      const outcome = await transport.request("app-chat-library", "loadChat", [id]);
      if (outcome === "discard-required") {
        if (!(await confirmDiscard())) return;
        const retry = await transport.request("app-chat-library", "loadChat", [id, true]);
        if (retry === "discard-required") return;
      }
      overlays.close();
    } catch (cause) {
      console.error("[chat-library] Could not open chat:", cause);
      setActionError("Couldn't open this chat. Details were written to the log.");
    }
  }

  async function newChat() {
    if (!(await confirmNavigation("Start a new chat?"))) return;
    store.cancelWorkspacePreview();
    const args = selectedWorkspaceId === null ? [] : [selectedWorkspaceId];
    try {
      const outcome = await transport.request("app-chat-library", "newChat", args);
      if (outcome === "discard-required") {
        if (!(await confirmDiscard())) return;
        const retry = await transport.request("app-chat-library", "newChat", [selectedWorkspaceId, true]);
        if (retry === "discard-required") return;
      }
      overlays.close();
    } catch (cause) {
      console.error("[chat-library] Could not start chat:", cause);
      setActionError("Couldn't start a new chat. Details were written to the log.");
    }
  }

  function toggleFavorite(row: AppChatLibraryRow) {
    transport.fireIntent("app-chat-library", "setGraphFavorite", [row.id, !row.favorite]);
  }
  function toggleArchived(row: AppChatLibraryRow) {
    transport.fireIntent("app-chat-library", "setGraphArchived", [row.id, !row.archived]);
  }
  function startRename(row: AppChatLibraryRow) {
    setActionError(null);
    setEditingTagsId(null);
    setRenamingId(row.id);
    setRenameDraft(row.title);
  }
  async function commitRename() {
    const title = renameDraft.trim();
    if (renamingId === null || !title) return;
    const id = renamingId;
    try {
      const updated = await transport.request("app-chat-library", "renameChat", [id, title]);
      if (updated) setRenamingId((current) => current === id ? null : current);
      else setActionError("Couldn't rename this chat. It may have changed elsewhere.");
    } catch (cause) {
      console.error("[chat-library] Rename failed:", cause);
      setActionError("Couldn't rename this chat. Details were written to the log.");
    }
  }
  function startTagEdit(row: AppChatLibraryRow) {
    setRenamingId(null);
    setEditingTagsId(row.id);
    setTagsDraft(row.tags.join(", "));
  }
  function commitTags() {
    if (editingTagsId === null) return;
    const tags = tagsDraft.split(",").map((value) => value.trim()).filter(Boolean);
    transport.fireIntent("app-chat-library", "setGraphTags", [editingTagsId, tags], undefined, true);
    setEditingTagsId(null);
  }
  async function deleteChat(row: AppChatLibraryRow) {
    const accepted = await askConfirm({
      title: "Delete chat?",
      body: "Delete “" + row.title + "”? This removes the saved chat from the library.",
      confirmLabel: "Delete chat",
      destructive: true,
    });
    if (!accepted) return;
    try {
      const deleted = await transport.request("app-chat-library", "deleteChat", [row.id]);
      if (!deleted) setActionError("Couldn't delete this chat. It may have changed elsewhere.");
    } catch (cause) {
      console.error("[chat-library] Delete failed:", cause);
      setActionError("Couldn't delete this chat. Details were written to the log.");
    }
  }
  function selectWorkspace(value: string) {
    if (value === "manage") {
      setManagingWorkspaces(true);
      return;
    }
    setSelectedWorkspaceId(value === "all" ? null : Number(value));
    setSelectedTags(new Set());
  }
  function saveWorkspace() {
    const name = newWorkspaceDraft.trim();
    if (!name) return;
    transport.fireIntent("app-chat-library", "createWorkspace", [name]);
    setCreatingWorkspace(false);
    setNewWorkspaceDraft("");
  }
  function setWorkspaceDefaultModel(workspace: AppWorkspaceRow, modelId: string) {
    const provider = modelId ? composerState.route.provider : "";
    transport.fireIntent("app-chat-library", "setWorkspaceDefaultModel", [workspace.id, provider, modelId], undefined, true);
  }
  function exportWorkspace(workspace: AppWorkspaceRow) {
    transport.fireIntent("app-chat-library", "exportWorkspace", [workspace.id], NATIVE_DIALOG_TIMEOUT_MS);
  }
  async function importWorkspace(workspace: AppWorkspaceRow) {
    if (importingWorkspaceId !== null) return;
    setImportingWorkspaceId(workspace.id);
    try {
      await transport.request("app-chat-library", "importWorkspace", [workspace.id], NATIVE_DIALOG_TIMEOUT_MS);
    } catch (cause) {
      console.error("[chat-library] Workspace import failed:", cause);
      setActionError("Couldn't import workspace. Details were written to the log.");
    } finally {
      setImportingWorkspaceId(null);
    }
  }

  function rowKeys(event: KeyboardEvent<HTMLButtonElement>) {
    const target = event.currentTarget;
    const buttons = Array.from(
      target.closest(".chat-library-groups")?.querySelectorAll<HTMLButtonElement>(".chat-library-row-primary") ?? [],
    );
    const index = buttons.indexOf(target);
    if (index < 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
    } else if (event.key === "F2" || event.key === "Delete") {
      event.preventDefault();
      const row = filtered.find((item) => String(item.id) === target.dataset.chatId);
      if (row) {
        if (event.key === "F2") startRename(row);
        else void deleteChat(row);
      }
    }
    // Enter activates the native row button.
  }

  return (
    <Dialog {...{ title: "Chat library" }} name="library" size="lg" className="chat-library-dialog">
      <div className="chat-library-layout">
        <div className="chat-library-heading">
          <p>Find and manage your conversations.</p>
          <Button variant="primary" iconStart="chat-plus" onClick={() => { void newChat(); }}>New chat</Button>
        </div>
        <div className="chat-library-toolbar">
          <TextField label="Search chats" type="search" size="sm" prefixIcon="search" suffix={{ type: "clear" }}
            placeholder="Search chats…" value={query} onValueChange={setQuery} autoComplete="off" />
          <SegmentedControl label="Filter chats" size="sm" value={filter} onChange={setFilter}
            options={[{ value: "all", label: "All" }, { value: "starred", label: "Starred" }, { value: "archived", label: "Archived" }]} />
          <Select ariaLabel="Workspace" value={selectedWorkspaceId === null ? "all" : String(selectedWorkspaceId)}
            onChange={selectWorkspace}
            options={[
              { value: "all", label: "All workspaces", icon: "workspace" },
              ...workspaces.map((workspace) => ({ value: String(workspace.id), label: workspace.name, icon: "workspace" as const })),
              { value: "manage", label: "Manage workspaces…", icon: "settings" },
            ]} />
        </div>
        {availableTags.length > 0 && (
          <div className="chat-library-tag-filters" role="group" aria-label="Filter by tag">
            <Icon name="tag" size="sm" />
            {availableTags.map((tag) => (
              <ToggleButton key={tag} size="sm" label={tag} pressed={selectedTags.has(tag)}
                onPressedChange={() => setSelectedTags((previous) => {
                  const next = new Set(previous);
                  if (next.has(tag)) next.delete(tag); else next.add(tag);
                  return next;
                })} />
            ))}
          </div>
        )}
        {managingWorkspaces && (
          <Card {...{ title: "Manage workspaces" }} actions={<IconButton icon="close" label="Close workspace manager" onClick={() => setManagingWorkspaces(false)} />}>
            <div className="chat-library-workspaces">
              {creatingWorkspace ? (
                <div className="chat-library-workspace-create">
                  <TextField id="chat-library-workspace-name" label="New workspace name" value={newWorkspaceDraft} onValueChange={setNewWorkspaceDraft}
                    onKeyDown={(event) => { if (event.key === "Enter") saveWorkspace(); if (event.key === "Escape") { event.preventDefault(); setCreatingWorkspace(false); } }} />
                  <Button onClick={saveWorkspace} disabled={!newWorkspaceDraft.trim()}>Create workspace</Button>
                  <Button variant="ghost" onClick={() => setCreatingWorkspace(false)}>Cancel</Button>
                </div>
              ) : <Button iconStart="plus" onClick={() => setCreatingWorkspace(true)}>Create workspace</Button>}
              {workspaces.map((workspace) => (
                <div className="chat-library-workspace" key={workspace.id}>
                  <Button variant="ghost" onClick={() => setWorkspaceSettingsId(workspaceSettingsId === workspace.id ? null : workspace.id)}
                    iconStart="workspace" aria-expanded={workspaceSettingsId === workspace.id}>
                    {workspace.name}
                  </Button>
                  {activeWorkspace?.id === workspace.id && (
                    <div className="chat-library-workspace-details">
                      <Select label={"Default model for " + workspace.name} value={workspace.defaultModelId}
                        onChange={(value) => setWorkspaceDefaultModel(workspace, value)}
                        options={[
                          { value: "", label: "No workspace default" },
                          ...composerState.route.modelOptions.map((model) => ({ value: model.id, label: model.label })),
                        ]} />
                      <Button iconStart="download" onClick={() => exportWorkspace(workspace)}>Export workspace…</Button>
                      <Button iconStart="upload" loading={importingWorkspaceId === workspace.id}
                        onClick={() => { void importWorkspace(workspace); }}>Import workspace…</Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}
        {state.notice && <p className="chat-library-notice" role="status">{state.notice}</p>}
        {actionError && <p className="chat-library-error" role="alert">{actionError}</p>}
        <span className="chat-library-visually-hidden" aria-live="polite" role="status">
          {state.rows.length ? formatCount(filtered.length) + " results" : ""}
        </span>
        {state.rows.length === 0 ? (
          <EmptyState icon="chat" {...{ title: "No saved chats yet" }} body="Start a new chat to build your library."
            actions={<Button iconStart="chat-plus" onClick={() => { void newChat(); }}>New chat</Button>} />
        ) : filtered.length === 0 ? (
          <EmptyState icon="search" {...{ title: "No matching chats" }} body="Try another search or change the filters."
            actions={<Button onClick={() => { setQuery(""); setFilter("all"); setSelectedTags(new Set()); setSelectedWorkspaceId(null); }}>Clear filters</Button>} />
        ) : (
          <div className="chat-library-groups">
            {groups.map((group) => (
              <section className="chat-library-group" key={group.key} aria-label={group.key}>
                <h3 className="chat-library-overline">{group.key}</h3>
                <ul className="chat-library-list">
                  {group.rows.map((row) => {
                    const editingTitle = renamingId === row.id;
                    const editingTags = editingTagsId === row.id;
                    const word = row.messageCount === 1 ? "message" : "messages";
                    return (
                      <li className="chat-library-row library-row" key={row.id}>
                        <Icon name="chat" size="md" className="chat-library-chat-icon" />
                        {editingTitle ? (
                          <div className="chat-library-inline-editor">
                            <TextField id="chat-library-rename" label={'Rename "' + row.title + '"'} value={renameDraft}
                              onValueChange={setRenameDraft} onKeyDown={(event) => {
                                if (event.key === "Enter") { event.preventDefault(); void commitRename(); }
                                if (event.key === "Escape") { event.preventDefault(); setRenamingId(null); }
                              }} />
                            <Button size="sm" onClick={() => { void commitRename(); }} disabled={!renameDraft.trim()}>Save</Button>
                            <IconButton icon="close" label="Cancel rename" onClick={() => setRenamingId(null)} />
                          </div>
                        ) : editingTags ? (
                          <div className="chat-library-inline-editor">
                            <TextField id="chat-library-tags" label={'Edit tags for "' + row.title + '"'} value={tagsDraft}
                              onValueChange={setTagsDraft} placeholder="Comma-separated tags"
                              onKeyDown={(event) => {
                                if (event.key === "Enter") { event.preventDefault(); commitTags(); }
                                if (event.key === "Escape") { event.preventDefault(); setEditingTagsId(null); }
                              }} />
                            <Button size="sm" onClick={commitTags}>Save tags</Button>
                            <IconButton icon="close" label="Cancel tag edit" onClick={() => setEditingTagsId(null)} />
                          </div>
                        ) : (
                          <button type="button" className="chat-library-row-primary" data-chat-id={row.id}
                            aria-label={'Open chat "' + row.title + '", ' + row.messageCount + ' ' + word + ', updated ' + row.updatedLabel}
                            onKeyDown={rowKeys} onClick={() => { void loadChat(row.id); }}>
                            <span className="chat-library-row-title">{row.title}</span>
                            <span className="chat-library-row-preview">{row.preview || "No messages yet"}</span>
                            <span className="chat-library-row-meta">{chatMeta(row)}</span>
                          </button>
                        )}
                        {row.tags.length > 0 && !editingTitle && !editingTags && (
                          <div className="chat-library-row-tags" aria-label="Tags">{row.tags.slice(0, 2).map((tag) => <Badge key={tag}>{tag}</Badge>)}</div>
                        )}
                        {!editingTitle && !editingTags && (
                          <div className="chat-library-row-actions">
                            <ToggleButton icon="star" size="sm" pressed={row.favorite}
                              className={"chat-library-star" + (row.favorite ? " is-starred" : "")}
                              aria-label={row.favorite ? 'Remove "' + row.title + '" from favorites' : 'Add "' + row.title + '" to favorites'}
                              onPressedChange={() => toggleFavorite(row)} />
                            <IconButton icon="edit" label={'Rename "' + row.title + '"'} size="sm"
                              className="chat-library-hover-action" onClick={() => startRename(row)} />
                            <MenuButton label="More" menuLabel={'More actions for "' + row.title + '"'} className="chat-library-hover-action">
                              <MenuItem icon="tag" label="Tag…" onSelect={() => startTagEdit(row)} />
                              <MenuItem icon="archive" label={row.archived ? "Unarchive" : "Archive"} onSelect={() => toggleArchived(row)} />
                              <MenuItem destructive label="Delete…" onSelect={() => { void deleteChat(row); }} />
                            </MenuButton>
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
