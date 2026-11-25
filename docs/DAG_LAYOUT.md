# DAG-aware canvas organization

Graphlink's **Organize graph → Layout model** selector now provides:

- **Conversation tree — stable** (existing default): primary-parent-first
  layered tree packing, unchanged for existing callers.
- **Directed graph — convergence-aware** (opt in): considers all eligible
  directed scene connections, including secondary and cross-tree links.
  Rank placement uses longest-path topological layering, four forward/
  backward barycentric ordering passes, measured card dimensions and
  deterministic clearances. Converging nodes sit below every retained
  incoming DAG edge. Disconnected components remain separated.

Real scene edges, source content, group membership and undo state are never
modified by layout. Cyclic legacy/reference edges are retained and rendered,
but cycle-closing arrows are ignored solely for rank constraints. Primary
edges take priority over secondary/reference edges in deterministic cycle
removal. All calculations run on the server's shadow document during
**Preview layout**, and **Apply layout** recomputes/checks a fingerprint
including the requested and effective layout styles. A stale preview or a
style switch cannot apply unreviewed node coordinates. One undo restores
all moved cards.

The experimental mode is bounded to 400 visible cards and 2,000 eligible
links; scenes with frames/containers or larger graphs automatically use the
existing group-safe tree organizer, clearly labeled as **Effective layout:
Conversation tree** in the preview. This fallback is deliberate, rather
than moving grouped cards across semantic boundaries or allowing expensive
quadratic processing. Scoped/selected layouts and fixed-node constraints
are supported through the same existing preview/obstacle planner.

This is a first deterministic DAG layout option, not full ELK compound
port-constrained routing. Future stages may replace the restricted fallback
with an ELK-style compound solver after cross-group and large-graph
invariants are benchmarked.
