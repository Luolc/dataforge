// The layout of the machine's graph and the transitions an attempt took. The
// states, then the end, are cards in layers from top to bottom (layered.ts),
// in the graph's order where the transitions allow; an edge against that
// order runs bottom to top. Each edge leaves and reaches a card at a port of
// its own: an edge ahead from the bottom of the card it leaves to the top of
// the one it goes to, an edge back from the top to the bottom; its label is
// beside the card it leaves. An edge to its own state is a loop beside the
// card's right side, its label right of the loop. A transition not taken
// back or to the end, between two states that both ran, is a stub out of
// the card it leaves, its label naming where it goes.

import type { AttemptRecord, Current, Project, Tone } from "../../../api/types";
import {
  duration,
  NO_ACTION,
  runCount,
  visitSeconds,
} from "../../../ui/format";
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
import { type Font, type Measure, TARGET, widest } from "./measure";

export const END = "\0end";

/**
 * A card's least width, and the room its text has beside the icon; more
 * ports on its top or bottom than that holds make it wider.
 */
export const CARD_WIDTH = 176;
export const TEXT_WIDTH = CARD_WIDTH - 36 - 12;
/** The width of a running card's live lines, which start at its padding. */
export const LIVE_WIDTH = CARD_WIDTH - 12 - 12;
/**
 * The padding above and below a card's text, the height of a line of its
 * title and of a line under it.
 */
const PAD = 10;
export const TITLE_LINE = 18;
export const SUB_LINE = 16;
/** The radius of an edge's corners. */
const R = 8;
/**
 * The height of a line of an edge's label, and how far its baseline sits
 * above the line's bottom.
 */
const LINE = 14;
const DESCENT = 4;
/** How far a loop reaches out of its card, and how tall it is. */
const LOOP = 30;
export const LOOP_WIDTH = 28;
/** How far a loop's label keeps from the loop. */
const LOOP_GAP = 6;
/**
 * How far a run's chip on a label keeps from the text before it and from
 * the chip beside it, the room either side of its number, and how tall it
 * is drawn.
 */
const CHIP_GAP = 4;
const CHIP_PAD = 5;
export const CHIP_HEIGHT = 16;

interface Node {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Where its edges leave it, from its top left. */
  ports: [number, number][];
  title: string;
  /** The title's lines, broken to fit the card's width. */
  lines: string[];
  /** Runs and their total time, or the end's status; Not run when none. */
  sub: string;
  time: string | null;
  /**
   * The lines under the title, each broken to fit the card's width: sub and
   * time, on one line where they fit. A running state's time, which grows,
   * is drawn on a line of its own after them.
   */
  under: string[];
  /**
   * The label and tone the node's icon and accessible name show: its last
   * visit's outcome, or the attempt's status at the end; null when the node
   * was not reached.
   */
  status: { label: string; tone: Tone } | null;
  idle: boolean;
  /** The index in visits of the state's last visit; null for none. */
  lastVisit: number | null;
  /**
   * Each of the state's runs, in the order it ran them, when it ran more
   * than once: its index in visits and its outcome, the run it runs now
   * included; empty otherwise.
   */
  runs: NodeRun[];
  /**
   * What on the card names how many times the state ran, at the start of
   * its first line under the title: "3 runs", or, while it runs, the run
   * now by its number in the Runs tab, "Run 7"; null when runs is empty.
   */
  runsText: { text: string; width: number } | null;
  /**
   * The state the attempt runs now: when its visit began, null when that is
   * not known, and the seconds of its visits before it; null for any other.
   */
  running: {
    since: string | null;
    before: number;
    /** The lines of the current run and the total, each with its clock. */
    rows: LiveRow[];
  } | null;
}

/**
 * A line of a running state that holds a clock: the text before it, which
 * clock (this run's alone, or the total of all its runs), and the text after
 * it; a line with no clock has live null.
 */
interface LiveRow {
  lead: string;
  live: "now" | "all" | null;
  tail: string;
}

/** A run of a state, as its card lists it. */
interface NodeRun {
  /** Its index in visits; its number in the Runs tab is one more. */
  index: number;
  label: string;
  tone: Tone;
  /** Whether the record holds it, so that it has a page of its own. */
  recorded: boolean;
}

interface LabelPart {
  outcome: string;
  /** The outcome's label, as a visit's outcome reads (outcomeOf). */
  label: string;
  taken: number;
  /** The index in visits of each visit that took it, in order. */
  runs: number[];
  /** The title of the state a stub's transition goes to; null for none. */
  to: string | null;
}

export interface Edge {
  key: string;
  from: string;
  to: string;
  path: string;
  taken: boolean;
  /** Whether it is drawn as a stub, its label naming where it goes. */
  stub: boolean;
  /**
   * The parts one a line from x, the last line's baseline at y, and where
   * each part's line and its runs' chips are.
   */
  label: {
    x: number;
    y: number;
    parts: LabelPart[];
    lines: LabelLine[];
  };
}

/**
 * Where a part of a label is drawn: its line's top and height, its text's
 * baseline, and a chip for each run that took it, which links to that run.
 */
interface LabelLine {
  top: number;
  height: number;
  y: number;
  chips: Chip[];
}

/**
 * A run's link on a label: the visit's index, the left and width of the
 * area that takes the pointer, and the width of the chip drawn in its
 * middle.
 */
interface Chip {
  index: number;
  x: number;
  width: number;
  pill: number;
}

export interface Layout {
  width: number;
  height: number;
  /** Whether the attempt has ended: no transition but those taken is left. */
  ended: boolean;
  /** The least side of a link or a control drawn on it, in px. */
  target: number;
  nodes: Node[];
  edges: Edge[];
  /**
   * How many states the attempt never reached and how many transitions it
   * never took were left out; null when the whole machine is drawn.
   */
  hidden: { states: number; transitions: number } | null;
}

/**
 * What of the machine a layout draws: all of it, or only what the attempt
 * reached, the states it ran or runs, the end once it has ended, and the
 * transitions it took, each labelled with the outcomes it took alone.
 */
export type Scope = "all" | "taken";

/**
 * How many times the attempt took each transition, by "from\0outcome"
 * (takenRuns).
 */
export function takenCounts(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
): Map<string, number> {
  return new Map(
    [...takenRuns(record, project, current)].map(([key, runs]) => [
      key,
      runs.length,
    ]),
  );
}

/**
 * The visits that took each transition, by "from\0outcome", as their
 * indices in visits, in order: a visit took it when the next visit is of
 * the state it goes to, the visit the attempt runs now that its record does
 * not hold yet included, or, for the last visit, when it goes to the end
 * and the attempt has ended with a status.
 */
function takenRuns(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
): Map<string, number[]> {
  const ended = endWord(project, record) !== null;
  const runs = new Map<string, number[]>();
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
    runs.set(key, [...(runs.get(key) ?? []), i]);
  });
  return runs;
}

/**
 * Whether the attempt reached a node: a state it ran or runs, or the end
 * through a transition it took, so that the end is never drawn with no line
 * into it.
 */
function reachedIn(
  record: AttemptRecord,
  current: Current | null,
  taken: Map<string, number>,
): (key: string) => boolean {
  const ran = new Set(record.visits.map((v) => v.state));
  if (current !== null) ran.add(current.state);
  const ended = (record.graph?.edges ?? []).some(
    (e) => e.to === null && (taken.get(`${e.from}\0${e.outcome}`) ?? 0) > 0,
  );
  return (key) => (key === END ? ended : ran.has(key));
}

/**
 * How many states and transitions a layout of only what the attempt reached
 * leaves out; null when it would leave out nothing, or when the attempt
 * reached no state and there would be nothing to draw.
 */
export function hiddenOf(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
): { states: number; transitions: number } | null {
  const graph = record.graph;
  if (!graph) return null;
  const taken = takenCounts(record, project, current);
  const reached = reachedIn(record, current, taken);
  if (!graph.states.some((s) => reached(s.name))) return null;
  const states = graph.states.filter((s) => !reached(s.name)).length;
  const transitions = graph.edges.filter(
    (e) => (taken.get(`${e.from}\0${e.outcome}`) ?? 0) === 0,
  ).length;
  return states + transitions === 0 ? null : { states, transitions };
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
 * in (currentOf), drawn as running, and the links on its labels target px a
 * side at least; null when the record has none.
 */
export function layout(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
  measure: Measure = widest,
  scope: Scope = "all",
  target: number = TARGET,
): Layout | null {
  const graph = record.graph;
  if (!graph) return null;
  const runsOf = takenRuns(record, project, current);
  const taken = takenCounts(record, project, current);
  const attemptEnded = attemptStatus(project, record) !== null;
  const reached = reachedIn(record, current, taken);
  const hidden = scope === "taken" ? hiddenOf(record, project, current) : null;
  const order = [...graph.states.map((s) => s.name), END].filter(
    (key) => hidden === null || reached(key),
  );

  const nodes = order.map((key): Node => {
    const box = {
      key,
      x: 0,
      y: 0,
      width: CARD_WIDTH,
      height: 0,
      ports: [] as [number, number][],
    };
    if (key === END) {
      const end = endWord(project, record);
      const ended = end !== null;
      const status = statusOf(project, end);
      return {
        ...box,
        title: "End",
        lines: wrap("End", "title", measure),
        sub: ended ? status.label : NOT_RUN,
        time: null,
        under: below(ended ? status.label : NOT_RUN, null, measure),
        status: ended ? status : null,
        idle: !ended,
        lastVisit: null,
        runs: [],
        runsText: null,
        running: null,
      };
    }
    const visits = record.visits.flatMap((v, index) =>
      v.state === key
        ? [{ ...outcomeOf(project, v.outcome, attemptEnded), index }]
        : [],
    );
    const mine = record.visits.filter((v) => v.state === key);
    const last = mine.at(-1);
    const times = mine.flatMap((v) => {
      const s = visitSeconds(v);
      return s === null ? [] : [s];
    });
    const seconds = times.reduce((sum, s) => sum + s, 0);
    const sub = mine.length === 0 ? NOT_RUN : runCount(mine.length);
    const time =
      mine.length === 0
        ? null
        : times.length === 0
          ? mine.every((v) => v.actions.length === 0)
            ? NO_ACTION
            : "—"
          : mine.length > 1
            ? `${duration(seconds)} ${IN_ALL}`
            : duration(seconds);
    if (current?.state === key) {
      const done = current.recorded ? mine.slice(0, -1) : mine;
      const runs = done.length + 1;
      // The run now by its number in the Runs tab, as every run is named.
      const at = current.recorded ? current.index : record.visits.length;
      const all: NodeRun[] = [
        ...visits.slice(0, done.length).map(({ index, label, tone }) => ({
          index,
          label,
          tone,
          recorded: true,
        })),
        {
          index: at,
          ...liveStatus.running,
          recorded: current.recorded,
        },
      ];
      const text = `Run ${(at + 1).toString()}`;
      const timed = current.since !== null;
      const rows = [
        ...liveRows(
          `${text} · ${liveStatus.running.label} `,
          timed ? "now" : null,
          "",
          measure,
        ),
        ...(timed && runs > 1
          ? liveRows(`${runCount(runs)} · `, "all", ` ${IN_ALL}`, measure)
          : []),
      ];
      return {
        ...box,
        title: stateTitle(project, key),
        lines: wrap(stateTitle(project, key), "title", measure),
        sub: `${text} · ${runCount(runs)}`,
        time: null,
        under: [],
        status: liveStatus.running,
        idle: false,
        lastVisit: current.recorded
          ? current.index
          : last
            ? record.visits.lastIndexOf(last)
            : null,
        runs: runs > 1 ? all : [],
        runsText: runs > 1 ? { text, width: measure(text, "sub") } : null,
        running: {
          since: current.since,
          before: done.reduce((sum, v) => sum + (visitSeconds(v) ?? 0), 0),
          rows,
        },
      };
    }
    return {
      ...box,
      title: stateTitle(project, key),
      lines: wrap(stateTitle(project, key), "title", measure),
      sub,
      time,
      under: below(sub, time, measure),
      status: last ? outcomeOf(project, last.outcome, attemptEnded) : null,
      idle: mine.length === 0,
      lastVisit: last ? record.visits.lastIndexOf(last) : null,
      runs:
        mine.length > 1
          ? visits.map(({ index, label, tone }) => ({
              index,
              label,
              tone,
              recorded: true,
            }))
          : [],
      runsText:
        mine.length > 1 ? { text: sub, width: measure(sub, "sub") } : null,
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
    const count = taken.get(`${e.from}\0${e.outcome}`) ?? 0;
    if (hidden !== null && count === 0) continue;
    const group = groups.get(key) ?? { from: e.from, to, parts: [] };
    group.parts.push({
      outcome: e.outcome,
      label: outcomeOf(project, e.outcome, false).label,
      taken: count,
      runs: runsOf.get(`${e.from}\0${e.outcome}`) ?? [],
      to: null,
    });
    groups.set(key, group);
  }
  // The transitions not taken back against the graph's order or to the
  // end, as stubs; a state that never ran keeps every line it has, so that
  // it stays joined to the rest of the graph.
  const ran = (key: string) =>
    key === END || nodes.some((n) => n.key === key && !n.idle);
  const stubbed = new Set(
    [...groups.entries()]
      .filter(
        ([, g]) =>
          g.from !== g.to &&
          g.parts.every((p) => p.taken === 0) &&
          (g.to === END || order.indexOf(g.to) < order.indexOf(g.from)) &&
          ran(g.from) &&
          ran(g.to),
      )
      .map(([key]) => key),
  );
  for (const key of stubbed) {
    const g = must(groups.get(key));
    const title = g.to === END ? "End" : stateTitle(project, g.to);
    g.parts = g.parts.map((p) => ({ ...p, to: title }));
  }

  // A card is tall enough for its title's lines and those under them, and
  // for its loop's label.
  const loops = new Map(
    [...groups.values()]
      .filter((g) => g.from === g.to)
      .map((g) => [g.from, g.parts]),
  );
  for (const node of nodes) {
    const loop = loops.get(node.key);
    node.height = Math.max(
      textHeight(node),
      loop ? labelHeight(loop, target) + LOOP_WIDTH : 0,
    );
  }

  const others = [...groups.entries()].filter(([, g]) => g.from !== g.to);
  const drawn = layered(
    nodes.map((n) => ({
      key: n.key,
      width: n.width,
      height: n.height,
      beside: loops.has(n.key)
        ? LOOP + LOOP_GAP + labelWidth(must(loops.get(n.key)), measure, target)
        : 0,
    })),
    others.map(([key, g]) => ({
      key,
      from: g.from,
      to: g.to,
      stub: stubbed.has(key),
      label: {
        width: labelWidth(g.parts, measure, target),
        height: labelHeight(g.parts, target),
      },
    })),
  );
  for (const node of nodes) {
    const at = must(drawn.boxes.get(node.key));
    node.x = at.x;
    node.y = at.y;
    node.width = at.width;
  }
  const box = (key: string) => must(nodes.find((n) => n.key === key));

  const edges = [...groups.entries()].map(([key, g]): Edge => {
    const on = g.parts.some((p) => p.taken > 0);
    const common = {
      key,
      from: g.from,
      to: g.to,
      taken: on,
      stub: stubbed.has(key),
    };
    if (g.from === g.to) {
      // A loop beside the card's right side, its label right of it.
      const node = box(g.from);
      const x = node.x + node.width;
      const a = node.y + (node.height - LOOP_WIDTH) / 2;
      const b = a + LOOP_WIDTH;
      const left = x + LOOP + LOOP_GAP;
      const lines = labelLines(
        g.parts,
        left,
        node.y + (node.height - labelHeight(g.parts, target)) / 2,
        measure,
        target,
      );
      return {
        ...common,
        path: `M${x} ${a} C ${x + LOOP} ${a}, ${x + LOOP} ${b}, ${x + 3} ${b}`,
        label: {
          x: left,
          y: must(lines.at(-1)).y,
          parts: g.parts,
          lines,
        },
      };
    }
    const r = must(drawn.links.get(key));
    // A port where the edge leaves; its arrow marks where it arrives.
    const [x, y] = r.ports[0];
    const from = box(g.from);
    from.ports.push([x - from.x, y - from.y]);
    const lines = labelLines(g.parts, r.label.x, r.label.y, measure, target);
    return {
      ...common,
      path: rounded(r.points),
      label: {
        x: r.label.x,
        // The baseline of the label's last line.
        y: must(lines.at(-1)).y,
        parts: g.parts,
        lines,
      },
    };
  });

  return {
    width: drawn.width,
    height: drawn.height,
    ended: attemptEnded,
    target,
    nodes,
    edges,
    hidden,
  };
}

/** The height of a card's text: its title's lines and those under it. */
export function textHeight(node: Pick<Node, "lines" | "under" | "running">) {
  const rows = node.running?.rows.length ?? 0;
  return (
    2 * PAD +
    node.lines.length * TITLE_LINE +
    (node.under.length + rows) * SUB_LINE
  );
}

/** Where a card's text starts, so that it sits in the card's middle. */
export function textTop(
  node: Pick<Node, "height" | "lines" | "under" | "running">,
): number {
  return PAD + (node.height - textHeight(node)) / 2;
}

/** Splits a word into the characters a reader sees, accents and all. */
const graphemes = new Intl.Segmenter();

/**
 * A text's lines in a font: its words, as many to a line as fit the card's
 * width, and a word too wide for a line of its own broken between its
 * characters, so that none is cut.
 */
function wrap(
  text: string,
  font: Font,
  measure: Measure,
  width = TEXT_WIDTH,
): string[] {
  const fits = (line: string) => measure(line, font) <= width;
  const lines: string[] = [];
  for (const word of text.split(/\s+/).filter((w) => w !== "")) {
    const last = lines.at(-1);
    if (last !== undefined && fits(`${last} ${word}`)) {
      lines[lines.length - 1] = `${last} ${word}`;
      continue;
    }
    let rest = Array.from(graphemes.segment(word), (g) => g.segment);
    while (rest.length > 1 && !fits(rest.join(""))) {
      let n = 1;
      while (n < rest.length - 1 && fits(rest.slice(0, n + 1).join(""))) n++;
      lines.push(rest.slice(0, n).join(""));
      rest = rest.slice(n);
    }
    lines.push(rest.join(""));
  }
  return lines.length > 0 ? lines : [""];
}

/** What marks a time as the total of all of a state's runs. */
const IN_ALL = "in all";

/** A clock's widest reading, to break lines by before the clock is known. */
const CLOCK = "88m_88s";

/**
 * The lines of a text that holds a clock, broken to fit the card's width with
 * the clock as wide as its widest reading.
 */
function liveRows(
  lead: string,
  live: LiveRow["live"],
  tail: string,
  measure: Measure,
): LiveRow[] {
  if (live === null) {
    return wrap(lead, "sub", measure, LIVE_WIDTH).map((line) => ({
      lead: line,
      live: null,
      tail: "",
    }));
  }
  return wrap(`${lead}${CLOCK}${tail}`, "sub", measure, LIVE_WIDTH).map(
    (line) => {
      const at = line.indexOf(CLOCK);
      return at < 0
        ? { lead: line, live: null, tail: "" }
        : {
            lead: line.slice(0, at),
            live,
            tail: line.slice(at + CLOCK.length),
          };
    },
  );
}

/** The lines under a title: sub and time, on one line where they fit. */
function below(sub: string, time: string | null, measure: Measure): string[] {
  const one = time === null ? sub : `${sub} · ${time}`;
  if (measure(one, "sub") <= TEXT_WIDTH) return [one];
  return [
    ...wrap(sub, "sub", measure),
    ...(time === null ? [] : wrap(time, "sub", measure)),
  ];
}

/**
 * The text of a part's line of an edge's label: its outcome's label, then,
 * where the attempt took it, "run" or "runs" before the chips of the runs
 * that took it, and, for a stub, where it goes.
 */
export function labelLine(part: LabelPart): string {
  const taken =
    part.runs.length === 0 ? "" : part.runs.length === 1 ? " · run" : " · runs";
  const to = part.to === null ? "" : ` → ${part.to}`;
  return `${part.label}${taken}${to}`;
}

/**
 * A part's line of a label as a reader reads it: its text, then the number
 * each of its runs has in the Runs tab, as in "None solved · runs 2, 4".
 */
export function labelText(part: LabelPart): string {
  const numbers = part.runs.map((i) => (i + 1).toString()).join(", ");
  return numbers === "" ? labelLine(part) : `${labelLine(part)} ${numbers}`;
}

/** How tall a part's line is: tall enough for its links where it has any. */
function lineHeight(part: LabelPart, target: number): number {
  return part.runs.length > 0 ? target : LINE;
}

/** How tall a label of these parts is, a part to a line. */
function labelHeight(parts: LabelPart[], target: number): number {
  return parts.reduce((sum, p) => sum + lineHeight(p, target), 0);
}

/**
 * A run's chip: its number as wide as it is drawn with room either side,
 * and the area that takes the pointer at least target px wide.
 */
function chipOf(
  index: number,
  x: number,
  measure: Measure,
  target: number,
): Chip {
  const pill =
    Math.ceil(measure((index + 1).toString(), "label")) + 2 * CHIP_PAD;
  return { index, x, width: Math.max(target, pill + CHIP_GAP), pill };
}

/**
 * Where each part of a label from left, its top at top, is drawn: a line
 * each, its text's baseline in the line's middle, and the chips of its
 * runs one beside another after its text.
 */
function labelLines(
  parts: LabelPart[],
  left: number,
  top: number,
  measure: Measure,
  target: number,
): LabelLine[] {
  let at = top;
  return parts.map((part) => {
    const height = lineHeight(part, target);
    let x = left + Math.ceil(measure(labelLine(part), "label")) + CHIP_GAP;
    const chips = part.runs.map((index) => {
      const chip = chipOf(index, x, measure, target);
      x += chip.width;
      return chip;
    });
    const line = {
      top: at,
      height,
      y: at + (height + LINE) / 2 - DESCENT,
      chips,
    };
    at += height;
    return line;
  });
}

/** How wide a label of these parts is, a part to a line. */
function labelWidth(
  parts: LabelPart[],
  measure: Measure,
  target: number,
): number {
  return Math.max(
    0,
    ...labelLines(parts, 0, 0, measure, target).map((line, i) => {
      const last = line.chips.at(-1);
      return last
        ? last.x + last.width
        : Math.ceil(measure(labelLine(must(parts[i])), "label"));
    }),
  );
}

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("not in the graph");
  return value;
}
