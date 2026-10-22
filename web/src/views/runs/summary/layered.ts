// A layered layout (Sugiyama's method) of a directed graph, left to right,
// with orthogonal edges:
//
// 1. An edge against the boxes' order is reversed, so the rest is acyclic
//    and the order is a topological one.
// 2. Each box gets a rank, as near the boxes it joins as the edges allow;
//    ranks are every other layer, and the layers between hold the labels.
// 3. An edge across more than one layer gets a dummy in each layer it
//    crosses, its label's dummy in the layer next to the box it leaves.
// 4. Sweeps by barycentre order each layer, keeping the order with the
//    fewest crossings.
// 5. Every box is as tall as the ports of the busiest side of any box need.
//    Heights by Brandes and Köpf: blocks of elements aligned with their
//    median neighbours, packed, in four directions balanced. Then each port
//    on a box's side as level with the other end of its edge as the box
//    allows, and a link's dummies level with a port where there is room.
// 6. Between two layers each edge that changes height turns in a track of
//    its own, the tracks of edges that overlap ordered so that they cross
//    only where their order across the layers says they must.
//
// Since every edge runs through a layer only in its own dummy, and turns
// only between layers, no edge runs through a box or a label, and no two
// labels or boxes overlap.

export interface Box {
  key: string;
  width: number;
  /** The least height: more ports on a side than it holds make it taller. */
  height: number;
  /** Room kept clear above the box, for a loop over it. */
  above: number;
}

export interface Link {
  key: string;
  from: string;
  to: string;
  label: { width: number; height: number };
}

interface Placed {
  x: number;
  y: number;
  height: number;
}

interface Routed {
  /** The corners, from the box it leaves to the one it reaches. */
  points: [number, number][];
  /** Where it leaves its box and where it reaches the other. */
  ports: [[number, number], [number, number]];
  /** Whether it runs right to left. */
  back: boolean;
  /** The label's box. */
  label: { x: number; y: number; width: number; height: number };
}

export interface Drawing {
  width: number;
  height: number;
  boxes: Map<string, Placed>;
  links: Map<string, Routed>;
}

/** The margin round the drawing. */
const MARGIN = 24;
/** How far apart the ports on one side of a box are, and from its corners. */
const PORT = 12;
const PORT_END = 10;
/** The gaps between two boxes, a box and an edge, and two edges. */
const GAP_BOXES = 36;
const GAP_BOX = 18;
const GAP_EDGES = 12;
/** How far a label sits above its edge. */
const LIFT = 4;
/** How far apart the tracks between two layers are, and from the layers. */
const TRACK = 10;
const TRACK_END = 14;
/** The least width of a layer and of the gap between two. */
const MIN_LAYER = 8;
const MIN_CHANNEL = 28;
const SWEEPS = 24;
/** How far an arrow's end stops short of the box it reaches. */
const ARROW = 3;

/** A link as an edge of the acyclic graph, from box u to box v. */
interface Arc {
  u: number;
  v: number;
  back: boolean;
  label: { width: number; height: number };
}

interface Element {
  layer: number;
  /** The box; null for a dummy. */
  box: Box | null;
  /** A dummy's label; null for none. */
  label: { width: number; height: number } | null;
  width: number;
  up: number;
  down: number;
  y: number;
  pos: number;
}

/** A piece of an edge between two adjacent layers. */
interface Segment {
  link: number;
  a: Element;
  b: Element;
  /** The offsets of its ports from a's and b's middles. */
  pa: number;
  pb: number;
}

interface Graph {
  layers: Element[][];
  elements: Element[];
  /** Each box's element, in the boxes' order. */
  nodes: Element[];
  /** Each arc's elements, from u to v. */
  chains: Element[][];
  segments: Segment[];
}

/** The segments on one side of a box, in the order of their ports. */
interface Side {
  el: Element;
  list: Segment[];
  right: boolean;
}

export function layered(boxes: Box[], links: Link[]): Drawing {
  const arcs = acyclic(boxes, links);
  const graph = build(boxes, arcs, rank(boxes.length, arcs));
  order(graph);
  const sides = portSides(graph);
  for (const [el, y] of heights(graph, arcs)) el.y = y;
  level(graph, sides);
  return route(links, arcs, graph);
}

/** 1. Each link as an arc, reversed when it goes against the boxes' order. */
function acyclic(boxes: Box[], links: Link[]): Arc[] {
  const index = new Map(boxes.map((b, i) => [b.key, i]));
  const at = (key: string) => {
    const i = index.get(key);
    if (i === undefined) throw new Error(`${key} is not a box`);
    return i;
  };
  return links.map((l) => {
    const f = at(l.from);
    const t = at(l.to);
    if (f === t) throw new Error(`${l.key} loops`);
    return f < t
      ? { u: f, v: t, back: false, label: l.label }
      : { u: t, v: f, back: true, label: l.label };
  });
}

/**
 * 2. Each box's rank, from 0: the longest path, then each box moved, while
 * that shortens the arcs, as far towards its side with more arcs as they
 * allow.
 */
function rank(count: number, arcs: Arc[]): number[] {
  const rank = Array.from({ length: count }, () => 0);
  for (const e of [...arcs].sort((p, q) => p.v - q.v))
    rank[e.v] = Math.max(rank[e.v] ?? 0, (rank[e.u] ?? 0) + 1);
  for (let moved = true, n = 0; moved && n < count * 4; n++) {
    moved = false;
    rank.forEach((was, i) => {
      const ins = arcs.filter((e) => e.v === i).map((e) => rank[e.u] ?? 0);
      const outs = arcs.filter((e) => e.u === i).map((e) => rank[e.v] ?? 0);
      const r =
        ins.length > outs.length
          ? Math.max(...ins) + 1
          : outs.length > ins.length
            ? Math.min(...outs) - 1
            : was;
      if (r !== was) {
        rank[i] = r;
        moved = true;
      }
    });
  }
  const low = Math.min(0, ...rank);
  return rank.map((r) => r - low);
}

/** 3. The elements of each layer, and the segments between them. */
function build(boxes: Box[], arcs: Arc[], ranks: number[]): Graph {
  const elements: Element[] = [];
  const make = (
    layer: number,
    box: Box | null,
    label: Element["label"],
  ): Element => {
    const el: Element = {
      layer,
      box,
      label,
      width: box ? box.width : (label?.width ?? 0),
      up: box ? box.height / 2 + box.above : label ? label.height + LIFT : 0,
      down: box ? box.height / 2 : 0,
      y: 0,
      pos: 0,
    };
    elements.push(el);
    return el;
  };
  const nodes = boxes.map((b, i) => make(2 * (ranks[i] ?? 0), b, null));
  const chains = arcs.map((e) => {
    const from = must(nodes[e.u]);
    const to = must(nodes[e.v]);
    // The label sits next to the box the link leaves.
    const labelAt = e.back ? to.layer - 1 : from.layer + 1;
    const chain = [from];
    for (let l = from.layer + 1; l < to.layer; l++)
      chain.push(make(l, null, l === labelAt ? e.label : null));
    chain.push(to);
    return chain;
  });
  const segments = chains.flatMap((chain, link) =>
    chain
      .slice(1)
      .map((b, i): Segment => ({ link, a: must(chain[i]), b, pa: 0, pb: 0 })),
  );
  const layers: Element[][] = Array.from(
    { length: 2 * Math.max(0, ...ranks) + 1 },
    () => [],
  );
  for (const el of elements) layers[el.layer]?.push(el);
  return { layers, elements, nodes, chains, segments };
}

/** 4. The order in each layer, by barycentre sweeps. */
function order({ layers, segments }: Graph) {
  const count = layers.length;
  const crossings = () => {
    let n = 0;
    for (let l = 0; l + 1 < count; l++) {
      const between = segments.filter((s) => s.a.layer === l);
      between.forEach((s, i) => {
        for (const t of between.slice(i + 1))
          if ((s.a.pos - t.a.pos) * (s.b.pos - t.b.pos) < 0) n++;
      });
    }
    return n;
  };
  const number = () => {
    for (const layer of layers) layer.forEach((el, i) => (el.pos = i));
  };
  const sortBy = (layer: Element[], down: boolean) => {
    const centre = new Map(
      layer.map((el) => {
        const near = segments
          .filter((s) => (down ? s.b === el : s.a === el))
          .map((s) => (down ? s.a.pos : s.b.pos));
        const mean =
          near.length > 0
            ? near.reduce((x, y) => x + y, 0) / near.length
            : el.pos;
        return [el, mean];
      }),
    );
    layer.sort((p, q) =>
      then((centre.get(p) ?? 0) - (centre.get(q) ?? 0), p.pos - q.pos),
    );
  };
  number();
  for (let l = 1; l < count; l++) {
    sortBy(must(layers[l]), true);
    number();
  }
  let best = layers.map((layer) => [...layer]);
  let fewest = crossings();
  for (let sweep = 0; sweep < SWEEPS && fewest > 0; sweep++) {
    const down = sweep % 2 === 0;
    for (let k = 1; k < count; k++) {
      sortBy(must(layers[down ? k : count - 1 - k]), down);
      number();
    }
    const n = crossings();
    if (n < fewest) {
      fewest = n;
      best = layers.map((layer) => [...layer]);
    }
  }
  best.forEach((layer, l) => (layers[l] = layer));
  number();
}

/**
 * 5. Each box's sides, the segments on each in the order of the elements at
 * their other ends, the links between the same two boxes by their order;
 * and every box as tall as the busiest side of any box needs.
 */
function portSides({ nodes, segments }: Graph): Side[] {
  const sorted = (list: Segment[], other: (s: Segment) => Element) =>
    [...list].sort((p, q) =>
      then(other(p).pos - other(q).pos, p.link - q.link),
    );
  const sides = nodes.flatMap((el): Side[] => [
    {
      el,
      list: sorted(
        segments.filter((s) => s.b === el),
        (s) => s.a,
      ),
      right: false,
    },
    {
      el,
      list: sorted(
        segments.filter((s) => s.a === el),
        (s) => s.b,
      ),
      right: true,
    },
  ]);
  const height = Math.max(
    ...nodes.map((el) => must(el.box).height),
    ...sides.map((s) => (s.list.length - 1) * PORT + 2 * PORT_END),
  );
  for (const el of nodes) {
    el.up = height / 2 + must(el.box).above;
    el.down = height / 2;
  }
  return sides;
}

/** The gap kept between two elements next to each other in a layer. */
const gap = (a: Element, b: Element) =>
  a.box && b.box ? GAP_BOXES : a.box || b.box ? GAP_BOX : GAP_EDGES;

/**
 * 5. Each element's height, at its middle: Brandes and Köpf's four
 * alignments, each shifted to the narrowest one's top or bottom by its
 * side, and the mean of the middle two; the top of all at MARGIN.
 */
function heights(graph: Graph, arcs: Arc[]): Map<Element, number> {
  const { elements } = graph;
  const reversed = arcs.map((e) => e.back);
  const runs = [true, false].flatMap((ahead) =>
    [true, false].map((downward) => align(graph, reversed, ahead, downward)),
  );
  const extents = runs.map((ys) => ({
    top: Math.min(...elements.map((el) => must(ys.get(el)) - el.up)),
    bottom: Math.max(...elements.map((el) => must(ys.get(el)) + el.down)),
  }));
  const narrowest = must(
    extents.reduce((m, e) => (e.bottom - e.top < m.bottom - m.top ? e : m)),
  );
  runs.forEach((ys, i) => {
    const e = must(extents[i]);
    const by =
      i % 2 === 0 ? narrowest.top - e.top : narrowest.bottom - e.bottom;
    for (const el of elements) ys.set(el, must(ys.get(el)) + by);
  });
  const balanced = new Map(
    elements.map((el) => {
      const [, b, c] = runs.map((ys) => must(ys.get(el))).sort((p, q) => p - q);
      return [el, (must(b) + must(c)) / 2];
    }),
  );
  const top = Math.min(...elements.map((el) => must(balanced.get(el)) - el.up));
  for (const [el, y] of balanced)
    balanced.set(el, Math.round(y - top + MARGIN));
  return balanced;
}

/**
 * One of Brandes and Köpf's alignments: going through the layers ahead
 * (left to right) or back, each element is aligned with the median of its
 * neighbours in the layer before, where that keeps the alignments of the
 * layer in order and crosses no edge between two dummies; then each block
 * of aligned elements is put as near the top (downward) or the bottom as
 * the elements before it in their layers allow. Returns each element's
 * height, at its middle.
 */
function align(
  { layers, segments }: Graph,
  reversed: boolean[],
  ahead: boolean,
  downward: boolean,
): Map<Element, number> {
  const order = (ahead ? layers : [...layers].reverse()).map((layer) =>
    downward ? layer : [...layer].reverse(),
  );
  const pos = new Map<Element, number>();
  for (const layer of order) layer.forEach((el, i) => pos.set(el, i));
  const at = (el: Element) => must(pos.get(el));
  // Each element's edges to the layer before, by where their other ends are.
  const before = (el: Element) =>
    segments
      .filter((s) => (ahead ? s.b === el : s.a === el))
      .map((s) => ({ s, other: ahead ? s.a : s.b }))
      .sort((p, q) => then(at(p.other) - at(q.other), p.s.link - q.s.link));
  // An edge that crosses one between two dummies is not aligned on.
  const inner = (s: Segment) => s.a.box === null && s.b.box === null;
  const crossed = new Set(
    segments.filter(
      (s) =>
        !inner(s) &&
        segments.some(
          (t) =>
            inner(t) &&
            t.a.layer === s.a.layer &&
            (at(s.a) - at(t.a)) * (at(s.b) - at(t.b)) < 0,
        ),
    ),
  );

  const root = new Map<Element, Element>();
  for (const layer of order) for (const el of layer) root.set(el, el);
  for (const layer of order.slice(1)) {
    let r = -1;
    for (const v of layer) {
      // An edge the right way round wins over one reversed, so that the way
      // through the machine runs straight.
      const all = before(v);
      const forward = all.filter((n) => !must(reversed[n.s.link]));
      const near = forward.length > 0 ? forward : all;
      const d = near.length;
      for (const m of new Set([
        Math.floor((d - 1) / 2),
        Math.ceil((d - 1) / 2),
      ])) {
        const n = near[m];
        if (root.get(v) !== v || !n) break;
        if (crossed.has(n.s) || at(n.other) <= r) continue;
        root.set(v, must(root.get(n.other)));
        r = at(n.other);
      }
    }
  }

  // Compaction: each block below the blocks of the elements above its own.
  const below = new Map<Element, { block: Element; by: number }[]>();
  const count = new Map<Element, number>();
  for (const layer of order)
    layer.forEach((w, i) => {
      const u = layer[i - 1];
      const bw = must(root.get(w));
      if (!count.has(bw)) count.set(bw, 0);
      if (!u) return;
      const bu = must(root.get(u));
      const [down, up] = downward ? [u.down, w.up] : [u.up, w.down];
      const list = below.get(bu) ?? [];
      list.push({ block: bw, by: down + gap(u, w) + up });
      below.set(bu, list);
      count.set(bw, (count.get(bw) ?? 0) + 1);
    });
  const y = new Map<Element, number>();
  const ready = [...count].filter(([, n]) => n === 0).map(([b]) => b);
  for (const b of ready) y.set(b, 0);
  while (ready.length > 0) {
    const b = must(ready.pop());
    for (const next of below.get(b) ?? []) {
      y.set(
        next.block,
        Math.max(y.get(next.block) ?? -Infinity, must(y.get(b)) + next.by),
      );
      const left = must(count.get(next.block)) - 1;
      count.set(next.block, left);
      if (left === 0) ready.push(next.block);
    }
  }
  const sign = downward ? 1 : -1;
  const ys = new Map<Element, number>();
  for (const [el, b] of root) ys.set(el, sign * must(y.get(b)));
  return ys;
}

/**
 * 5. Each port as level with the other end of its edge as the box and the
 * ports' order and gaps allow, twice for the edges between two boxes; then
 * a link's dummies level with one of its ports, where that keeps the gaps
 * in their layers and turns the link fewer times.
 */
function level({ layers, chains, segments }: Graph, sides: Side[]) {
  const ports = () => {
    for (let n = 0; n < 2; n++)
      for (const { el, list, right } of sides) {
        const wants = list.map((s) =>
          right ? s.b.y + s.pb - el.y : s.a.y + s.pa - el.y,
        );
        const room = el.down - PORT_END;
        const at = spread(wants, PORT, -room, room);
        list.forEach((s, i) => {
          if (right) s.pa = must(at[i]);
          else s.pb = must(at[i]);
        });
      }
  };
  ports();

  const turns = (link: number) =>
    segments.filter((s) => s.link === link && s.a.y + s.pa !== s.b.y + s.pb)
      .length;
  const fits = (el: Element, y: number) => {
    const layer = must(layers[el.layer]);
    const above = layer[el.pos - 1];
    const under = layer[el.pos + 1];
    return (
      (!above || above.y + above.down + gap(above, el) + el.up <= y) &&
      (!under || y + el.down + gap(el, under) + under.up <= under.y)
    );
  };
  for (let n = 0; n < 3; n++) {
    chains.forEach((chain, li) => {
      const dummies = chain.slice(1, -1);
      const mine = segments.filter((s) => s.link === li);
      const first = must(mine[0]);
      const last = must(mine.at(-1));
      for (const y of [first.a.y + first.pa, last.b.y + last.pb]) {
        if (dummies.length === 0 || !dummies.every((d) => fits(d, y))) continue;
        const before = dummies.map((d) => d.y);
        const was = turns(li);
        for (const d of dummies) d.y = y;
        if (turns(li) < was) break;
        dummies.forEach((d, i) => (d.y = must(before[i])));
      }
    });
    ports();
  }
}

/**
 * 6. Each layer's width, the tracks between each two layers, and each
 * box's place and link's corners, ports and label.
 */
function route(links: Link[], arcs: Arc[], graph: Graph): Drawing {
  const { layers, elements, nodes, chains, segments } = graph;
  const widths = layers.map((layer) =>
    Math.max(MIN_LAYER, ...layer.map((el) => el.width)),
  );
  const tracks = new Map<Segment, number>();
  const channels = widths.slice(1).map((_, l) =>
    channel(
      segments.filter((s) => s.a.layer === l),
      tracks,
    ),
  );
  const xs: number[] = [];
  let x = MARGIN;
  widths.forEach((w, l) => {
    xs.push(x);
    x += w + (channels[l] ?? 0);
  });

  const boxes = new Map<string, Placed>();
  for (const el of nodes) {
    const box = must(el.box);
    boxes.set(box.key, {
      x: Math.round(
        must(xs[el.layer]) + (must(widths[el.layer]) - box.width) / 2,
      ),
      y: el.y - el.down,
      height: el.up - box.above + el.down,
    });
  }
  const sideOf = (el: Element, right: boolean) => {
    if (!el.box)
      return must(xs[el.layer]) + (right ? must(widths[el.layer]) : 0);
    const p = must(boxes.get(el.box.key));
    return right ? p.x + el.box.width : p.x;
  };

  const routed = new Map<string, Routed>();
  links.forEach((link, li) => {
    const arc = must(arcs[li]);
    const mine = segments.filter((s) => s.link === li);
    const first = must(mine[0]);
    const points: [number, number][] = [
      [sideOf(first.a, true), first.a.y + first.pa],
    ];
    for (const s of mine) {
      const t = tracks.get(s);
      if (t === undefined) continue;
      const l = s.a.layer;
      const tx = must(xs[l]) + must(widths[l]) + TRACK_END + t * TRACK;
      points.push([tx, s.a.y + s.pa], [tx, s.b.y + s.pb]);
    }
    const last = must(mine.at(-1));
    points.push([sideOf(last.b, false), last.b.y + last.pb]);
    if (arc.back) points.reverse();
    const start = must(points[0]);
    const end = must(points.at(-1));
    const ports: Routed["ports"] = [[...start], [...end]];
    // Room for the arrow at the end.
    end[0] += arc.back ? ARROW : -ARROW;

    const dummy = must(must(chains[li]).find((el) => el.label !== null));
    const label = must(dummy.label);
    const lx = must(xs[dummy.layer]);
    const lw = must(widths[dummy.layer]);
    routed.set(link.key, {
      points,
      ports,
      back: arc.back,
      label: {
        x: arc.back ? lx + lw - label.width : lx,
        y: dummy.y - LIFT - label.height,
        width: label.width,
        height: label.height,
      },
    });
  });

  return {
    width: x + MARGIN,
    height: Math.max(...elements.map((el) => el.y + el.down)) + MARGIN,
    boxes,
    links: routed,
  };
}

/**
 * Puts each segment between two layers that changes height in a track, and
 * returns the width the tracks need. Going up, the upper turns first; going
 * down, the lower. And where one starts level with where another ends, it
 * turns first, so that the two do not run along each other.
 */
function channel(all: Segment[], tracks: Map<Segment, number>): number {
  const ya = (s: Segment) => s.a.y + s.pa;
  const yb = (s: Segment) => s.b.y + s.pb;
  const between = all.filter((s) => ya(s) !== yb(s));
  const span = (s: Segment) =>
    [Math.min(ya(s), yb(s)), Math.max(ya(s), yb(s))] as const;
  const ups = between
    .filter((s) => yb(s) < ya(s))
    .sort((p, q) => ya(p) - ya(q));
  const downs = between
    .filter((s) => yb(s) > ya(s))
    .sort((p, q) => ya(q) - ya(p));
  const overlap = (
    p: readonly [number, number],
    q: readonly [number, number],
  ) => p[0] <= q[1] + GAP_EDGES && q[0] <= p[1] + GAP_EDGES;
  const before = (o: Segment, s: Segment) => {
    const group = ups.includes(o) ? ups : downs;
    return (
      (group.includes(s) &&
        group.indexOf(o) < group.indexOf(s) &&
        overlap(span(o), span(s))) ||
      ya(o) === yb(s)
    );
  };
  const used: (readonly [number, number])[][] = [];
  const waiting = [...ups, ...downs];
  while (waiting.length > 0) {
    const i = waiting.findIndex((s) => !waiting.some((o) => before(o, s)));
    const [s] = waiting.splice(Math.max(0, i), 1);
    if (!s) break;
    const mine = span(s);
    let t =
      Math.max(
        -1,
        ...between
          .filter((o) => tracks.has(o) && before(o, s))
          .map((o) => tracks.get(o) ?? 0),
      ) + 1;
    while (used[t]?.some((o) => overlap(o, mine)) === true) t++;
    (used[t] ??= []).push(mine);
    tracks.set(s, t);
  }
  return used.length === 0
    ? MIN_CHANNEL
    : Math.max(MIN_CHANNEL, 2 * TRACK_END + (used.length - 1) * TRACK);
}

/**
 * Heights as near those wanted as their order, a gap of at least step
 * between each two, and the bounds allow, the squares of the distances
 * least: less each one's share of the gaps, this is isotonic regression,
 * solved by pooling adjacent violators and then clamped to the bounds.
 */
function spread(
  wants: number[],
  step: number,
  low: number,
  high: number,
): number[] {
  const blocks: { sum: number; count: number }[] = [];
  wants.forEach((w, i) => {
    blocks.push({ sum: w - i * step, count: 1 });
    for (;;) {
      const b = blocks.at(-1);
      const a = blocks.at(-2);
      if (!a || !b || a.sum / a.count <= b.sum / b.count) break;
      blocks.splice(-2, 2, { sum: a.sum + b.sum, count: a.count + b.count });
    }
  });
  const top = high - (wants.length - 1) * step;
  const out: number[] = [];
  for (const b of blocks) {
    const z = Math.round(Math.min(top, Math.max(low, b.sum / b.count)));
    for (let k = 0; k < b.count; k++) out.push(z + out.length * step);
  }
  return out;
}

/** An order by first, then by next where first ties. */
function then(first: number, next: number): number {
  return first !== 0 ? first : next;
}

function must<T>(value: T | null | undefined): T {
  if (value === undefined || value === null) throw new Error("not laid out");
  return value;
}
