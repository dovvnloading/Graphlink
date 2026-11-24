# Reusable workflow library

Workflow Library is global to the local chats database (migration 7). A saved
version pins the version-one workflow contract and adapter descriptors, and
has no source canvas node IDs or input contents. Editing a library definition
appends an immutable revision; deleting a library entry archives its label
without invalidating nodes that reference older revisions.

## Canvas

Select a connected branch of ordinary Copy text / Word count notes, open
Workflows in the application bar, and choose Make workflow from selection.
Choose outputs and optionally expose operation choices. External sources
become named inputs, not copied contents. Save a new workflow or append a
revision to a library entry. Open the entry to inspect its definition,
history, export, rename, duplicate, delete, insert a pinned node, or unpack.

A pinned Workflow node can accept literal text, JSON, table inputs, or
connected text sources. Text/number/choice parameters are validated against
the frozen interface and registered adapter configuration. If exactly one text
output is exposed, it can connect to existing text-input nodes. A previewed
Graph Runner step resolves the exact pinned library revision and records a
linked pure child execution receipt in the graph's transaction. No external
effects are authorized by this adapter. A newer revision is never substituted
automatically; the upgrade preview explicitly resets input configuration and
results. Existing versions remain intact.

## Portable files and current scope

The .graphlink-workflow file contains only a validated version-one spec and
parameter declarations. Export scrubs configuration JSON before validating;
import rejects incompatible ports, cycles, unknown adapter versions, secret
patterns and non-pure adapters. Import creates a new local library identity.

All eight built-in pure registry transforms are supported for imported
definitions. Canvas capture and editable unpack currently cover the existing
plain-note text adapters; imported definitions using other transforms can run
as pinned nodes but cannot yet be unpacked as native editable canvas
operations. Model, scientific, MCP and other external/effectful steps retain
their existing runner and approvals and cannot be captured by this library.
