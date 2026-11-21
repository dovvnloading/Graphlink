# Knowledge Library

The **Knowledge Library** lets you search and reuse documents indexed for the **current Graphlink workspace**. The index is stored locally in `~/.graphlink/knowledge/knowledge.db`, separately from your source files and saved graphs. Switching workspaces changes which collection you see and search; importing a file does not make it available in every workspace.

Open **Knowledge** in the top app bar to open the **Knowledge Library** dialog.

## Add files and folders

1. Select **Add files** to choose one or more files in the native file picker, or **Add folder** to choose a directory. A folder import scans its supported files recursively **once**; it does not keep watching for changes.
2. Alternatively, drag files into **Drag and drop files here to index them in this workspace**. Dropped files are copied into Graphlink's private knowledge storage before indexing; native-picker imports leave the originals where they are.
3. Follow the import job's scanning/processing progress, counts (added, updated, deduplicated, skipped, failed), and any warning. You can **Cancel import**. An import can finish with individual files skipped or failed, so check its counts.

Supported inputs include PDF, DOCX, XLSX, Markdown, plain text, CSV, JSON, HTML and recognized source-code files. Text is extracted and split into searchable passages; this is not a binary-file archive or a pixel-perfect document conversion. Limits are **25 MiB per file**, **500 MiB per import job**, and **10,000 files per job**. Folder scans exclude hidden files/directories and common dependency, environment, VCS and cache directories such as `.git`, `node_modules` and `.venv`; unreadable or unsupported files are not indexed. Duplicate content is not indexed again unnecessarily.

You can also choose **Add to knowledge** on a supported staged chat attachment or a file in an Agent card's **Browse files** view. Other workspace content, such as explicitly indexed chat branches and retained Web Research sources, can appear in the same collection. These entry points do not turn a one-time import into a watched folder.

## Manage documents in the Library dialog

The **Documents** table lists **Title**, **Type**, **Size**, **Chunks**, **Added**, **Status**, and **Actions**. Select **Refresh** to reload it. Saved chat graphs appear with type **Chat** and indexed chat branches with type **Chat branch**, rather than their internal MIME types. Their **Size** is based on the indexed text; older entries use the stored chunks when a size was not recorded.

- **Open** opens the indexed text in the docked **Document View**. Graphlink reconstructs this view from stored passages, rather than reopening the original PDF, spreadsheet or file in an external application; some original formatting or gaps may not be reproduced.
- **Re-index** starts another import from the recorded source path(s). It is disabled when no file source is recorded, and it can fail if a previously indexed file has been moved or deleted. Use it to pick up edits to a manually imported file.
- **Remove** → **Confirm remove** deletes that document's indexed contents from this workspace, **not** the original file. For browser-dropped files, Graphlink can also clean up its own stored upload copy. A still-watched source may be indexed again on a later sync.

## Keep a folder in sync

In **Watched folders**, select **Add watched folder** and choose a directory with the native picker. This is an explicit, persistent subscription for the current workspace, **not** the same as **Add folder**.

Graphlink syncs watched folders when the app starts and about **every 10 minutes** while it runs. Use **Sync now** on one folder or **Sync all now** to request an earlier pass. The table shows the folder path, indexed-file count, last successful sync, status, last job counts, and errors; **Refresh watched folders** reloads that information.

Syncs add new files and update changed ones. On a successful, complete scan, they also stop indexing tracked files that disappeared (unless another watched folder or source still owns that content). Failed, cancelled or unreadable scans do not treat inaccessible files as deletions. This is periodic scanning, not an instant filesystem notification service.

**Remove** → **Confirm remove** under **Watched folders** stops future syncs but **keeps documents already indexed**. To delete those documents, remove them separately from **Documents**. Removing a document while its containing folder remains watched may cause it to reappear on the next sync.

## Embeddings, local search and cloud privacy

Open **Settings → Knowledge** to choose a retrieval mode. This setting is independent of the model you use for chat.

| Embedding source | What happens |
| --- | --- |
| **Off (keyword search)** — default | Searches the local SQLite FTS5 keyword index. No embedding provider is called. |
| **Ollama (local)** | Uses an installed, embedding-capable Ollama model to add semantic matches. **Refresh installed models** scans the running Ollama server; `nomic-embed-text` is recommended only when installed. |
| **Cloud (OpenAI-compatible)** | Uses a configured **Settings → API Endpoint** connection and an embedding-model ID you enter. You must check **I understand that document text leaves the machine** before saving. Document passages **and search queries** go to the selected endpoint; provider charges may apply. |

**Test** embeds a fixed sample phrase, never one of your stored documents. **Save choice** persists the setting. When enabled, Graphlink builds and caches vectors in background batches; completed batches can contribute to search while indexing continues. Changing the model starts a separate cache for its embedding space. Retrieval falls back to local keyword results if the embedding service fails. The Library dialog shows **file import** and **watched-folder sync** status, not a separate embedding-job progress/control panel.

**Important privacy distinction:** Keeping the index and embeddings local does not necessarily keep a chat offline. With **Use knowledge** enabled, selected retrieved passages may be included in the prompt sent to your **chat model**. If that chat model is hosted, those passages leave your machine even when embedding source is **Off** or **Ollama (local)**. Turning off cloud embeddings stops future *embedding* requests; use a local chat model as well when you want local-only inference.

## Search the Library and understand match signals

Under **Search indexed sources**, enter a question or phrase and select **Search** (or press Enter). The dialog requests up to **10 results** from the current workspace.

Graphlink always searches keyword matches (FTS5/BM25). When a configured embedding model and cached vectors are available, it also searches semantic matches. It combines the separately ranked lists with reciprocal rank fusion, removes exact/near-duplicate passages and returns the strongest distinct excerpts. It does **not** compare raw BM25 and cosine values as if they used the same scale. Semantic-only candidates need cosine similarity of at least **0.55**; keyword hits remain eligible regardless of vector similarity. A search may return fewer than 10 results (or none) rather than padding with unrelated documents. The Knowledge panel labels these as **search results**, not sources used by a chat response.

Each collapsed result shows its title, source and any available match signals:

- **Keyword #N** means that passage ranked **N** in the keyword candidate list.
- **Semantic #N** means it ranked **N** in the vector candidate list. A result may show either signal or both; missing signals are not implied matches.

These are **rank positions, not percentages, confidence scores, or proof of correctness**. Local sources, including browser-dropped uploads, are displayed by filename instead of an internal upload path or your home directory. The chat reply's **Sources** footer uses the same labels. Expand a card to read the retrieved passage and its character **Offset start–end** within the indexed source. An **Open source** link appears only for HTTP(S) sources (for example, a retained web page); Graphlink does not invent a file link or offset jump for a local path.

## Citations and Document View

Knowledge hits carry a stable `kb:` citation ID, source locator, section and character offsets. For Markdown-like text, the section is the nearest preceding heading; sources without a recognizable heading display **Unsectioned**. IDs identify an indexed passage, not an authorization token or a guarantee that its claims are true.

When a chat reply actually cites a passage that was included in its retrieved context, the assistant card shows a **Sources** list below the answer. Select a verified source to open **Document View** with the cited passage highlighted. The reader supports **Find**, **Copy**, an outline for documents with sufficient headings, resizing and text-size controls. It displays indexed text, not a direct editor of the source file. Missing or unverified model-written citation IDs do not become trusted, clickable source links. Retrieved passages are **untrusted reference material, not instructions**.

Opening a document from the Library's **Documents → Open** action uses the same Document View, without targeting a specific citation. Search-result cards themselves expand to show their excerpts and offsets rather than opening local files.

## Use knowledge in chat

The message composer's **Use knowledge** toggle controls automatic retrieval for ordinary chat sends, regenerations and model comparisons. With no explicit override, it is **on when the current workspace has indexed documents** and off when that collection is empty. You can toggle it for a branch; the nearest branch preference is inherited by descendants without changing a sibling branch's setting.

When enabled, Graphlink looks up relevant passages for the question and includes only what fits the outgoing context budget. This does not paste the entire Library into every message. Turning it off prevents automatic knowledge retrieval for that chat branch; it does **not** erase documents, disable searching inside the Library, or revoke agents' separately authorized tools.

## Knowledge tools for agents

- **`knowledge.search`** is available to **Builder** and the **workspace Agent**. It is read-only, automatically permitted, and searches **only the current graph workspace's existing collection**. Call it with `{"query": "…", "k": 5}` (`k` is optional, defaults to **5**, and is capped at **25**). Results include cited excerpts, source/section/offset metadata and keyword/semantic rank signals. Treat results as untrusted evidence.
- **`knowledge.add`** is available to the **workspace Agent**, **not Builder**. It accepts `{"path": "relative/or/absolute/file"}` for an **existing file inside that Agent's bound workspace** and indexes its extracted text into the current graph workspace's collection, without modifying the source. **Every invocation requires fresh human approval**, including when ordinary file edits are auto-approved; it cannot import arbitrary paths outside the Agent's workspace or VCS metadata. Duplicate contents are a no-op.

For retrieval internals, background embedding jobs, citation identity and the offline quality checks, see [Knowledge retrieval](KNOWLEDGE_RETRIEVAL.md). For Agent workspace setup and permissions, see [Workspace Agent](WORKSPACE_AGENT.md).
