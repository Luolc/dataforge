import { describe, expect, it } from "vitest";

import busy from "../../../../fixtures/graph/attempts/batch-1/task-0301/record.json";
import early from "../../../../fixtures/graph/attempts/batch-1/task-0302/record.json";
import tangle from "../../../../fixtures/graph/attempts/batch-1/task-0303/record.json";
import rollout from "../../../../fixtures/graph/attempts/batch-1/task-0304/record.json";
import wide from "../../../../fixtures/graph/attempts/batch-1/task-0305/record.json";
import swapped from "../../../../fixtures/graph/swapped-heights.json";
import {
  everyTaken,
  inReview,
  measured,
  must,
  records,
  reviewSince,
  runningIn,
  sampleProject,
} from "../../../../fixtures/sample";
import { type AttemptRecord, record } from "../../../api/types";
import {
  CARD_WIDTH,
  type Edge,
  END,
  hiddenOf,
  LABEL_LEAST,
  labelLine,
  type Layout,
  layout,
  LINE,
  LOOP_WIDTH,
  takenCounts,
  TEXT_WIDTH,
  SUB_LINE,
  textHeight,
  textTop,
} from "./graph";
import { type Measure, widest } from "./measure";

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
      { outcome: "done", label: "Done", taken: 2, to: null },
    ]);
    expect(edge("review", "author").taken).toBe(false);
    expect(edge("author", END).taken).toBe(false);
    expect(edge("solve", END).taken).toBe(true);
    // Every outcome is named, the taken one with its count.
    expect(edge("solve", END).label.parts).toEqual([
      { outcome: "in_range", label: "In range", taken: 1, to: null },
      { outcome: "all_solved", label: "All solved", taken: 0, to: null },
      { outcome: "none_solved", label: "None solved", taken: 0, to: null },
    ]);
  });

  it("draws, when asked for what was taken, only the states that ran, the end once a transition into it was taken, and the outcomes taken", () => {
    for (const r of [records.passed, records.raised, records.exhausted]) {
      const whole = must(layout(r, sampleProject));
      const taken = must(layout(r, sampleProject, null, undefined, "taken"));
      const ran = new Set(r.visits.map((v) => v.state));
      const counts = takenCounts(r, sampleProject);

      expect(taken.nodes.map((n) => n.key)).toEqual(
        whole.nodes
          .map((n) => n.key)
          .filter(
            (k) =>
              ran.has(k) ||
              (k === END && whole.edges.some((e) => e.to === END && e.taken)),
          ),
      );
      expect(taken.edges.flatMap((e) => e.label.parts)).toEqual(
        whole.edges
          .flatMap((e) => e.label.parts)
          .filter((p) => p.taken > 0)
          .map((p) => ({ ...p, to: null })),
      );
      expect(taken.hidden).toEqual({
        states: must(r.graph).states.filter((s) => !ran.has(s.name)).length,
        transitions: must(r.graph).edges.filter(
          (e) => !counts.has(`${e.from}\0${e.outcome}`),
        ).length,
      });
      expect(whole.hidden).toBeNull();
    }
  });

  it("draws the whole machine, when asked for what was taken, of an attempt that reached every state and took every transition", () => {
    const whole = must(layout(everyTaken, sampleProject));
    const taken = must(
      layout(everyTaken, sampleProject, null, undefined, "taken"),
    );

    expect(hiddenOf(everyTaken, sampleProject)).toBeNull();
    expect(taken.hidden).toBeNull();
    expect(taken.nodes.map((n) => n.key)).toEqual(
      whole.nodes.map((n) => n.key),
    );
  });

  it("draws the whole machine, when asked for what was taken, while no state has run", () => {
    const none = { ...records.passed, status: null, visits: [] };
    const taken = must(layout(none, sampleProject, null, undefined, "taken"));

    expect(taken.hidden).toBeNull();
    expect(taken.nodes).toHaveLength(must(none.graph).states.length + 1);
  });

  it("says a state's runs recorded no time when none had an action, not 0s", () => {
    const [author, check] = records.raised.visits;
    const bare = { ...must(check), actions: [] };
    const node = (visits: AttemptRecord["visits"]) =>
      must(
        must(layout({ ...records.raised, visits }, sampleProject)).nodes.find(
          (n) => n.key === "check",
        ),
      );

    expect(node([must(author), bare]).time).toBe("no action recorded");
    // A run with no action that keeps its own times gives them.
    const own = {
      ...bare,
      started_at: "2026-03-02T09:30:00Z",
      ended_at: "2026-03-02T09:35:00Z",
    };
    expect(node([must(author), own]).time).toBe("5m 0s");
    // An action whose times do not read is still an action.
    const action = must(must(check).actions[0]);
    const unread = { ...must(check), actions: [{ ...action, ended_at: "?" }] };
    expect(node([must(author), unread]).time).toBe("—");
    expect(node([must(author), bare]).under.join(" ")).not.toMatch(/0s/);
    // A run that has a time gives the state's, the other adding nothing.
    const timed = node([must(author), must(check), must(author), bare]);
    expect(timed.sub).toBe("2 runs");
    expect(timed.time).toBe(
      `${must(node([must(author), must(check)]).time)} in all`,
    );
  });

  it("shows a state whose last run raised as Exception whatever the project file says", () => {
    const project = {
      ...sampleProject,
      statuses: {
        ...sampleProject.statuses,
        raised: { label: "Crashed", tone: "warn" as const },
      },
    };
    const check = must(
      must(layout(records.raised, project)).nodes.find(
        (n) => n.key === "check",
      ),
    );

    expect(check.status).toEqual({ label: "Exception", tone: "bad" });
  });

  it("names each outcome on an edge by the project's label, else as `readable` spells it", () => {
    const project = {
      ...sampleProject,
      statuses: {
        ...sampleProject.statuses,
        in_range: { label: "In range", tone: "good" as const },
        all_solved: { label: "All solved", tone: "warn" as const },
      },
    };
    const drawn = must(layout(records.passed, project));
    const solve = must(drawn.edges.find((e) => e.key === `solve\0${END}`));

    expect(solve.label.parts.map(labelLine)).toEqual([
      "In range ×1",
      "All solved",
      "None solved",
    ]);
  });

  it("draws a transition not taken back or to the end, between states that ran, as a stub naming where it goes", () => {
    const drawn = must(layout(records.passed, sampleProject));
    const edge = (from: string, to: string) =>
      must(drawn.edges.find((e) => e.key === `${from}\0${to}`));

    // Not taken, back to a state before it, both ran.
    expect(edge("review", "author")).toMatchObject({
      stub: true,
      taken: false,
    });
    expect(edge("review", "author").label.parts).toEqual([
      { outcome: "fail", label: "Fail", taken: 0, to: "Author" },
    ]);
    // Not taken, to the end.
    expect(edge("author", END)).toMatchObject({ stub: true, taken: false });
    expect(must(edge("author", END).label.parts[0]).to).toBe("End");
    // Taken, back or to the end: drawn all the way.
    expect(edge("check", "author")).toMatchObject({ stub: false, taken: true });
    expect(edge("solve", END)).toMatchObject({ stub: false, taken: true });
    // Ahead to another state, taken or not: drawn all the way.
    expect(edge("author", "check").stub).toBe(false);
    const ahead = must(
      must(layout(record.parse(rollout), sampleProject)).edges.find(
        (e) => e.key === "unit\0stage",
      ),
    );
    expect(ahead).toMatchObject({ stub: false, taken: false });
    expect(drawn.edges.filter((e) => e.stub).map((e) => e.key)).toEqual([
      `author\0${END}`,
      `review\0author`,
    ]);
  });

  it("draws every transition of a state that never ran all the way", () => {
    const drawn = must(layout(record.parse(rollout), sampleProject));
    const stub = (from: string, to: string) =>
      must(drawn.edges.find((e) => e.key === `${from}\0${to}`)).stub;
    // Back and not taken: a stub between two states that ran, not out of
    // one that never ran.
    expect(stub("compile", "scaffold")).toBe(true);
    expect(stub("rollback", "outline")).toBe(false);
    expect(stub("archive", "verify")).toBe(false);
    expect(stub("rollback", END)).toBe(false);

    // The seeded machines, each walked, so that some states ran and some
    // did not.
    let touched = 0;
    for (let seed = 0; seed < 300; seed++) {
      const drawn = must(
        layout(machine(1200000 + seed, 12, 5, true), sampleProject),
      );
      const idle = new Set(
        drawn.nodes.filter((n) => n.idle && n.key !== END).map((n) => n.key),
      );
      for (const e of drawn.edges) {
        if (e.from === e.to || !(idle.has(e.from) || idle.has(e.to))) continue;
        touched++;
        // Its arrow stops 3 short of the top or the bottom of its target.
        const to = must(drawn.nodes.find((n) => n.key === e.to));
        const end = must(
          [...e.path.matchAll(/L\s*(-?[\d.]+)\s+(-?[\d.]+)/g)].at(-1),
        );
        const [x, y] = [Number(end[1]), Number(end[2])];

        expect(e.stub).toBe(false);
        expect(x).toBeGreaterThanOrEqual(to.x);
        expect(x).toBeLessThanOrEqual(to.x + to.width);
        expect([to.y - 3, to.y + to.height + 3]).toContain(y);
      }
    }
    expect(touched).toBeGreaterThan(0);
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

/**
 * A label's box, as wide as its widest line measured as the layouts here
 * measure it (widest), 14 a line, its last line's baseline at y.
 */
function labelBox(edge: Edge): Rect {
  const { label } = edge;
  const w = Math.max(...label.lines.map((l) => widest(l.text, "label")));
  const x0 = label.x;
  return {
    x0,
    y0: label.y - 10 - (label.lines.length - 1) * 14,
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

// A synthetic machine from a seeded generator (an LCG, its high bits): 1 to
// most states, 1 to outs outcomes each, of words from 1 to 19 characters;
// a fifth of the edges to one busy state, a tenth to the state itself, a
// fifth to the end and the rest to any state; with walk, visits along the
// edges, so that labels carry counts.
const WORDS = [
  "ok",
  "no",
  "x",
  "done",
  "pass",
  "fail",
  "error",
  "held",
  "in_range",
  "retry_later",
  "needs_more_work",
  "a_very_long_outcome",
];

function machine(
  seed: number,
  most: number,
  outs: number,
  walk: boolean,
): AttemptRecord {
  let s = seed;
  const rand = (n: number) => {
    s = (s * 1103515245 + 12345) % 2147483648;
    return Math.floor(s / 65536) % n;
  };
  const names = Array.from(
    { length: 1 + rand(most) },
    (_, i) => `q${String(i)}`,
  );
  const busy = must(names[rand(names.length)]);
  const edges = names.flatMap((from) => {
    const used = new Set<string>();
    return Array.from({ length: 1 + rand(outs) }, (_, k) => {
      const word = must(WORDS[rand(WORDS.length)]);
      const outcome = used.has(word) ? `${word}${String(k)}` : word;
      used.add(outcome);
      const r = rand(10);
      const to =
        r < 2
          ? null
          : r < 4
            ? busy
            : r < 5
              ? from
              : must(names[rand(names.length)]);
      return {
        from,
        outcome,
        to,
        end: to === null ? "passed" : null,
        report: false,
        mark: null,
        end_on_last_visit: false,
      };
    });
  });
  const visits: AttemptRecord["visits"] = [];
  for (let state = walk ? names[0] : undefined; state !== undefined;) {
    const here = edges.filter((e) => e.from === state);
    const e = must(here[rand(here.length)]);
    visits.push({
      ...must(records.passed.visits[0]),
      state,
      visit: visits.length + 1,
      outcome: e.outcome,
    });
    state = visits.length < 25 ? (e.to ?? undefined) : undefined;
  }
  return {
    ...records.passed,
    visits,
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
  };
}

/**
 * How long the machines may take: a few seconds here, more on a slower
 * runner.
 */
const MACHINES_MS = 30_000;

/** How many machines of each size the test lays out, with walks and not. */
const SIZES = [
  { most: 6, outs: 3, count: 800 },
  { most: 12, outs: 5, count: 600 },
  { most: 20, outs: 6, count: 150 },
  { most: 30, outs: 4, count: 30 },
];

/** Titles of tangle's states that break into more than one line. */
const long = {
  critique: { title: "Second pass on the edits" },
  probe: { title: "a_state_name_with_no_spaces_at_all" },
};

describe("the layout's shapes", () => {
  it("keep loops, labels and edges out of each other's way", () => {
    const found = Object.fromEntries(
      Object.entries({ busy, early, tangle, rollout }).map(([name, json]) => [
        name,
        clashes(must(layout(record.parse(json), sampleProject))),
      ]),
    );

    expect(found).toEqual({ busy: [], early: [], tangle: [], rollout: [] });
  });

  it("keep every label inside the graph's width and height", () => {
    for (const json of [busy, early, tangle, rollout, wide]) {
      for (const scope of ["all", "taken"] as const) {
        const drawn = must(
          layout(record.parse(json), sampleProject, null, widest, scope),
        );
        for (const e of drawn.edges) {
          const box = labelBox(e);
          expect(box.x1).toBeLessThanOrEqual(drawn.width);
          expect(box.y1).toBeLessThanOrEqual(drawn.height);
        }
      }
    }
  });

  it("keep each label as wide as the project's label of its outcome", () => {
    // Every outcome of these records labelled far wider than its name.
    const outcomes = Object.values({ busy, early, tangle, rollout }).flatMap(
      (json) => record.parse(json).graph?.edges.map((e) => e.outcome) ?? [],
    );
    const project = {
      ...sampleProject,
      statuses: Object.fromEntries(
        outcomes.map((o) => [
          o,
          {
            label: `Golden swapped for the best ${o}`,
            tone: "neutral" as const,
          },
        ]),
      ),
    };
    const found = Object.fromEntries(
      Object.entries({ busy, early, tangle, rollout }).map(([name, json]) => [
        name,
        clashes(must(layout(record.parse(json), project))),
      ]),
    );

    expect(found).toEqual({ busy: [], early: [], tangle: [], rollout: [] });
  });

  it("make each card as tall as its text and as wide as its busiest side needs", () => {
    const sizes = (drawn: Layout) => {
      // The ports on the top and the bottom of each card, from where its
      // edges start and end; an arrow stops 3 short of its card.
      const ports = new Map<string, number>();
      for (const e of drawn.edges) {
        if (e.from === e.to) continue;
        const ys = [...e.path.matchAll(/[ML]\s*-?[\d.]+\s+(-?[\d.]+)/g)].map(
          (m) => Number(m[1]),
        );
        for (const [key, y] of [
          [e.from, must(ys[0])],
          [e.to, must(ys.at(-1))],
        ] as const) {
          const n = must(drawn.nodes.find((c) => c.key === key));
          const side = Math.abs(y - n.y) < Math.abs(y - n.y - n.height) ? 0 : 1;
          const at = `${key} ${String(side)}`;
          ports.set(at, (ports.get(at) ?? 0) + 1);
        }
      }
      const loops = new Map(
        drawn.edges
          .filter((e) => e.from === e.to)
          .map((e) => [e.from, e.label.parts.length]),
      );
      return drawn.nodes.map((n) => ({
        width: n.width,
        height: n.height,
        wants: {
          width: Math.max(
            CARD_WIDTH,
            ...[0, 1].map((side) => {
              const count = ports.get(`${n.key} ${String(side)}`) ?? 0;
              return count > 0 ? (count - 1) * 12 + 20 : 0;
            }),
          ),
          height: Math.max(
            textHeight(n),
            (loops.get(n.key) ?? 0) * LINE + LOOP_WIDTH,
          ),
        },
      }));
    };
    const tangled = sizes(
      must(layout(record.parse(tangle), { ...sampleProject, states: long })),
    );
    const machines = Array.from({ length: 200 }, (_, i) =>
      sizes(must(layout(machine(3000000 + i, 30, 6, false), sampleProject))),
    );

    for (const all of [tangled, ...machines])
      for (const s of all)
        expect({ width: s.width, height: s.height }).toEqual(s.wants);
    // Heights differ with the title's lines, and widths with the ports.
    expect(new Set(tangled.map((s) => s.height)).size).toBeGreaterThan(1);
    expect(
      machines.some((all) => new Set(all.map((s) => s.width)).size > 1),
    ).toBe(true);
  });

  it("break each line of a card to fit its width, cutting nothing", () => {
    const states = {
      ...long,
      assemble: { title: "MMMMMMMMMMMMMMM" },
      refine: { title: "WWWWW mmmmm" },
    };
    const project = { ...sampleProject, states };
    const words = (text: string) => text.replaceAll(" ", "");
    for (const measure of [measured, widest]) {
      const drawn = must(layout(record.parse(tangle), project, null, measure));
      // No card grows for its text, each line fits beside the icon, and
      // the lines hold the whole text.
      for (const n of drawn.nodes) {
        expect(n.width).toBe(CARD_WIDTH);
        for (const line of n.lines)
          expect(measure(line, "title")).toBeLessThanOrEqual(TEXT_WIDTH);
        for (const line of n.under)
          expect(measure(line, "sub")).toBeLessThanOrEqual(TEXT_WIDTH);
        expect(words(n.lines.join(""))).toBe(words(n.title));
        // On one line, runs and time are joined by a dot; on two, not.
        expect([
          words(n.time === null ? n.sub : `${n.sub} · ${n.time}`),
          words(n.sub + (n.time ?? "")),
        ]).toContain(words(n.under.join("")));
      }
    }

    const drawn = must(layout(record.parse(tangle), project, null, measured));
    const lines = (key: string) =>
      must(drawn.nodes.find((n) => n.key === key)).lines;
    expect(lines("critique")).toEqual(["Second pass on", "the edits"]);
    for (const key of ["probe", "assemble", "refine"])
      expect(lines(key).length).toBeGreaterThan(1);
  });

  it("centre each card's text in its height", () => {
    let taller = 0;
    for (let seed = 0; seed < 100; seed++) {
      const drawn = must(
        layout(
          machine(5000000 + seed, 8, 5, true),
          sampleProject,
          null,
          measured,
        ),
      );
      for (const n of drawn.nodes) {
        if (n.height <= textHeight(n)) continue;
        taller++;
        // Its padding, where the card is only as tall as its text.
        const pad = textTop({ ...n, height: textHeight(n) });
        const text = textHeight(n) - 2 * pad;
        expect(textTop(n)).toBeCloseTo(n.height - textTop(n) - text);
      }
    }
    expect(taller).toBeGreaterThan(0);
  });

  it("lay the cards out top to bottom, those a state leads to from left to right in the order of its transitions", () => {
    // a forks to b and c, which both go to the end; first is the state the
    // transition a lists first leads to.
    const fork = (states: string[], first: string, second: string) =>
      must(
        layout(
          {
            ...records.passed,
            visits: [],
            graph: {
              start: "a",
              states: states.map((name) => ({
                name,
                kind: "AgentState",
                max_visits: 3,
                on_exhausted: "exhausted",
                max_cost_usd: null,
              })),
              edges: [
                ["a", "one", first],
                ["a", "two", second],
                ["b", "done", null],
                ["c", "done", null],
              ].map(([from, outcome, to]) => ({
                from: must(from),
                outcome: must(outcome),
                to: to ?? null,
                end: to === null ? "passed" : null,
                report: false,
                mark: null,
                end_on_last_visit: false,
              })),
            },
          },
          sampleProject,
          null,
          measured,
        ),
      );
    for (const states of [
      ["a", "b", "c"],
      ["a", "c", "b"],
    ])
      for (const [first, second] of [
        ["b", "c"],
        ["c", "b"],
      ] as const) {
        const drawn = fork(states, first, second);
        const node = (key: string) =>
          must(drawn.nodes.find((n) => n.key === key));
        const [a, one, two, end] = ["a", first, second, END].map(node);

        for (const below of [must(one), must(two)])
          expect(must(a).y + must(a).height).toBeLessThan(below.y);
        for (const above of [must(one), must(two)])
          expect(above.y + above.height).toBeLessThan(must(end).y);
        // The two share a layer, the first one's card left of the other's.
        expect(must(one).y).toBe(must(two).y);
        expect(must(one).x + must(one).width).toBeLessThan(must(two).x);
      }
  });

  it("keep apart two edges that swap heights between two layers", () => {
    const found = swapped.graphs.map((graph) =>
      clashes(
        must(
          layout(
            record.parse({ ...records.passed, visits: [], graph }),
            sampleProject,
          ),
        ),
      ),
    );

    expect(found).toEqual([[], []]);
  });

  it(
    "keep out of each other's way on any machine",
    () => {
      for (const rec of Object.values(records))
        expect(clashes(must(layout(rec, sampleProject)))).toEqual([]);
      const found = SIZES.flatMap(({ most, outs, count }) =>
        Array.from({ length: count }, (_, i) => 100000 * most + i).flatMap(
          (seed) =>
            [false, true].flatMap((walk) => {
              const drawn = must(
                layout(machine(seed, most, outs, walk), sampleProject),
              );
              const c = clashes(drawn);
              return c.length > 0
                ? [{ seed, most, outs, walk, clashes: c.slice(0, 3) }]
                : [];
            }),
        ),
      );

      expect(found).toEqual([]);
    },
    MACHINES_MS,
  );
});

// Five pixels a character: a line of 25 characters fits a card.
const roomy: Measure = (text) => text.length * 5;

describe("a running attempt's layout", () => {
  it("draws the state it runs as running and the transition into it taken", () => {
    const drawn = must(
      layout(inReview, sampleProject, runningIn().current, roomy),
    );
    const review = must(drawn.nodes.find((n) => n.key === "review"));

    expect(review).toMatchObject({
      sub: "Run 1 of 1",
      under: [],
      status: { label: "Running" },
      idle: false,
      lastVisit: null,
      running: {
        since: reviewSince,
        before: 0,
        rows: [{ lead: "Run 1 · Running ", live: "now", tail: "" }],
      },
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
      sub: "Run 1 of 1",
      lastVisit: 1,
      running: { before: 0 },
    });
  });

  it("writes the run now and the total of all runs on lines of their own", () => {
    const current = {
      state: "check",
      visit: 5,
      index: 4,
      recorded: false,
      since: reviewSince,
    };
    const drawn = must(layout(records.passed, sampleProject, current, roomy));
    const check = must(drawn.nodes.find((n) => n.key === "check"));
    const running = must(check.running);

    const n = check.sub.split(" ")[1];
    expect(Number(n)).toBeGreaterThan(1);
    expect(check.sub).toBe(`Run ${n ?? ""} of ${n ?? ""}`);
    expect(running.rows).toEqual([
      { lead: `Run ${n ?? ""} · Running `, live: "now", tail: "" },
      { lead: `${n ?? ""} runs · `, live: "all", tail: " in all" },
    ]);
    expect(textHeight(check)).toBe(
      textHeight({ ...check, running: { ...running, rows: [] } }) +
        2 * SUB_LINE,
    );
  });
});

describe("a running card's two lines", () => {
  it("keeps the run now on one line with its clock at the widest, when that fits the card", () => {
    // 6px a character: "Run 15 · Running 88m_88s" is 144px, over the 128px
    // beside the icon and within the 152px of the card's live lines.
    const six: Measure = (text) => text.length * 6;
    const current = {
      state: "check",
      visit: 15,
      index: 4,
      recorded: false,
      since: reviewSince,
    };
    const drawn = must(layout(records.passed, sampleProject, current, six));
    const rows = must(drawn.nodes.find((n) => n.key === "check")?.running).rows;

    expect(rows.map((r) => r.live)).toEqual(["now", "all"]);
  });
});

describe("the time of a state that ended", () => {
  it("says in all after the time of more than one run, and not after one run", () => {
    const drawn = must(layout(records.passed, sampleProject, null, measured));
    const many = drawn.nodes.filter((n) => /^\d+ runs$/.test(n.sub));
    const one = drawn.nodes.filter((n) => n.sub === "1 run");

    expect(many.length).toBeGreaterThan(0);
    for (const n of many) expect(n.time).toMatch(/ in all$/);
    for (const n of one) expect(n.time).not.toContain("in all");
  });
});

describe("a state's runs", () => {
  const busyRecord = record.parse(busy);

  it("lists each run of a state with its outcome, and names how many on a card that ran more than once", () => {
    const drawn = must(layout(busyRecord, sampleProject));
    const draft = must(drawn.nodes.find((n) => n.key === "draft"));
    const audit = must(drawn.nodes.find((n) => n.key === "audit"));

    expect(draft.runsText?.text).toBe("5 runs");
    expect(draft.runs.map((r) => [r.index, r.label, r.recorded])).toEqual([
      [0, "Done", true],
      [2, "Invalid", true],
      [3, "Done", true],
      [9, "Done", true],
      [22, "Done", true],
    ]);
    // One run: the card links to it, and has no control to list it.
    expect(audit.runs.map((r) => r.index)).toEqual([must(audit.lastVisit)]);
    expect(audit.runsText).toBeNull();
  });

  it("lists the run a state runs now after those it ran, the card naming it by the state's count", () => {
    const current = {
      state: "check",
      visit: 3,
      index: 6,
      recorded: false,
      since: reviewSince,
    };
    const drawn = must(layout(records.passed, sampleProject, current, roomy));
    const check = must(drawn.nodes.find((n) => n.key === "check"));

    expect(check.runsText?.text).toBe("Run 3");
    expect(check.runs.map((r) => [r.index, r.label, r.recorded])).toEqual([
      [1, "Fail", true],
      [3, "Pass", true],
      [6, "Running", false],
    ]);
  });

  it("lists nothing for a state not run", () => {
    const drawn = must(layout(record.parse(rollout), sampleProject));
    const idle = drawn.nodes.filter((n) => n.idle);

    expect(idle.length).toBeGreaterThan(0);
    for (const n of idle) {
      expect(n.runs).toEqual([]);
      expect(n.runsText).toBeNull();
    }
  });
});

describe("a graph wider than its room", () => {
  const records = { busy, early, tangle, rollout, wide };
  const natural = (json: unknown, scope: "all" | "taken") =>
    must(layout(record.parse(json), sampleProject, null, widest, scope));
  // Each part's lines.
  const partsOf = (drawn: Layout) =>
    drawn.edges.flatMap((e) =>
      e.label.parts.map((part, i) => ({
        edge: e,
        part,
        lines: e.label.lines.filter((line) => line.part === i),
      })),
    );
  // A line's first piece: its first word, with the word after a "·" or "→"
  // that starts it, or the words up to a ")" where it starts with a "(".
  const firstPiece = (text: string) => {
    const words = text.split(" ");
    const [a = "", b = ""] = words;
    if (a === "·" || a === "→") return `${a} ${b}`;
    if (!a.startsWith("(") || a.endsWith(")")) return a;
    const end = words.findIndex((w) => w.endsWith(")"));
    return words.slice(0, end + 1).join(" ");
  };

  it("breaks its labels onto more lines to fit, losing nothing of their text and no parenthesis", () => {
    for (const scope of ["all", "taken"] as const) {
      const room = natural(wide, scope).width - 120;
      const drawn = must(
        layout(record.parse(wide), sampleProject, null, widest, scope, room),
      );

      expect(drawn.width).toBeLessThanOrEqual(room);
      expect(clashes(drawn)).toEqual([]);
      const parts = partsOf(drawn);
      expect(parts.some(({ lines }) => lines.length > 1)).toBe(true);
      for (const { part, lines } of parts) {
        expect(lines.map((line) => line.text).join(" ")).toBe(labelLine(part));
        for (const line of lines) {
          expect(line.text.split("(").length).toBe(line.text.split(")").length);
        }
      }
    }
  });

  it("keeps the words in a parenthesis on one line", () => {
    const project = {
      ...sampleProject,
      statuses: {
        ...sampleProject.statuses,
        all_solved_too_easy: {
          label: "All solved (far too easy)",
          tone: "warn" as const,
        },
      },
    };
    const drawn = must(
      layout(record.parse(wide), project, null, widest, "all", 1),
    );
    const lines = partsOf(drawn)
      .filter(({ part }) => part.label === "All solved (far too easy)")
      .map(({ lines }) => lines.map((line) => line.text));

    expect(lines.length).toBeGreaterThan(0);
    for (const texts of lines) {
      expect(texts.length).toBeGreaterThan(1);
      expect(texts).toContain("(far too easy)");
    }
  });

  it("is laid out as it is when it fits its room", () => {
    for (const json of Object.values(records)) {
      for (const scope of ["all", "taken"] as const) {
        const drawn = natural(json, scope);
        expect(
          layout(
            record.parse(json),
            sampleProject,
            null,
            widest,
            scope,
            drawn.width,
          ),
        ).toEqual(drawn);
      }
    }
  });

  it("breaks no label's line before it is half a card wide, when nothing fits", () => {
    for (const [name, json] of Object.entries(records)) {
      const drawn = must(
        layout(record.parse(json), sampleProject, null, widest, "all", 1),
      );
      expect([name, clashes(drawn)]).toEqual([name, []]);
      for (const { lines } of partsOf(drawn)) {
        lines.slice(1).forEach((next, i) => {
          const line = must(lines[i]);
          // What would have gone on the line before it, had it fit there.
          expect(
            widest(`${line.text} ${firstPiece(next.text)}`, "label"),
          ).toBeGreaterThan(LABEL_LEAST);
        });
      }
    }
  });
});
