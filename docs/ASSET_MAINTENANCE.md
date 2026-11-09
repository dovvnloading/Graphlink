# Graphlink asset maintenance

Graphlink stores images as SHA-256-named binary files next to `chats.db`, under `assets/<first-two-hex-digits>/<sha256>`. Removing a graph does **not** immediately remove its files: other graphs, retained SQLite backups, an unsaved in-memory edit, or an undo operation may still need them.

## Safe cleanup (offline)

1. Save your work and **close all Graphlink desktop/headless processes** that use the selected data directory.
2. Inspect a dry-run report:

   ```sh
   python -m backend.asset_gc --db ~/.graphlink/chats.db
   ```

3. After reviewing the orphan count and disk usage, opt in to cleanup:

   ```sh
   python -m backend.asset_gc --db ~/.graphlink/chats.db --apply
   ```

On Windows, replace `~/.graphlink/chats.db` with your actual `chats.db` path.

The tool acquires Graphlink's existing **OS-enforced instance lock**, so it refuses to run if the desktop or headless backend is using that database directory. It scans the live graph records plus **every retained SQLite chat backup** before deleting any file. Invalid/inaccessible backup or graph data blocks the whole cleanup instead of guessing what is unreferenced.

The default **seven-day grace period** keeps recently written blobs even if the current graph database does not reference them. `--min-age-days N` changes that window; reducing it increases risk. Only correctly-shaped, ordinary hash-named asset files are candidates; unknown files and symlinks are not removed. Dry run is the default; deletion requires `--apply`.

## Backup and privacy expectations

A deleted graph's image will remain in `assets/` as long as any retained backup references it. This is intentional: otherwise restoring an older graph would leave missing images. The tool does **not** delete or rewrite backup files, exported `.graphlink` archives, or user files outside the asset store. Fully purging sensitive deleted images requires a separate conscious decision about historical backups and external archives.

Use the application's existing backup/export features before destructive maintenance. This is a conservative manual recovery of storage space, not an automatic secure-erase feature or a claim that the disk no longer contains recoverable copies.
