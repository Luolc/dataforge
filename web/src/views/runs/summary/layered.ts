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
// 5. Heights by Brandes and Köpf: blocks of elements aligned with their
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

export function layered(boxes: Box[], links: Link[]): Drawing {
  const index = new Map(boxes.map((b, i) => [b.key, i]));
  const at = (key: string) => {
    const i = index.get(key);
    if (i === undefined) throw new Error(`${key} is not a box`);
    return i;
  };

  // 1. Each link as an edge of the acyclic graph, from u to v.
  const edges = links.map((l) => {
    const f = at(l.from);
    const t = at(l.to);
    if (f === t) throw new Error(`${l.key} loops`);
    return f < t
      ? { u: f, v: t, back: false, label: l.label }
      : { u: t, v: f, back: true, label: l.label };
  });

  // 2. Ranks: the longest path, then each box moved, while that shortens
  // the edges, as far towards the side with more edges as they allow.
  const rank = boxes.map(() => 0);
  for (const e of [...edges].sort((p, q) => p.v - q.v))
    rank[e.v] = Math.max(rank[e.v] ?? 0, (rank[e.u] ?? 0) + 1);
  for (let moved = true, n = 0; moved && n < boxes.length * 4; n++) {
    moved = false;
    boxes.forEach((_, i) => {
      const ins = edges.filter((e) => e.v === i).map((e) => rank[e.u] ?? 0);
      const outs = edges.filter((e) => e.u === i).map((e) => rank[e.v] ?? 0);
      const r =
        ins.length > outs.length
          ? Math.max(...ins) + 1
          : outs.length > ins.length
            ? Math.min(...outs) - 1
            : (rank[i] ?? 0);
      if (r !== rank[i]) {
        rank[i] = r;
        moved = true;
      }
    });
  }
  const low = Math.min(0, ...rank);
  const ranks = rank.map((r) => r - low);
  const layerCount = 2 * Math.max(0, ...ranks) + 1;

  // 3. The elements of each layer, and the segments between them.
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
  const chains = edges.map((e) => {
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
  const segments: Segment[] = chains.flatMap((chain, li) =>
    chain.slice(1).map((b, i) => ({
      link: li,
      a: must(chain[i]),
      b,
      pa: 0,
      pb: 0,
    })),
  );
  const layers: Element[][] = Array.from({ length: layerCount }, () => []);
  for (const el of elements) layers[el.layer]?.push(el);

  // 4. The order in each layer.
  const left = (el: Element) => segments.filter((s) => s.b === el);
  const right = (el: Element) => segments.filter((s) => s.a === el);
  const crossings = () => {
    let n = 0;
    for (let l = 0; l + 1 < layerCount; l++) {
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
        const near = (down ? left(el) : right(el)).map((s) =>
          down ? s.a.pos : s.b.pos,
        );
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
  for (let l = 1; l < layerCount; l++) {
    sortBy(must(layers[l]), true);
    number();
  }
  let best = layers.map((layer) => [...layer]);
  let fewest = crossings();
  for (let sweep = 0; sweep < SWEEPS && fewest > 0; sweep++) {
    const down = sweep % 2 === 0;
    for (let k = 1; k < layerCount; k++) {
      const l = down ? k : layerCount - 1 - k;
      sortBy(must(layers[l]), down);
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

  // Ports: those on one side of a box in the order of the elements at their
  // other ends, the links between the same two boxes by their order. A box
  // is tall enough for the ports of its busier side.
  const sorted = (list: Segment[], other: (s: Segment) => Element) =>
    [...list].sort((p, q) =>
      then(other(p).pos - other(q).pos, p.link - q.link),
    );
  const sides = nodes.flatMap((el) => {
    const box = must(el.box);
    const ins = sorted(left(el), (s) => s.a);
    const outs = sorted(right(el), (s) => s.b);
    const most = Math.max(ins.length, outs.length);
    const height = Math.max(box.height, (most - 1) * PORT + 2 * PORT_END);
    el.up = height / 2 + box.above;
    el.down = height / 2;
    return [
      { el, list: ins, right: false },
      { el, list: outs, right: true },
    ];
  });

  // 5. Heights: Brandes and Köpf's four alignments, balanced.
  const gap = (a: Element, b: Element) =>
    a.box && b.box ? GAP_BOXES : a.box || b.box ? GAP_BOX : GAP_EDGES;
  const runs = [true, false].flatMap((ahead) =>
    [true, false].map((downward) =>
      align(
        layers,
        segments,
        edges.map((e) => e.back),
        ahead,
        downward,
        gap,
      ),
    ),
  );
  // Each run shifted to the narrowest one's top or bottom, by its side.
  const extent = (ys: Map<Element, number>) => {
    const tops = elements.map((el) => must(ys.get(el)) - el.up);
    const bottoms = elements.map((el) => must(ys.get(el)) + el.down);
    return { top: Math.min(...tops), bottom: Math.max(...bottoms) };
  };
  const extents = runs.map(extent);
  const narrowest = must(
    extents.reduce((m, e) => (e.bottom - e.top < m.bottom - m.top ? e : m)),
  );
  runs.forEach((ys, i) => {
    const e = must(extents[i]);
    const by =
      i % 2 === 0 ? narrowest.top - e.top : narrowest.bottom - e.bottom;
    for (const el of elements) ys.set(el, must(ys.get(el)) + by);
  });
  for (const el of elements) {
    const [, b, c] = runs.map((ys) => must(ys.get(el))).sort((p, q) => p - q);
    el.y = (must(b) + must(c)) / 2;
  }
  const top = Math.min(...elements.map((el) => el.y - el.up));
  for (const el of elements) el.y = Math.round(el.y - top + MARGIN);

  // Each port as level with the other end of its edge as the box and the
  // ports' order and gaps allow; twice, for the edges between two boxes.
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

  // Then a link's dummies level with one of its ports, where that keeps
  // the gaps in their layers and turns the link fewer times.
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

  // 6. Widths, and the tracks between each two layers.
  const widths = layers.map((layer) =>
    Math.max(MIN_LAYER, ...layer.map((el) => el.width)),
  );
  const yOf = (el: Element, port: number) => el.y + port;
  const tracks = new Map<Segment, number>();
  const channels = widths.slice(1).map((_, l) => {
    const between = segments.filter(
      (s) => s.a.layer === l && yOf(s.a, s.pa) !== yOf(s.b, s.pb),
    );
    const span = (s: Segment) => {
      const ya = yOf(s.a, s.pa);
      const yb = yOf(s.b, s.pb);
      return [Math.min(ya, yb), Math.max(ya, yb)] as const;
    };
    // Going up, the upper turns first; going down, the lower. And where
    // one starts level with where another ends, it turns first, so that
    // the two do not run along each other.
    const ups = between
      .filter((s) => yOf(s.b, s.pb) < yOf(s.a, s.pa))
      .sort((p, q) => yOf(p.a, p.pa) - yOf(q.a, q.pa));
    const downs = between
      .filter((s) => yOf(s.b, s.pb) > yOf(s.a, s.pa))
      .sort((p, q) => yOf(q.a, q.pa) - yOf(p.a, p.pa));
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
        yOf(o.a, o.pa) === yOf(s.b, s.pb)
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
  });
  const xs: number[] = [];
  let x = MARGIN;
  widths.forEach((w, l) => {
    xs.push(x);
    x += w + (channels[l] ?? 0);
  });
  const width = x + MARGIN;
  const height = Math.max(...elements.map((el) => el.y + el.down)) + MARGIN;

  const placed = new Map<string, Placed>();
  for (const el of nodes) {
    const box = must(el.box);
    placed.set(box.key, {
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
    const p = must(placed.get(el.box.key));
    return right ? p.x + el.box.width : p.x;
  };

  const routed = new Map<string, Routed>();
  links.forEach((link, li) => {
    const edge = must(edges[li]);
    const chain = must(chains[li]);
    const mine = segments.filter((s) => s.link === li);
    const first = must(mine[0]);
    const points: [number, number][] = [
      [sideOf(first.a, true), yOf(first.a, first.pa)],
    ];
    for (const s of mine) {
      const ya = yOf(s.a, s.pa);
      const yb = yOf(s.b, s.pb);
      const t = tracks.get(s);
      if (t === undefined) continue;
      const l = s.a.layer;
      const tx = must(xs[l]) + must(widths[l]) + TRACK_END + t * TRACK;
      points.push([tx, ya], [tx, yb]);
    }
    const last = must(mine.at(-1));
    points.push([sideOf(last.b, false), yOf(last.b, last.pb)]);
    if (edge.back) points.reverse();
    const start = must(points[0]);
    const end = must(points.at(-1));
    const ports: Routed["ports"] = [[...start], [...end]];
    // Room for the arrow at the end.
    end[0] += edge.back ? ARROW : -ARROW;

    const dummy = must(chain.find((el) => el.label !== null));
    const labelBox = must(dummy.label);
    const lx = must(xs[dummy.layer]);
    const lw = must(widths[dummy.layer]);
    routed.set(link.key, {
      points,
      ports,
      back: edge.back,
      label: {
        x: edge.back ? lx + lw - labelBox.width : lx,
        y: dummy.y - LIFT - labelBox.height,
        width: labelBox.width,
        height: labelBox.height,
      },
    });
  });

  return { width, height, boxes: placed, links: routed };
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
  layers: Element[][],
  segments: Segment[],
  reversed: boolean[],
  ahead: boolean,
  downward: boolean,
  gap: (a: Element, b: Element) => number,
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
