# Cleanup for pre-#503 branch knowledge entries

Older Graphlink versions used `branch:n1`, `branch:n2`, etc. as the source IDs for user-indexed conversation branches. Node IDs are reused between different saved graphs, so the old rows do not contain reliable graph ownership. **Graphlink will not guess or automatically delete them during upgrades.**

PR #503 changed all *new* branch indexing to a persistent, unique source ID. If search still returns stale old passages, you can preview and explicitly remove selected legacy rows.

From the repository root, run:

```bash
python -m backend.knowledge_legacy_cleanup
```

The default is **read-only**. It lists only old-format entries that match the historical source URI, title and MIME, showing their document IDs and collections but **not** their private text.

Review the IDs. To delete a specific row, explicitly name it and confirm:

```bash
python -m backend.knowledge_legacy_cleanup --delete-id 123 --confirm-delete
```

For an alternate/local-test knowledge database, add `--db /path/to/knowledge.db`. Multiple selected IDs can be passed using repeated `--delete-id` options. There is deliberately **no delete-all flag**.

Before deleting anything, Graphlink makes a full SQLite online backup beside `knowledge.db`, named `knowledge.db.legacy-branch-backup-*.db`, with private file permissions. The command verifies all selected IDs again within the deletion transaction; it refuses non-legacy IDs or a changed record. To recover, stop Graphlink and restore the backup carefully, preserving any newer changes made since that backup (restoring it as a whole rolls those back).

These legacy rows are not linked to particular saved graph IDs; only remove entries you have explicitly reviewed. The newer unique `branch:<hex>` documents are not eligible for this cleanup tool.
