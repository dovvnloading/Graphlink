"""Distance trees and Newick analysis for the grant-gated Biology Workbench.

Algorithms use only the Python standard library. Branch lengths are
nonnegative, dimensionless distances in the units supplied by the user.
Rooted consensus and RF comparisons deliberately do not estimate lengths.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from graphlink_plugins.scientific.core import Citation, Field, Operation


_MAX_TAXA = 32
_MAX_TREES = 100
_MAX_LENGTH = 1e12
_LENGTH_RE = re.compile(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


@dataclass(eq=False)
class _Node:
    name: str | None = None
    length: float | None = None  # Length of the incoming edge; root has none.
    children: list[_Node] = field(default_factory=list)


def _taxa(node: _Node) -> tuple[str, ...]:
    if not node.children:
        if node.name is None:
            raise ValueError("A tree leaf must have a taxon label.")
        return (node.name,)
    return tuple(sorted(name for child in node.children for name in _taxa(child)))


def _leaves(root: _Node) -> tuple[str, ...]:
    leaves = _taxa(root)
    if not 2 <= len(leaves) <= _MAX_TAXA or len(set(leaves)) != len(leaves):
        raise ValueError(f"A tree needs 2–{_MAX_TAXA} distinct taxa.")
    return leaves


def _clean_label(label: object) -> str:
    if (not isinstance(label, str) or not 1 <= len(label) <= 128
            or not label.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in label)):
        raise ValueError("Taxon labels must be distinct, nonblank printable text (at most 128 characters).")
    return label


class _Newick:
    """Bounded recursive-descent parser, including quotes and bracket comments."""

    def __init__(self, text: str):
        if not isinstance(text, str) or not 1 <= len(text) <= 65536:
            raise ValueError("Newick input must contain 1–65,536 characters.")
        self.text = text
        self.pos = 0

    def _skip(self) -> None:
        s = self.text
        while self.pos < len(s):
            if s[self.pos].isspace():
                self.pos += 1
            elif s[self.pos] == "[":
                depth = 0
                while self.pos < len(s):
                    char = s[self.pos]
                    self.pos += 1
                    if char == "[":
                        depth += 1
                        if depth > 8:
                            raise ValueError("Newick comment nesting is too deep.")
                    elif char == "]":
                        depth -= 1
                        if not depth:
                            break
                if depth:
                    raise ValueError("Unterminated Newick comment.")
            else:
                break

    def _label(self) -> str | None:
        self._skip()
        s = self.text
        if self.pos < len(s) and s[self.pos] == "'":
            self.pos += 1
            result = []
            while self.pos < len(s):
                char = s[self.pos]
                self.pos += 1
                if char == "'":
                    if self.pos < len(s) and s[self.pos] == "'":
                        result.append("'")
                        self.pos += 1
                    else:
                        return "".join(result)
                else:
                    result.append(char)
            raise ValueError("Unterminated quoted Newick label.")
        start = self.pos
        while (self.pos < len(s) and not s[self.pos].isspace()
               and s[self.pos] not in ",():;[]"):
            self.pos += 1
        return s[start:self.pos] or None

    def _length(self) -> float | None:
        self._skip()
        if self.pos >= len(self.text) or self.text[self.pos] != ":":
            return None
        self.pos += 1
        self._skip()
        start = self.pos
        while (self.pos < len(self.text) and not self.text[self.pos].isspace()
               and self.text[self.pos] not in ",();[]"):
            self.pos += 1
        token = self.text[start:self.pos]
        if not _LENGTH_RE.fullmatch(token):
            raise ValueError("Newick branch lengths must be finite, nonnegative decimals.")
        value = float(token)
        if not math.isfinite(value) or value > _MAX_LENGTH:
            raise ValueError("Newick branch length exceeds the finite supported range.")
        return value

    def _node(self, depth: int = 0) -> _Node:
        if depth > 64:
            raise ValueError("Newick nesting is too deep.")
        self._skip()
        if self.pos >= len(self.text):
            raise ValueError("Incomplete Newick tree.")
        children: list[_Node] = []
        if self.text[self.pos] == "(":
            self.pos += 1
            children.append(self._node(depth + 1))
            while True:
                self._skip()
                if self.pos >= len(self.text):
                    raise ValueError("Unclosed Newick group.")
                mark = self.text[self.pos]
                self.pos += 1
                if mark == ")":
                    break
                if mark != ",":
                    raise ValueError("Expected comma or closing parenthesis in Newick.")
                children.append(self._node(depth + 1))
                if len(children) > _MAX_TAXA:
                    raise ValueError("Too many Newick children.")
            if len(children) < 2:
                raise ValueError("Newick internal nodes require at least two children.")
        name = self._label()
        if not children:
            name = _clean_label(name)
        elif name is not None and name:
            _clean_label(name)
        length = self._length()
        return _Node(name or None, length, children)

    def tree(self) -> _Node:
        node = self._node()
        self._skip()
        if self.pos >= len(self.text) or self.text[self.pos] != ";":
            raise ValueError("Newick trees must end with a semicolon.")
        self.pos += 1
        node.length = None  # Root-edge lengths have no path interpretation.
        _leaves(node)
        return node


def _parse(text: str) -> _Node:
    parser = _Newick(text)
    root = parser.tree()
    parser._skip()
    if parser.pos != len(text):
        raise ValueError("Expected exactly one Newick tree.")
    return root


def _forest(text: str) -> list[_Node]:
    parser = _Newick(text)
    trees: list[_Node] = []
    while True:
        parser._skip()
        if parser.pos == len(text):
            break
        if len(trees) >= _MAX_TREES:
            raise ValueError(f"Consensus supports at most {_MAX_TREES} trees.")
        trees.append(parser.tree())
    if len(trees) < 2:
        raise ValueError("Consensus needs at least two Newick trees.")
    taxa = _leaves(trees[0])
    if any(_leaves(tree) != taxa for tree in trees[1:]):
        raise ValueError("All consensus trees must have the same taxa.")
    return trees


def _quote(label: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        return label
    return "'" + label.replace("'", "''") + "'"


def _newick(root: _Node) -> str:
    def encode(node: _Node, is_root: bool) -> str:
        children = ("(" + ",".join(encode(c, False) for c in sorted(node.children, key=_taxa)) + ")"
                    if node.children else "")
        name = _quote(node.name) if node.name else ""
        length = (":" + format(node.length, ".15g")
                  if not is_root and node.length is not None else "")
        return children + name + length
    return encode(root, True) + ";"


def _weighted(root: _Node) -> bool:
    return all(child.length is not None and _weighted(child) for child in root.children)


def _total_length(root: _Node) -> float:
    return math.fsum((child.length or 0.0) + _total_length(child) for child in root.children)


def _matrix(data: dict) -> tuple[list[str], dict[tuple[int, int], float]]:
    try:
        names = json.loads(data["taxa"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Taxa must be a JSON array of distinct labels.") from exc
    if (not isinstance(names, list) or not 2 <= len(names) <= _MAX_TAXA
            or any(type(name) is not str for name in names)):
        raise ValueError(f"Taxa must be a JSON array with 2–{_MAX_TAXA} string labels.")
    names = [_clean_label(name) for name in names]
    if len(set(names)) != len(names):
        raise ValueError("Taxon labels must be unique.")
    matrix = data["distances"]
    n = len(names)
    if not isinstance(matrix, list) or len(matrix) != n or any(
            not isinstance(row, list) or len(row) != n for row in matrix):
        raise ValueError("The distance matrix must be square and match the taxa list.")
    distances: dict[tuple[str, str], float] = {}
    for i in range(n):
        for j in range(n):
            value = matrix[i][j]
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or value < 0 or value > _MAX_LENGTH):
                raise ValueError("Distances must be finite, nonnegative and at most 1e12.")
            if i == j and value != 0:
                raise ValueError("Distance matrix diagonal must be zero.")
            if i < j:
                other = matrix[j][i]
                if (type(other) not in (int, float) or not math.isfinite(other)
                        or abs(value - other) > 1e-10 * max(1, value, abs(other))):
                    raise ValueError("Distance matrix must be symmetric.")
                distances[names[i], names[j]] = (value + other) / 2
    sorted_names = sorted(names)
    ordered: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = sorted_names[i], sorted_names[j]
            ordered[i, j] = distances[a, b] if (a, b) in distances else distances[b, a]
    return sorted_names, ordered


def _pair(i: int, j: int) -> tuple[int, int]:
    return (min(i, j), max(i, j))


def _nonnegative(value: float, label: str) -> float:
    if not math.isfinite(value) or abs(value) > 2 * _MAX_LENGTH:
        raise ValueError(f"{label} exceeds the supported numerical range.")
    return max(0.0, value)


def _distance_result(root: _Node, algorithm: str, clipped: int = 0) -> dict:
    leaves = _leaves(root)
    return {
        "summary": (f"{algorithm}: inferred a tree with {len(leaves)} taxa. "
                    + (f"Clipped {clipped} negative fitted limbs to zero; input distances may not be additive. "
                       if clipped else "")
                    + "Branch lengths use the input distance scale."),
        "values": {"newick": _newick(root), "taxa_count": len(leaves),
                   "total_branch_length": _total_length(root),
                   "negative_limbs_clipped": clipped,
                   "branch_length_unit": "input distance units"},
    }


def upgma(data: dict) -> dict:
    names, d = _matrix(data)
    active: dict[int, tuple[_Node, int, float, tuple[str, ...]]] = {
        i: (_Node(name), 1, 0.0, (name,)) for i, name in enumerate(names)
    }
    next_id = len(active)
    while len(active) > 1:
        ids = sorted(active)
        i, j = min(((i, j) for k, i in enumerate(ids) for j in ids[k + 1:]),
                   key=lambda ij: (d[_pair(*ij)], active[ij[0]][3], active[ij[1]][3]))
        a, na, ha, _ = active[i]
        b, nb, hb, _ = active[j]
        height = d[_pair(i, j)] / 2
        a.length = _nonnegative(height - ha, "UPGMA branch length")
        b.length = _nonnegative(height - hb, "UPGMA branch length")
        node = _Node(children=[a, b])
        for k in ids:
            if k not in (i, j):
                d[_pair(k, next_id)] = (na * d[_pair(i, k)] + nb * d[_pair(j, k)]) / (na + nb)
        del active[i], active[j]
        active[next_id] = (node, na + nb, height, _taxa(node))
        next_id += 1
    return _distance_result(next(iter(active.values()))[0], "UPGMA")


def neighbor_joining(data: dict) -> dict:
    names, d = _matrix(data)
    active = {i: _Node(name) for i, name in enumerate(names)}
    next_id = len(active)
    clipped = 0

    def limb(node: _Node, length: float) -> None:
        nonlocal clipped
        if not math.isfinite(length) or abs(length) > 2 * _MAX_LENGTH:
            raise ValueError("Neighbor-joining limb exceeds the numerical range.")
        if length < -1e-10:
            clipped += 1
        node.length = max(0.0, length)

    while len(active) > 3:
        ids = sorted(active)
        m = len(ids)
        row_sums = {i: math.fsum(d[_pair(i, j)] for j in ids if j != i) for i in ids}
        i, j = min(((i, j) for k, i in enumerate(ids) for j in ids[k + 1:]),
                   key=lambda ij: ((m - 2) * d[_pair(*ij)] - row_sums[ij[0]] - row_sums[ij[1]],
                                   _taxa(active[ij[0]]), _taxa(active[ij[1]])))
        delta = (row_sums[i] - row_sums[j]) / (m - 2)
        a, b = active[i], active[j]
        dij = d[_pair(i, j)]
        limb(a, (dij + delta) / 2)
        limb(b, (dij - delta) / 2)
        node = _Node(children=[a, b])
        for k in ids:
            if k not in (i, j):
                d[_pair(k, next_id)] = (d[_pair(i, k)] + d[_pair(j, k)] - dij) / 2
        del active[i], active[j]
        active[next_id] = node
        next_id += 1
    ids = sorted(active)
    if len(ids) == 2:
        i, j = ids
        limb(active[i], d[_pair(i, j)] / 2)
        limb(active[j], d[_pair(i, j)] / 2)
    else:
        i, j, k = ids
        limb(active[i], (d[_pair(i, j)] + d[_pair(i, k)] - d[_pair(j, k)]) / 2)
        limb(active[j], (d[_pair(i, j)] + d[_pair(j, k)] - d[_pair(i, k)]) / 2)
        limb(active[k], (d[_pair(i, k)] + d[_pair(j, k)] - d[_pair(i, j)]) / 2)
    return _distance_result(_Node(children=list(active.values())), "Neighbor joining", clipped)


def _graph(root: _Node):
    """Convert a rooted representation into the same undirected edge tree."""
    graph: dict[int, list[tuple[int, float | None]]] = {}
    labels: dict[int, str | None] = {}

    def visit(node: _Node) -> int:
        index = len(graph)
        graph[index] = []
        labels[index] = node.name if not node.children else None
        for child in node.children:
            other = visit(child)
            graph[index].append((other, child.length))
            graph[other].append((index, child.length))
        return index

    visit(root)
    return graph, labels


def _orient(graph, labels, vertex: int, parent: int | None) -> _Node:
    node = _Node(name=labels[vertex])
    for other, weight in graph[vertex]:
        if other != parent:
            child = _orient(graph, labels, other, vertex)
            child.length = weight
            node.children.append(child)
    return node


def _collapse(node: _Node) -> _Node:
    node.children = [_collapse(child) for child in node.children]
    if node.name is None and len(node.children) == 1:
        only = node.children[0]
        only.length = (node.length + only.length
                       if node.length is not None and only.length is not None else None)
        return only
    return node


def _on_edge(graph, labels, a: int, b: int, from_a: float | None) -> _Node:
    weight = next(edge for other, edge in graph[a] if other == b)
    left = _orient(graph, labels, a, b)
    right = _orient(graph, labels, b, a)
    left.length = from_a
    right.length = (max(0.0, weight - from_a)
                    if weight is not None and from_a is not None else None)
    left, right = _collapse(left), _collapse(right)
    return _Node(children=[left, right])


def _path(graph, start: int, end: int) -> list[tuple[int, int, float | None]]:
    parents: dict[int, int | None] = {start: None}
    edges: dict[int, float | None] = {}
    pending = [start]
    while pending and end not in parents:
        here = pending.pop()
        for other, weight in graph[here]:
            if other not in parents:
                parents[other] = here
                edges[other] = weight
                pending.append(other)
    result: list[tuple[int, int, float | None]] = []
    here = end
    while True:
        previous = parents[here]
        if previous is None:
            break
        result.append((previous, here, edges[here]))
        here = previous
    result.reverse()
    return result


def _require_length(weight: float | None) -> float:
    if weight is None:
        raise ValueError("A weighted tree requires every branch length.")
    return weight


def _midpoint(graph, labels) -> _Node:
    if any(weight is None for neighbors in graph.values() for _, weight in neighbors):
        raise ValueError("Midpoint rerooting requires every branch length.")
    tips = sorted((name, index) for index, name in labels.items() if name is not None)
    max_distance = -1.0
    diameter_path = []
    for i, (_, a) in enumerate(tips):
        for _, b in tips[i + 1:]:
            path = _path(graph, a, b)
            distance = math.fsum(_require_length(weight) for _, _, weight in path)
            if distance > max_distance:
                max_distance, diameter_path = distance, path
    if max_distance == 0:
        a, b, _ = diameter_path[0]
        return _on_edge(graph, labels, a, b, 0.0)
    remaining = max_distance / 2
    for a, b, length in diameter_path:
        length = _require_length(length)
        if remaining == 0:
            return _collapse(_orient(graph, labels, a, None))
        if remaining < length:
            return _on_edge(graph, labels, a, b, remaining)
        remaining -= length
    return _collapse(_orient(graph, labels, diameter_path[-1][1], None))


def reroot_tree(data: dict) -> dict:
    root = _parse(data["tree"])
    graph, labels = _graph(root)
    if data["method"] == "midpoint":
        changed = _midpoint(graph, labels)
        chosen = "midpoint of the longest tip-to-tip path"
    elif data["method"] == "outgroup":
        name = data["outgroup"]
        matches = [i for i, label in labels.items() if label == name]
        if len(matches) != 1:
            raise ValueError("The outgroup must name exactly one leaf in this tree.")
        tip = matches[0]
        other, length = graph[tip][0]
        changed = _on_edge(graph, labels, tip, other, length / 2 if length is not None else None)
        chosen = f"outgroup {name!r} (split pendant edge in half)"
    else:
        raise ValueError("Choose outgroup or midpoint rerooting.")
    if _leaves(changed) != _leaves(root):
        raise ValueError("Rerooting did not preserve the taxon set.")
    return {
        "summary": f"Rerooted {len(_leaves(root))} taxa by {chosen}; patristic distances are unchanged.",
        "values": {"newick": _newick(changed), "method": data["method"],
                   "taxa_count": len(_leaves(root)),
                   "total_branch_length": (_total_length(changed) if _weighted(changed)
                                           else "undefined (missing branch lengths)")},
    }


def _clades(root: _Node, rooted: bool) -> set[frozenset[str]]:
    all_taxa = frozenset(_leaves(root))
    count = len(all_taxa)
    found = set()

    def visit(node: _Node, at_root: bool) -> frozenset[str]:
        if not node.children:
            if node.name is None:
                raise ValueError("A tree leaf must have a taxon label.")
            return frozenset((node.name,))
        members = frozenset().union(*(visit(child, False) for child in node.children))
        size = len(members)
        if not at_root and 2 <= size <= (count - 1 if rooted else count - 2):
            if rooted:
                found.add(members)
            else:
                complement = all_taxa - members
                side = min((members, complement), key=lambda s: (len(s), tuple(sorted(s))))
                found.add(side)
        return members

    visit(root, True)
    return found


def _split_name(clade: frozenset[str]) -> str:
    return "{" + ", ".join(sorted(clade)) + "}"


def compare_trees(data: dict) -> dict:
    a, b = _parse(data["tree_a"]), _parse(data["tree_b"])
    if _leaves(a) != _leaves(b):
        raise ValueError("Tree comparison requires the same taxa in both trees.")
    if data["mode"] not in ("rooted", "unrooted"):
        raise ValueError("Tree comparison mode must be rooted or unrooted.")
    rooted = data["mode"] == "rooted"
    a_clades, b_clades = _clades(a, rooted), _clades(b, rooted)
    shared = a_clades & b_clades
    rf = len(a_clades ^ b_clades)
    denominator = len(a_clades) + len(b_clades)
    return {
        "summary": (f"{data['mode'].capitalize()} Robinson–Foulds distance {rf} for "
                    f"{len(_leaves(a))} taxa; {len(shared)} shared informative "
                    + ("clades." if rooted else "splits. Root placement is ignored.")),
        "values": {"robinson_foulds": rf, "normalized_rf": rf / denominator if denominator else 0.0,
                   "shared": len(shared), "tree_a_features": len(a_clades),
                   "tree_b_features": len(b_clades), "taxa_count": len(_leaves(a)),
                   "comparison_mode": data["mode"]},
        "table": {"columns": ["Clade / canonical split", "Tree A", "Tree B"],
                  "rows": [[_split_name(s), int(s in a_clades), int(s in b_clades)]
                           for s in sorted(a_clades | b_clades,
                                           key=lambda s: (len(s), tuple(sorted(s))))]},
    }


def patristic_distances(data: dict) -> dict:
    root = _parse(data["tree"])
    if not _weighted(root):
        raise ValueError("Patristic distances require all non-root branch lengths.")
    graph, labels = _graph(root)
    tips = sorted((label, index) for index, label in labels.items() if label is not None)
    rows = []
    distances = []
    for i, (name, a) in enumerate(tips):
        for other_name, b in tips[i + 1:]:
            distance = math.fsum(_require_length(weight) for _, _, weight in _path(graph, a, b))
            rows.append([name, other_name, distance])
            distances.append(distance)
    return {
        "summary": f"Computed {len(rows)} tip-to-tip patristic distances across {len(tips)} taxa.",
        "values": {"taxa_count": len(tips), "pair_count": len(rows),
                   "mean_distance": math.fsum(distances) / len(distances),
                   "maximum_distance": max(distances),
                   "branch_length_unit": "Newick branch length units"},
        "table": {"columns": ["Taxon A", "Taxon B", "Patristic distance"], "rows": rows},
    }


def consensus_tree(data: dict) -> dict:
    trees = _forest(data["trees"])
    if data["method"] not in ("strict", "majority"):
        raise ValueError("Choose strict or majority rooted consensus.")
    n = len(trees)
    taxa = frozenset(_leaves(trees[0]))
    counts = Counter(clade for tree in trees for clade in _clades(tree, True))
    accepted = {clade for clade, count in counts.items()
                if count == n or (data["method"] == "majority" and 2 * count > n)}
    # Strict/majority (>50%) rooted clades are mutually compatible. Each
    # selected clade belongs to its smallest enclosing supported clade.
    groups = accepted | {taxa} | {frozenset((name,)) for name in taxa}
    descendants: dict[frozenset[str], list[frozenset[str]]] = {group: [] for group in groups}
    for group in groups - {taxa}:
        parent = min((other for other in groups if group < other), key=len)
        descendants[parent].append(group)

    def build(group: frozenset[str]) -> _Node:
        if len(group) == 1:
            return _Node(next(iter(group)))
        return _Node(children=[build(child) for child in sorted(
            descendants[group], key=lambda child: tuple(sorted(child)))])

    result = {
        "summary": (f"{data['method'].capitalize()} rooted consensus of {n} trees "
                    f"on {len(taxa)} taxa, retaining {len(accepted)} supported nontrivial clades. "
                    "Branch lengths are not inferred."),
        "values": {"newick": _newick(build(taxa)), "tree_count": n, "taxa_count": len(taxa),
                   "retained_clades": len(accepted), "method": data["method"],
                   "branch_lengths": "not inferred"},
    }
    if accepted:
        result["table"] = {
            "columns": ["Retained rooted clade", "Supporting trees", "Fraction"],
            "rows": [[_split_name(s), counts[s], counts[s] / n] for s in sorted(
                accepted, key=lambda s: (len(s), tuple(sorted(s))))],
        }
    return result


def parse_newick(data: dict) -> dict:
    root = _parse(data["tree"])
    depths = []
    internal = 0

    def walk(node: _Node, depth: float, known: bool) -> None:
        nonlocal internal
        if node.children:
            internal += 1
            for child in node.children:
                walk(child, depth + (child.length or 0), known and child.length is not None)
        else:
            depths.append((node.name, depth if known else None))

    walk(root, 0.0, True)
    complete = _weighted(root)
    return {
        "summary": (f"Parsed Newick with {len(depths)} distinct taxa and {internal} "
                    f"internal nodes; branch lengths are {'complete' if complete else 'partial or absent'}."),
        "values": {"newick": _newick(root), "taxa_count": len(depths),
                   "internal_nodes": internal, "branch_lengths_complete": "yes" if complete else "no",
                   "total_branch_length": (_total_length(root) if complete
                                           else "undefined (missing branch lengths)")},
        "table": {"columns": ["Taxon", "Root-to-tip distance"],
                  "rows": [[name, distance if distance is not None else "unspecified"]
                           for name, distance in sorted(depths)]},
    }


_DISTANCE_EXAMPLE = {
    "taxa": '["A", "B", "C", "D"]',
    "distances": [[0, 2, 6, 6], [2, 0, 6, 6], [6, 6, 0, 4], [6, 6, 4, 0]],
}
_TREE_EXAMPLE = "((A:1,B:1):2,(C:2,D:2):1);"
_NEWICK_CITATION = Citation("Newick tree format (PHYLIP)",
                           "https://evolution.genetics.washington.edu/phylip/newicktree.html")
_RF_CITATION = Citation("Robinson and Foulds (1981), comparison of phylogenetic trees",
                        "https://doi.org/10.1007/BF01734359")

OPERATIONS = (
    Operation(
        "upgma", "UPGMA distance tree", "Build a rooted ultrametric tree from a symmetric distance matrix.",
        (Field("taxa", "Taxon labels (JSON array)", "text"),
         Field("distances", "Symmetric distance matrix", "matrix", "1")),
        _DISTANCE_EXAMPLE, upgma,
        methods=("Average-linkage agglomeration weighted by cluster size; each merge height "
                 "is half the intercluster distance. Assumes approximately clocklike distances."),
        citations=(Citation("Sokal and Michener (1958), UPGMA",
                            "https://doi.org/10.2307/2412447"),),
    ),
    Operation(
        "neighbor_joining", "Neighbor-joining distance tree",
        "Build an unrooted additive-distance tree, represented as a Newick trifurcation.",
        (Field("taxa", "Taxon labels (JSON array)", "text"),
         Field("distances", "Symmetric distance matrix", "matrix", "1")),
        _DISTANCE_EXAMPLE, neighbor_joining,
        methods=("Saitou–Nei neighbor joining with minimum Q criterion and three-way final "
                 "join; fitted negative limbs are clipped to zero and reported."),
        citations=(Citation("Saitou and Nei (1987), neighbor-joining method",
                            "https://doi.org/10.1093/oxfordjournals.molbev.a040454"),),
    ),
    Operation(
        "parse_newick", "Parse Newick tree", "Validate and canonicalize a Newick tree with labeled tips.",
        (Field("tree", "Newick tree", "text"),), {"tree": _TREE_EXAMPLE}, parse_newick,
        methods=("Recursive Newick parser with single-quoted labels, escaped apostrophes, "
                 "bracket comments, polytomies and optional nonnegative branch lengths."),
        citations=(_NEWICK_CITATION,),
    ),
    Operation(
        "reroot_tree", "Reroot phylogenetic tree", "Reroot on an outgroup edge or at the weighted midpoint.",
        (Field("tree", "Newick tree", "text"),
         Field("method", "Rooting method", "choice", choices=("outgroup", "midpoint")),
         Field("outgroup", "Outgroup taxon (ignored for midpoint)", "text")),
        {"tree": _TREE_EXAMPLE, "method": "outgroup", "outgroup": "A"}, reroot_tree,
        methods=("Place root at midpoint of outgroup pendant edge or diameter of the "
                 "weighted tip-distance tree, contracting unary vertices without changing paths."),
        citations=(_NEWICK_CITATION,),
    ),
    Operation(
        "compare_trees", "Compare phylogenetic trees",
        "Compare rooted clades or unrooted splits using Robinson–Foulds distance.",
        (Field("tree_a", "First Newick tree", "text"),
         Field("tree_b", "Second Newick tree", "text"),
         Field("mode", "Comparison mode", "choice", choices=("unrooted", "rooted"))),
        {"tree_a": _TREE_EXAMPLE, "tree_b": "((A:1,C:2):2,(B:1,D:2):1);", "mode": "unrooted"},
        compare_trees,
        methods=("Symmetric difference of nontrivial bipartitions (unrooted) or "
                 "nontrivial clades (rooted); normalized by total feature count."),
        citations=(_RF_CITATION,),
    ),
    Operation(
        "patristic_distances", "Patristic tip distances",
        "Compute all pairwise path lengths from a fully length-annotated Newick tree.",
        (Field("tree", "Weighted Newick tree", "text"),), {"tree": _TREE_EXAMPLE},
        patristic_distances,
        methods="Sum the edge lengths along each unique leaf-to-leaf path; no molecular clock assumption.",
        citations=(_NEWICK_CITATION,),
    ),
    Operation(
        "consensus_tree", "Consensus phylogenetic tree",
        "Compute strict or majority-rule rooted consensus from multiple Newick trees.",
        (Field("trees", "Newick trees (semicolon-delimited)", "text"),
         Field("method", "Consensus method", "choice", choices=("majority", "strict"))),
        {"trees": "((A,B),(C,D));((A,B),(C,D));((A,C),(B,D));", "method": "majority"},
        consensus_tree,
        methods=("Retain clades present in every tree (strict) or in strictly more than "
                 "half the rooted input trees (majority); conflicting clades collapse to polytomies."),
        citations=(Citation("PHYLIP Consense documentation",
                            "https://evolution.genetics.washington.edu/phylip/doc/consense.html"),),
    ),
)
