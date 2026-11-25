/** Agglomerative average-linkage clustering for heatmap rows/columns.
 * Euclidean distances use a common scale to keep huge/small finite values
 * representable. Ties are broken by original indices. No observations,
 * labels or stored matrix cells are reordered.
 */
export interface ClusterNode {
  id: number;
  minLeaf: number;
  size: number;
  height: number;
  depth: number;
  leaf?: number;
  left?: ClusterNode;
  right?: ClusterNode;
}
export interface AxisClusters { order: number[]; tree: ClusterNode | null }
export interface HeatmapClusters {
  rowOrder: number[];
  columnOrder: number[];
  rowTree: ClusterNode | null;
  columnTree: ClusterNode | null;
}

type Entry = [distance: number, minA: number, minB: number, a: number, b: number];

function less(a: Entry, b: Entry) {
  for (let i = 0; i < 5; i++) {
    if (a[i] < b[i]) return true;
    if (a[i] > b[i]) return false;
  }
  return false;
}

class PairHeap {
  private items: Entry[] = [];
  push(value: Entry) {
    const data = this.items;
    let i = data.length;
    data.push(value);
    while (i > 0) {
      const parent = Math.floor((i - 1) / 2);
      if (!less(value, data[parent])) break;
      data[i] = data[parent];
      i = parent;
    }
    data[i] = value;
  }
  pop(): Entry | undefined {
    const data = this.items;
    if (!data.length) return undefined;
    const best = data[0];
    const last = data.pop()!;
    if (!data.length) return best;
    let i = 0;
    while (2 * i + 1 < data.length) {
      let child = 2 * i + 1;
      if (child + 1 < data.length && less(data[child + 1], data[child])) child++;
      if (!less(data[child], last)) break;
      data[i] = data[child];
      i = child;
    }
    data[i] = last;
    return best;
  }
}

function pairKey(a: number, b: number) {
  return a < b ? a + ":" + b : b + ":" + a;
}

export function clusterAxis(vectors: number[][]): AxisClusters {
  const n = vectors.length;
  if (n === 0) return { order: [], tree: null };
  if (n === 1) return { order: [0], tree: {
    id: 0, minLeaf: 0, leaf: 0, height: 0, size: 1, depth: 0,
  } };
  const scale = Math.max(...vectors.flat().map(Math.abs)) || 1;
  const normalized = vectors.map(row => row.map(value => value / scale));
  const heap = new PairHeap();
  const distances = new Map<string, number>();
  const active = new Map<number, ClusterNode>();
  for (let i = 0; i < n; i++) {
    active.set(i, { id: i, minLeaf: i, size: 1, height: 0, depth: 0, leaf: i });
  }
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const distance = Math.sqrt(normalized[i].reduce((total, value, k) =>
        total + (value - normalized[j][k]) ** 2, 0));
      distances.set(pairKey(i, j), distance);
      heap.push([distance, i, j, i, j]);
    }
  }
  let nextId = n;
  while (active.size > 1) {
    let pair: Entry | undefined;
    do { pair = heap.pop(); } while (pair && (!active.has(pair[3]) || !active.has(pair[4])));
    if (!pair) throw new Error("Cluster heap exhausted");
    const a = active.get(pair[3])!;
    const b = active.get(pair[4])!;
    const [left, right] = a.minLeaf < b.minLeaf ? [a, b] : [b, a];
    const merged: ClusterNode = {
      id: nextId, minLeaf: Math.min(a.minLeaf, b.minLeaf),
      size: a.size + b.size, height: pair[0], depth: Math.max(a.depth, b.depth) + 1,
      left, right,
    };
    active.delete(a.id);
    active.delete(b.id);
    for (const other of active.values()) {
      const distance = (a.size * distances.get(pairKey(a.id, other.id))!
        + b.size * distances.get(pairKey(b.id, other.id))!) / merged.size;
      distances.set(pairKey(nextId, other.id), distance);
      const mins = [merged.minLeaf, other.minLeaf].sort((x, y) => x - y);
      heap.push([distance, mins[0], mins[1], nextId, other.id]);
    }
    active.set(nextId++, merged);
  }
  const tree = active.values().next().value as ClusterNode;
  const visit = (node: ClusterNode): number[] => node.leaf !== undefined
    ? [node.leaf] : [...visit(node.left!), ...visit(node.right!)];
  return { order: visit(tree), tree };
}

export function clusterHeatmap(matrix: number[][], mode?: "rows" | "columns" | "both" | null): HeatmapClusters {
  const rows = matrix.length;
  const cols = matrix[0]?.length ?? 0;
  const row = mode === "rows" || mode === "both" ? clusterAxis(matrix) : null;
  const col = mode === "columns" || mode === "both"
    ? clusterAxis(Array.from({ length: cols }, (_, j) => matrix.map(values => values[j])))
    : null;
  return {
    rowOrder: row?.order ?? Array.from({ length: rows }, (_, i) => i),
    columnOrder: col?.order ?? Array.from({ length: cols }, (_, i) => i),
    rowTree: row?.tree ?? null,
    columnTree: col?.tree ?? null,
  };
}

export function dendrogramPaths(tree: ClusterNode | null, order: number[],
  orientation: "rows" | "columns", cellSize: number, depth = 34): string[] {
  if (!tree || !tree.left || !tree.right) return [];
  const positions = new Map(order.map((index, ordinal) => [index, (ordinal + 0.5) * cellSize]));
  const maxHeight = tree.height;
  const lines: string[] = [];
  function visit(node: ClusterNode): [number, number] {
    if (node.leaf !== undefined) {
      const position = positions.get(node.leaf)!;
      return orientation === "rows" ? [0, position] : [position, 0];
    }
    const a = visit(node.left!);
    const b = visit(node.right!);
    const ratio = maxHeight > 0 ? node.height / maxHeight : node.depth / tree!.depth;
    if (orientation === "rows") {
      const x = -(4 + depth * ratio);
      const y = (a[1] + b[1]) / 2;
      lines.push(`M ${a[0]} ${a[1]} H ${x} V ${b[1]} H ${b[0]}`);
      return [x, y];
    }
    const y = -(4 + depth * ratio);
    const x = (a[0] + b[0]) / 2;
    lines.push(`M ${a[0]} ${a[1]} V ${y} H ${b[0]} V ${b[1]}`);
    return [x, y];
  }
  visit(tree);
  return lines;
}
