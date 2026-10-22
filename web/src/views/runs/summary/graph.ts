// The layout of the machine's graph and the transitions an attempt took. The
// states, then the end, are cards in layers from left to right (layered.ts),
// in the graph's order where the transitions allow; an edge against that
// order runs right to left. Each edge leaves and reaches a card at a port of
// its own on the card's side, its label beside the card it leaves; an edge
// to its own state is a loop over the card, its label beside the loop.

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
import { layered } from "./layered";

export const END = "\0end";

const W = 168;
const H = 56;
/** The radius of an edge's corners. */
const R = 8;
/** The width of a character of a card's title, for its card's width. */
const CHAR = 8.2;
/**
 * The height of a line of an edge's label, how far its baseline sits above
 * the line's bottom, and the most a character of it is wide.
 */
export const LINE = 14;
const DESCENT = 4;
const LABEL_CHAR = 7;
/** How high a loop rises, how wide it is, and where it sits on the card. */
const LOOP = 30;
const LOOP_WIDTH = 28;
const LOOP_RIGHT = 16;
/** How far a loop's label keeps from the loop. */
const LOOP_GAP = 6;

interface Node {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Where its edges leave it, from its top left. */
  ports: [number, number][];
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
  const taken = takenCounts(record, project, current);
  const attemptEnded = attemptStatus(project, record) !== null;

  const nodes = order.map((key): Node => {
    const box = {
      key,
      x: 0,
      y: 0,
      width: W,
      height: H,
      ports: [] as [number, number][],
    };
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

  // A card is wide enough for its title, and for its loop's label beside
  // the loop over it.
  const loops = new Map(
    [...groups.values()]
      .filter((g) => g.from === g.to)
      .map((g) => [g.from, g.parts]),
  );
  for (const node of nodes) {
    const loop = loops.get(node.key);
    node.width = Math.max(
      W,
      Math.ceil(56 + node.title.length * CHAR + 48),
      loop ? labelWidth(loop) + LOOP_WIDTH + LOOP_RIGHT + LOOP_GAP + 8 : 0,
    );
  }

  const others = [...groups.entries()].filter(([, g]) => g.from !== g.to);
  const drawn = layered(
    nodes.map((n) => ({
      key: n.key,
      width: n.width,
      height: H,
      above: loops.has(n.key)
        ? Math.max(LOOP, must(loops.get(n.key)).length * LINE + 6)
        : 0,
    })),
    others.map(([key, g]) => ({
      key,
      from: g.from,
      to: g.to,
      label: { width: labelWidth(g.parts), height: g.parts.length * LINE },
    })),
  );
  for (const node of nodes) {
    const at = must(drawn.boxes.get(node.key));
    node.x = at.x;
    node.y = at.y;
    node.height = at.height;
  }
  const box = (key: string) => must(nodes.find((n) => n.key === key));

  const edges = [...groups.entries()].map(([key, g]): Edge => {
    const on = g.parts.some((p) => p.taken > 0);
    const common = { key, from: g.from, to: g.to, taken: on };
    if (g.from === g.to) {
      // A loop over the card's right end, its label left of it.
      const node = box(g.from);
      const b = node.x + node.width - LOOP_RIGHT;
      const a = b - LOOP_WIDTH;
      const y = node.y;
      return {
        ...common,
        path: `M${a} ${y} C ${a} ${y - LOOP}, ${b} ${y - LOOP}, ${b} ${y - 3}`,
        label: {
          x: a - LOOP_GAP,
          y: y - 6,
          anchor: "end",
          stack: true,
          parts: g.parts,
        },
      };
    }
    const r = must(drawn.links.get(key));
    // A port where the edge leaves; its arrow marks where it arrives.
    const [x, y] = r.ports[0];
    const from = box(g.from);
    from.ports.push([x - from.x, y - from.y]);
    return {
      ...common,
      path: rounded(r.points),
      label: {
        x: r.back ? r.label.x + r.label.width : r.label.x,
        // The baseline of the label's last line.
        y: r.label.y + r.label.height - DESCENT,
        anchor: r.back ? "end" : "start",
        stack: true,
        parts: g.parts,
      },
    };
  });

  return { width: drawn.width, height: drawn.height, nodes, edges };
}

/** How wide a label of these parts is, a part to a line. */
function labelWidth(parts: LabelPart[]): number {
  return Math.max(
    0,
    ...parts.map((p) =>
      Math.ceil(
        (p.outcome.length + (p.taken > 0 ? 3 + String(p.taken).length : 0)) *
          LABEL_CHAR,
      ),
    ),
  );
}

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("not in the graph");
  return value;
}
