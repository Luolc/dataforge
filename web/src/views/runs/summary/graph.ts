// The layout of the machine's graph and the transitions an attempt took. The
// states sit in one row in the graph's order, then the end. An edge to the
// next node is a straight arrow; an edge back runs below the row, an edge
// further forward above it, and an edge to its own state is a loop over it.

import type { AttemptRecord, Project, Tone } from "../../../api/types";
import { duration, visitSeconds } from "../../../ui/format";
import { endStatus, NOT_RUN, stateTitle, statusOf } from "../../../ui/status";

export const END = "\0end";

const W = 120;
const GAP = 96;
const H = 52;
const X0 = 24;
const LANE = 30;

export interface Node {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  sub: string;
  tone: Tone | null;
  idle: boolean;
  /** The index in visits of the state's last visit; null for none. */
  lastVisit: number | null;
}

export interface LabelPart {
  outcome: string;
  taken: number;
}

export interface Edge {
  key: string;
  path: string;
  taken: boolean;
  label: { x: number; y: number; parts: LabelPart[] };
}

export interface Layout {
  width: number;
  height: number;
  nodes: Node[];
  edges: Edge[];
}

/**
 * How many times the attempt took each transition, by "from\0outcome": a
 * visit took it when the next visit is of the state it goes to, or, for the
 * last visit, when it goes to the end and the attempt has ended with a
 * status.
 */
export function takenCounts(
  record: AttemptRecord,
  project: Project,
): Map<string, number> {
  const ended = endStatus(project, record) !== null;
  const counts = new Map<string, number>();
  const edges = record.graph?.edges ?? [];
  record.visits.forEach((visit, i) => {
    const next = record.visits[i + 1];
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

/** Lay out the record's graph; null when the record has none. */
export function layout(record: AttemptRecord, project: Project): Layout | null {
  const graph = record.graph;
  if (graph === null) return null;
  const order = [...graph.states.map((s) => s.name), END];
  const at = (key: string) => order.indexOf(key);
  const x = (key: string) => X0 + at(key) * (W + GAP);
  const cx = (key: string) => x(key) + W / 2;
  const taken = takenCounts(record, project);

  // Transitions between the same two nodes share one arrow.
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
  const span = (g: { from: string; to: string }) => at(g.to) - at(g.from);
  const lanes = (spans: number[]) => [...new Set(spans)].sort((a, b) => a - b);
  const back = lanes(
    [...groups.values()]
      .map(span)
      .filter((s) => s < 0)
      .map((s) => -s),
  );
  const ahead = lanes([...groups.values()].map(span).filter((s) => s > 1));

  const top = 22 + ahead.length * LANE;
  const row = top + 96;
  const bottom = row + H / 2;
  const height = bottom + back.length * LANE + 30;
  const width = X0 * 2 + order.length * W + (order.length - 1) * GAP;

  const edges: Edge[] = [];
  for (const [key, g] of groups) {
    const on = g.parts.some((p) => p.taken > 0);
    const s = span(g);
    let path: string;
    let label: { x: number; y: number };
    if (s === 0) {
      const a = x(g.from) + 28;
      const b = x(g.from) + W - 28;
      const y = row - H / 2;
      path = `M${a} ${y} C ${a} ${y - 34}, ${b} ${y - 34}, ${b} ${y - 3}`;
      label = { x: (a + b) / 2, y: y - 30 };
    } else if (s === 1) {
      path = `M${x(g.from) + W} ${row} H ${x(g.to) - 3}`;
      label = { x: x(g.from) + W + GAP / 2, y: row - 8 };
    } else if (s < 0) {
      const y1 = bottom + (back.indexOf(-s) + 1) * LANE;
      const a = cx(g.from) - 14;
      const b = cx(g.to) + 14;
      path = `M${a} ${bottom} V ${y1 - 10} Q ${a} ${y1}, ${a - 10} ${y1} H ${b + 10} Q ${b} ${y1}, ${b} ${y1 - 10} V ${bottom + 3}`;
      label = { x: (a + b) / 2, y: y1 - 6 };
    } else {
      const y1 = top - ahead.indexOf(s) * LANE;
      const y0 = row - H / 2;
      const a = cx(g.from) + 34;
      const b = cx(g.to);
      path = `M${a} ${y0} V ${y1 + 10} Q ${a} ${y1}, ${a + 10} ${y1} H ${b - 10} Q ${b} ${y1}, ${b} ${y1 + 10} V ${y0 - 3}`;
      label = { x: (a + b) / 2, y: y1 - 6 };
    }
    edges.push({
      key,
      path,
      taken: on,
      label: { ...label, parts: g.parts },
    });
  }

  const nodes = order.map((key): Node => {
    const box = { key, x: x(key), y: row - H / 2, width: W, height: H };
    if (key === END) {
      const end = endStatus(project, record);
      const ended = end !== null;
      const status = statusOf(project, end);
      return {
        ...box,
        title: "End",
        sub: ended ? status.label : NOT_RUN,
        tone: ended ? status.tone : null,
        idle: !ended,
        lastVisit: null,
      };
    }
    const mine = record.visits.filter((v) => v.state === key);
    const last = mine.at(-1);
    const seconds = mine.reduce((sum, v) => sum + (visitSeconds(v) ?? 0), 0);
    return {
      ...box,
      title: stateTitle(project, key),
      sub:
        mine.length === 0
          ? NOT_RUN
          : `${mine.length} run${mine.length > 1 ? "s" : ""} · ${duration(seconds)}`,
      tone: last ? statusOf(project, last.outcome).tone : null,
      idle: mine.length === 0,
      lastVisit: last ? record.visits.lastIndexOf(last) : null,
    };
  });

  return { width, height, nodes, edges };
}
