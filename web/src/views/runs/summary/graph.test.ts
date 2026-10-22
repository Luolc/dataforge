import { describe, expect, it } from "vitest";

import busy from "../../../../fixtures/graph/attempts/batch-1/task-0301/record.json";
import early from "../../../../fixtures/graph/attempts/batch-1/task-0302/record.json";
import tangle from "../../../../fixtures/graph/attempts/batch-1/task-0303/record.json";
import {
  inReview,
  must,
  records,
  reviewSince,
  runningIn,
  sampleProject,
} from "../../../../fixtures/sample";
import { type AttemptRecord, record } from "../../../api/types";
import { type Edge, END, type Layout, layout, takenCounts } from "./graph";

describe("the graph", () => {
  it("counts the transitions the visits took", () => {
    expect(
      Object.fromEntries(takenCounts(records.passed, sampleProject)),
    ).toEqual({
      "author\0done": 2,
      "check\0fail": 1,
      "check\0pass": 1,
      "review\0pass": 1,
      "solve\0in_range": 1,
    });
  });

  it("draws taken transitions solid with counts and the rest dashed", () => {
    const drawn = must(layout(records.passed, sampleProject));
    const edge = (from: string, to: string) =>
      must(drawn.edges.find((e) => e.key === `${from}\0${to}`));

    expect(edge("author", "check").taken).toBe(true);
    expect(edge("author", "check").label.parts).toEqual([
      { outcome: "done", taken: 2 },
    ]);
    expect(edge("review", "author").taken).toBe(false);
    expect(edge("author", END).taken).toBe(false);
    expect(edge("solve", END).taken).toBe(true);
    // Every outcome is named, the taken one with its count.
    expect(edge("solve", END).label.parts).toEqual([
      { outcome: "in_range", taken: 1 },
      { outcome: "all_solved", taken: 0 },
      { outcome: "none_solved", taken: 0 },
    ]);
  });

  it("marks states not run and the end by the attempt's status", () => {
    const drawn = must(layout(records.exhausted, sampleProject));
    const node = (key: string) => must(drawn.nodes.find((n) => n.key === key));

    expect(node("solve")).toMatchObject({
      title: "Pass@k",
      sub: "Not run",
      idle: true,
      lastVisit: null,
    });
    expect(node("check")).toMatchObject({
      status: { tone: "bad" },
      lastVisit: 5,
    });
    expect(node(END)).toMatchObject({
      sub: "Exhausted",
      status: { tone: "bad" },
    });
    // The last visit went back to author, but the attempt ended there.
    expect(
      takenCounts(records.exhausted, sampleProject).get("check\0fail"),
    ).toBe(2);
  });

  it("leaves the end unreached while the status is a not_ended word", () => {
    const rec = { ...records.passed, status: "running" };
    const project = { ...sampleProject, not_ended: ["running"] };
    const drawn = must(layout(rec, project));

    expect(drawn.nodes.at(-1)).toMatchObject({ idle: true, sub: "Not run" });
    expect(takenCounts(rec, project).size).toBe(
      takenCounts(records.passed, sampleProject).size - 1,
    );
  });

  it("reaches the end as No status when not_ended leaves a null status", () => {
    const rec = { ...records.passed, status: null };
    const project = { ...sampleProject, not_ended: ["running"] };
    const drawn = must(layout(rec, project));

    expect(drawn.nodes.at(-1)).toMatchObject({
      idle: false,
      sub: "No status",
      status: { tone: "neutral" },
    });
    expect(takenCounts(rec, project)).toEqual(
      takenCounts(records.passed, sampleProject),
    );
  });

  it("leaves the end unreached while the attempt runs", () => {
    const drawn = must(layout(records.running, sampleProject));

    expect(drawn.nodes.at(-1)).toMatchObject({
      idle: true,
      sub: "Not run",
    });
  });
});

// The shapes a layout draws, read back from its SVG paths and its labels as
// the page draws them, so that what is checked is what shows. A corner
// rounded by Q is read as the corner itself; a curve (a loop) as the box
// round its points.
type Point = [number, number];
interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

function shapesOf(path: string) {
  const segments: [Point, Point][] = [];
  const curves: Rect[] = [];
  let at: Point = [0, 0];
  for (const [, op, args] of path.matchAll(/([MLHQC])([^MLHQC]*)/g)) {
    const n = (args ?? "")
      .trim()
      .split(/[\s,]+/)
      .map(Number);
    const to = (x = 0, y = 0): Point => [x, y];
    if (op === "M") at = to(n[0], n[1]);
    else if (op === "L" || op === "H") {
      const next = op === "L" ? to(n[0], n[1]) : to(n[0], at[1]);
      segments.push([at, next]);
      at = next;
    } else if (op === "Q") {
      const corner = to(n[0], n[1]);
      const next = to(n[2], n[3]);
      segments.push([at, corner], [corner, next]);
      at = next;
    } else {
      const pts = [at, to(n[0], n[1]), to(n[2], n[3]), to(n[4], n[5])];
      curves.push({
        x0: Math.min(...pts.map((p) => p[0])),
        y0: Math.min(...pts.map((p) => p[1])),
        x1: Math.max(...pts.map((p) => p[0])),
        y1: Math.max(...pts.map((p) => p[1])),
      });
      at = to(n[4], n[5]);
    }
  }
  return { segments, curves };
}

/** A label's box: 7 a character, 14 a line, its last line's baseline at y. */
function labelBox(edge: Edge): Rect {
  const { label } = edge;
  const text = label.parts.map(
    (p) => `${p.outcome}${p.taken > 0 ? ` ×${String(p.taken)}` : ""}`,
  );
  const lines = label.stack ? text : [text.join(", ")];
  const w = Math.max(...lines.map((l) => l.length)) * 7;
  const x0 =
    label.anchor === "start"
      ? label.x
      : label.anchor === "end"
        ? label.x - w
        : label.x - w / 2;
  return {
    x0,
    y0: label.y - 10 - (lines.length - 1) * 14,
    x1: x0 + w,
    y1: label.y + 3,
  };
}

const overlap = (p: Rect, q: Rect) =>
  Math.min(p.x1, q.x1) - Math.max(p.x0, q.x0) > 0.5 &&
  Math.min(p.y1, q.y1) - Math.max(p.y0, q.y0) > 0.5;
const segmentRect = ([a, b]: [Point, Point]): Rect => ({
  x0: Math.min(a[0], b[0]),
  y0: Math.min(a[1], b[1]),
  x1: Math.max(a[0], b[0]),
  y1: Math.max(a[1], b[1]),
});
// Whether a segment runs through a box's inside.
const through = (s: [Point, Point], r: Rect) => {
  const b = segmentRect(s);
  const length = (lo: number, hi: number, r0: number, r1: number) =>
    Math.min(hi, r1) - Math.max(lo, r0) > 0.5;
  if (b.y0 === b.y1)
    return b.y0 > r.y0 && b.y0 < r.y1 && length(b.x0, b.x1, r.x0, r.x1);
  if (b.x0 === b.x1)
    return b.x0 > r.x0 && b.x0 < r.x1 && length(b.y0, b.y1, r.y0, r.y1);
  return overlap(b, r);
};
// Whether two segments run along the same line for a stretch.
const along = (s: [Point, Point], t: [Point, Point]) => {
  const p = segmentRect(s);
  const q = segmentRect(t);
  if (p.y0 === p.y1 && q.y0 === q.y1 && p.y0 === q.y0)
    return Math.min(p.x1, q.x1) - Math.max(p.x0, q.x0) > 0.5;
  if (p.x0 === p.x1 && q.x0 === q.x1 && p.x0 === q.x0)
    return Math.min(p.y1, q.y1) - Math.max(p.y0, q.y0) > 0.5;
  return false;
};

/** Every way a layout's shapes get in each other's way, as text. */
function clashes(drawn: Layout): string[] {
  const found: string[] = [];
  const cards = drawn.nodes.map((n) => ({
    key: n.key,
    x0: n.x,
    y0: n.y,
    x1: n.x + n.width,
    y1: n.y + n.height,
  }));
  const shapes = drawn.edges.map((e) => ({
    e,
    ...shapesOf(e.path),
    label: labelBox(e),
  }));
  const name = (e: Edge) => `${e.from}>${e.to === END ? "end" : e.to}`;
  for (const a of shapes) {
    for (const c of cards) {
      if (a.segments.some((s) => through(s, c)))
        found.push(`${name(a.e)} runs through ${c.key}`);
      if (a.curves.some((r) => overlap(r, c)))
        found.push(`${name(a.e)}'s loop covers ${c.key}`);
      if (overlap(a.label, c))
        found.push(`${name(a.e)}'s label covers ${c.key}`);
    }
    for (const b of shapes) {
      if (a === b) continue;
      if (b.segments.some((s) => through(s, a.label)))
        found.push(`${name(b.e)} runs through ${name(a.e)}'s label`);
      if (b.curves.some((r) => overlap(r, a.label)))
        found.push(`${name(b.e)}'s loop covers ${name(a.e)}'s label`);
      if (b.segments.some((s) => a.curves.some((r) => through(s, r))))
        found.push(`${name(b.e)} runs through ${name(a.e)}'s loop`);
      if (a.e.key < b.e.key) {
        if (overlap(a.label, b.label))
          found.push(`${name(a.e)}'s and ${name(b.e)}'s labels overlap`);
        if (a.segments.some((s) => b.segments.some((t) => along(s, t))))
          found.push(`${name(a.e)} and ${name(b.e)} run along each other`);
      }
    }
  }
  return found;
}

// Synthetic machines from a seeded generator: states with loops, edges
// ahead and back, and ends, as many of each as a machine may have.
function machines(count: number): AttemptRecord[] {
  let seed = 7;
  const rand = (n: number) => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return Math.floor(seed / 65536) % n;
  };
  return Array.from({ length: count }, (_, m) => {
    const names = Array.from(
      { length: 2 + rand(10) },
      (_, i) => `s${String(i)}`,
    );
    const edges = names.flatMap((from) =>
      Array.from({ length: 1 + rand(4) }, (_, k) => {
        const pick = rand(names.length + 2);
        const to = pick >= names.length ? null : (names[pick] ?? null);
        return {
          from,
          outcome: `o${String(k)}`,
          to,
          end: to === null ? "passed" : null,
          report: false,
          mark: null,
          end_on_last_visit: false,
        };
      }),
    );
    return {
      ...records.passed,
      visits: [],
      graph: {
        start: must(names[0]),
        states: names.map((name) => ({
          name,
          kind: "AgentState",
          max_visits: 3,
          on_exhausted: "exhausted",
          max_cost_usd: null,
        })),
        edges,
      },
      round: m,
    };
  });
}

describe("the layout's shapes", () => {
  it("keep loops, labels and edges out of each other's way", () => {
    const found = Object.fromEntries(
      Object.entries({ busy, early, tangle }).map(([name, json]) => [
        name,
        clashes(must(layout(record.parse(json), sampleProject))),
      ]),
    );

    expect(found).toEqual({ busy: [], early: [], tangle: [] });
  });

  it("keep out of each other's way on any machine", () => {
    for (const rec of [...Object.values(records), ...machines(60)]) {
      const drawn = must(layout(rec, sampleProject));
      expect(clashes(drawn)).toEqual([]);
    }
  });
});

describe("a running attempt's layout", () => {
  it("draws the state it runs as running and the transition into it taken", () => {
    const drawn = must(layout(inReview, sampleProject, runningIn().current));
    const review = must(drawn.nodes.find((n) => n.key === "review"));

    expect(review).toMatchObject({
      sub: "1 run · Running",
      status: { label: "Running" },
      idle: false,
      lastVisit: null,
      running: { since: reviewSince, before: 0 },
    });
    expect(takenCounts(inReview, sampleProject).get("check\0pass")).toBe(
      undefined,
    );
    expect(
      takenCounts(inReview, sampleProject, runningIn().current).get(
        "check\0pass",
      ),
    ).toBe(1);
  });

  it("counts a run its record holds with no outcome once", () => {
    const current = must(
      runningIn({ state: "check", index: 1, recorded: true }).current,
    );
    const drawn = must(layout(records.running, sampleProject, current));

    expect(drawn.nodes.find((n) => n.key === "check")).toMatchObject({
      sub: "1 run · Running",
      lastVisit: 1,
      running: { before: 0 },
    });
  });
});
