// The layout of the machine's graph and the transitions an attempt took. The
// states sit in one row in the graph's order, then the end, each a card with a
// port on either side. An edge to the next node runs straight between their
// ports; an edge further forward runs above the row, an edge back below it,
// in one lane for all the edges back to its state; an edge to its own state
// is a loop over the card.

import type { AttemptRecord, Current, Project, Tone } from "../../../api/types";
import { duration, visitSeconds } from "../../../ui/format";
import {
  attemptStatus,
  endWord,
  liveStatus,
  NOT_RUN,
  outcomeOf,
  stateTitle,
  statusOf,
} from "../../../ui/status";

export const END = "\0end";

const W = 168;
const H = 56;
const GAP = 88;
const X0 = 32;
/** How far apart the lanes above and below the row run. */
const LANE = 36;
/** How far the lane nearest the row runs from the cards. */
const NEAR = 22;
/** The radius of an edge's corners. */
const R = 8;
/** The width of a character of a card's title, for its card's width. */
const CHAR = 8.2;
/** How far apart the edges that leave or reach one side of a card run. */
const STEP = 14;

interface Node {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  /** Runs and their total time, or the end's status; Not run when none. */
  sub: string;
  time: string | null;
  /**
   * The word the node's icon and accessible name show: its last visit's
   * outcome, or the attempt's status at the end; null for none.
   */
  word: string | null;
  /** Whether a null word is No outcome rather than Not ended. */
  ended: boolean;
  /** The word's label and tone; null when the node was not reached. */
  status: { label: string; tone: Tone } | null;
  idle: boolean;
  /** The index in visits of the state's last visit; null for none. */
  lastVisit: number | null;
  /**
   * The state the attempt runs now: when its visit began, null when that is
   * not known, and the seconds of its visits before it; null for any other.
   */
  running: { since: string | null; before: number } | null;
}

interface LabelPart {
  outcome: string;
  taken: number;
}

export interface Edge {
  key: string;
  from: string;
  to: string;
  path: string;
  taken: boolean;
  /** An edge back, drawn from its end, with its arrow at the path's start. */
  back: boolean;
  label: {
    x: number;
    y: number;
    anchor: "start" | "middle" | "end";
    /** Stack the parts one a line, else run them on one. */
    stack: boolean;
    parts: LabelPart[];
  };
}

export interface Layout {
  width: number;
  height: number;
  nodes: Node[];
  edges: Edge[];
}

/**
 * How many times the attempt took each transition, by "from\0outcome": a
 * visit took it when the next visit is of the state it goes to, the visit
 * the attempt runs now that its record does not hold yet included, or, for
 * the last visit, when it goes to the end and the attempt has ended with a
 * status.
 */
export function takenCounts(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
): Map<string, number> {
  const ended = endWord(project, record) !== null;
  const counts = new Map<string, number>();
  const edges = record.graph?.edges ?? [];
  const pending = current !== null && !current.recorded ? current : null;
  record.visits.forEach((visit, i) => {
    const next =
      record.visits[i + 1] ??
      (pending && i === record.visits.length - 1 ? pending : undefined);
    const edge = edges.find(
      (e) => e.from === visit.state && e.outcome === visit.outcome,
    );
    if (edge === undefined) return;
    const took = next ? edge.to === next.state : edge.to === null && ended;
    if (!took) return;
    const key = `${edge.from}\0${edge.outcome}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  return counts;
}

/**
 * Give each span, shortest first, the innermost lane where it overlaps no
 * span already there, so that a shorter edge never crosses a longer one's
 * corner; returns each span's lane, from 0, in the order given.
 */
export function lanes(spans: [number, number][]): number[] {
  const byLength = spans
    .map((s, i) => ({ s, i }))
    .sort((a, b) => {
      const byLength = a.s[1] - a.s[0] - (b.s[1] - b.s[0]);
      return byLength !== 0 ? byLength : a.s[0] - b.s[0];
    });
  const taken: [number, number][][] = [];
  const lane = new Array<number>(spans.length).fill(0);
  for (const { s, i } of byLength) {
    let n = taken.findIndex((l) => l.every(([a, b]) => s[1] <= a || s[0] >= b));
    if (n < 0) n = taken.push([]) - 1;
    taken[n]?.push(s);
    lane[i] = n;
  }
  return lane;
}

// A path through the given corners, each rounded by R.
function rounded(points: [number, number][]): string {
  const [first, ...rest] = points;
  if (first === undefined) return "";
  let d = `M${first[0]} ${first[1]}`;
  rest.forEach(([x, y], i) => {
    const next = rest[i + 1];
    const prev = points[i] ?? first;
    if (next === undefined) {
      d += ` L${x} ${y}`;
      return;
    }
    const r = (a: number, b: number) =>
      Math.sign(b - a) * Math.min(R, Math.abs(b - a) / 2);
    d += ` L${x - r(prev[0], x)} ${y - r(prev[1], y)}`;
    d += ` Q${x} ${y} ${x + r(x, next[0])} ${y + r(y, next[1])}`;
  });
  return d;
}

/**
 * Lay out the record's graph, with current, the visit a running attempt is
 * in (currentOf), drawn as running; null when the record has none.
 */
export function layout(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
): Layout | null {
  const graph = record.graph;
  if (!graph) return null;
  const order = [...graph.states.map((s) => s.name), END];
  const at = (key: string) => order.indexOf(key);
  const taken = takenCounts(record, project, current);
  const attemptEnded = attemptStatus(project, record) !== null;

  const nodes = order.map((key): Node => {
    const box = { key, x: 0, y: 0, width: W, height: H };
    if (key === END) {
      const end = endWord(project, record);
      const ended = end !== null;
      const status = statusOf(project, end);
      return {
        ...box,
        title: "End",
        sub: ended ? status.label : NOT_RUN,
        time: null,
        word: end,
        ended: true,
        status: ended ? status : null,
        idle: !ended,
        lastVisit: null,
        running: null,
      };
    }
    const mine = record.visits.filter((v) => v.state === key);
    const last = mine.at(-1);
    const seconds = mine.reduce((sum, v) => sum + (visitSeconds(v) ?? 0), 0);
    if (current?.state === key) {
      const done = current.recorded ? mine.slice(0, -1) : mine;
      const runs = done.length + 1;
      return {
        ...box,
        title: stateTitle(project, key),
        sub: `${runs.toString()} run${runs > 1 ? "s" : ""} · ${liveStatus.running.label}`,
        time: null,
        word: null,
        ended: false,
        status: liveStatus.running,
        idle: false,
        lastVisit: current.recorded
          ? current.index
          : last
            ? record.visits.lastIndexOf(last)
            : null,
        running: {
          since: current.since,
          before: done.reduce((sum, v) => sum + (visitSeconds(v) ?? 0), 0),
        },
      };
    }
    return {
      ...box,
      title: stateTitle(project, key),
      sub:
        mine.length === 0
          ? NOT_RUN
          : `${mine.length} run${mine.length > 1 ? "s" : ""}`,
      time: mine.length === 0 ? null : duration(seconds),
      word: last?.outcome ?? null,
      ended: attemptEnded,
      status: last ? outcomeOf(project, last.outcome, attemptEnded) : null,
      idle: mine.length === 0,
      lastVisit: last ? record.visits.lastIndexOf(last) : null,
      running: null,
    };
  });
  let left = X0;
  for (const node of nodes) {
    node.width = Math.max(W, Math.ceil(56 + node.title.length * CHAR + 48));
    node.x = left;
    left += node.width + GAP;
  }
  const box = (key: string) => must(nodes[at(key)]);

  // Transitions between the same two nodes share one edge.
  const groups = new Map<
    string,
    { from: string; to: string; parts: LabelPart[] }
  >();
  for (const e of graph.edges) {
    const to = e.to ?? END;
    const key = `${e.from}\0${to}`;
    const group = groups.get(key) ?? { from: e.from, to, parts: [] };
    group.parts.push({
      outcome: e.outcome,
      taken: taken.get(`${e.from}\0${e.outcome}`) ?? 0,
    });
    groups.set(key, group);
  }
  const all = [...groups.entries()].map(([key, g]) => ({
    key,
    ...g,
    span: at(g.to) - at(g.from),
  }));
  const back = all.filter((g) => g.span < 0);
  const ahead = all.filter((g) => g.span > 1);

  // The edges back to one state share a lane, from that state to the
  // furthest one they leave from.
  const targets = [...new Set(back.map((g) => g.to))];
  const backLane = new Map(
    lanes(
      targets.map((t): [number, number] => [
        at(t),
        Math.max(...back.filter((g) => g.to === t).map((g) => at(g.from))),
      ]),
    ).map((l, i) => [targets[i], l]),
  );
  const aheadLane = lanes(ahead.map((g) => [at(g.from), at(g.to)]));
  const aheadCount = Math.max(0, ...aheadLane.map((l) => l + 1));
  const backCount = Math.max(0, ...[...backLane.values()].map((l) => l + 1));

  const loops = all.some((g) => g.span === 0);
  const top = 24 + Math.max(aheadCount * LANE, loops ? 34 : 0) + NEAR;
  const row = top + H / 2;
  const bottom = top + H;
  const height = bottom + backCount * LANE + 28;
  const width = left - GAP + X0;
  for (const node of nodes) node.y = top;

  // Where the edges leave and reach a card's bottom (back) or top (ahead):
  // the one in the outer lane further from the edge's other end, so that
  // no two cross.
  const slot = (
    list: { key: string; lane: number }[],
    key: string,
    farFirst: boolean,
  ) => {
    const sorted = [...list].sort((a, b) =>
      farFirst ? b.lane - a.lane : a.lane - b.lane,
    );
    return sorted.findIndex((s) => s.key === key);
  };

  // The i-th of n points spread left to right about a card's middle.
  const spread = (node: Node, n: number, i: number) => {
    const step = Math.min(STEP, (node.width - 32) / Math.max(1, n - 1));
    return node.x + node.width / 2 - ((n - 1) * step) / 2 + i * step;
  };

  const edges = all.map((g): Edge => {
    const on = g.parts.some((p) => p.taken > 0);
    const from = box(g.from);
    const to = box(g.to);
    const common = { key: g.key, from: g.from, to: g.to, taken: on };
    if (g.span === 0) {
      const a = from.x + from.width - 44;
      const b = from.x + from.width - 16;
      const y = top;
      return {
        ...common,
        back: false,
        path: `M${a} ${y} C ${a} ${y - 30}, ${b} ${y - 30}, ${b} ${y - 3}`,
        label: {
          x: a - 4,
          y: y - 14,
          anchor: "end",
          stack: false,
          parts: g.parts,
        },
      };
    }
    if (g.span === 1) {
      const x0 = from.x + from.width;
      return {
        ...common,
        back: false,
        path: `M${x0} ${row} H ${to.x - 3}`,
        label: {
          x: x0 + GAP / 2,
          y: row - 8,
          anchor: "middle",
          stack: true,
          parts: g.parts,
        },
      };
    }
    if (g.span > 1) {
      const lane = aheadLane[ahead.indexOf(g)] ?? 0;
      const y1 = top - NEAR - lane * LANE;
      const outs = ahead
        .filter((o) => o.from === g.from)
        .map((o) => ({ key: o.key, lane: aheadLane[ahead.indexOf(o)] ?? 0 }));
      const ins = ahead
        .filter((o) => o.to === g.to)
        .map((o) => ({ key: o.key, lane: aheadLane[ahead.indexOf(o)] ?? 0 }));
      const a = from.x + from.width - 24 - slot(outs, g.key, true) * STEP;
      const b = to.x + 24 + slot(ins, g.key, true) * STEP;
      return {
        ...common,
        back: false,
        path: rounded([
          [a, top],
          [a, y1],
          [b, y1],
          [b, top - 3],
        ]),
        label: {
          x: a + 8,
          y: y1 - 5,
          anchor: "start",
          stack: false,
          parts: g.parts,
        },
      };
    }
    const lane = backLane.get(g.to) ?? 0;
    const y1 = bottom + NEAR + lane * LANE;
    const outs = back
      .filter((o) => o.from === g.from)
      .map((o) => ({ key: o.key, lane: backLane.get(o.to) ?? 0 }));
    const a = spread(from, outs.length, slot(outs, g.key, false));
    // Every edge back to a state reaches it at its middle.
    const b = to.x + to.width / 2;
    return {
      ...common,
      back: true,
      // Drawn from the state it goes back to, so that the dashes of edges
      // that share a lane line up and do not join into a solid line; the
      // arrow is at the path's start.
      path: rounded([
        [b, bottom + 3],
        [b, y1],
        [a, y1],
        [a, bottom],
      ]),
      label: {
        x: a - 8,
        y: y1 - 5,
        anchor: "end",
        stack: false,
        parts: g.parts,
      },
    };
  });

  return { width, height, nodes, edges };
}

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("not in the graph");
  return value;
}
