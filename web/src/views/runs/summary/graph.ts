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
import { type Font, type Measure, widest } from "./measure";

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
export const LINE = 14;
const DESCENT = 4;
/** How far a loop reaches out of its card, and how tall it is. */
const LOOP = 30;
export const LOOP_WIDTH = 28;
/** How far a loop's label keeps from the loop. */
const LOOP_GAP = 6;
/**
 * The narrowest a label's lines are broken to so that the graph fits its
 * frame, half a card's width; a piece of its text wider than that keeps a
 * line of its own.
 */
export const LABEL_LEAST = CARD_WIDTH / 2;

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
   * Each of the state's runs, in the order it ran them, the run it runs now
   * included: its index in visits and its outcome; empty for none.
   */
  runs: NodeRun[];
  /**
   * What on the card names how many times the state ran, at the start of
   * its first line under the title: "3 runs", or, while it runs, "Run 3";
   * null when it ran once or not at all.
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
   * The parts from x, the last line's baseline at y, and the lines their
   * text is drawn on.
   */
  label: {
    x: number;
    y: number;
    parts: LabelPart[];
    lines: LabelLine[];
  };
}

/**
 * A line of a label: its top, its text's baseline, the index in parts of
 * the part it is of, and the part's text on it. A part starts on a line of
 * its own.
 */
interface LabelLine {
  part: number;
  text: string;
  top: number;
  y: number;
}

export interface Layout {
  width: number;
  height: number;
  /** Whether the attempt has ended: no transition but those taken is left. */
  ended: boolean;
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
 * in (currentOf), drawn as running, and its labels' lines broken to make it
 * at most room wide where they can be; null when the record has none.
 */
export function layout(
  record: AttemptRecord,
  project: Project,
  current: Current | null = null,
  measure: Measure = widest,
  scope: Scope = "all",
  room = Infinity,
): Layout | null {
  const graph = record.graph;
  if (!graph) return null;
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
    const mine = record.visits.filter((v) => v.state === key);
    const visits = record.visits.flatMap((v, index): NodeRun[] =>
      v.state === key
        ? [
            {
              ...outcomeOf(project, v.outcome, attemptEnded),
              index,
              recorded: true,
            },
          ]
        : [],
    );
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
      const text = `Run ${runs.toString()}`;
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
        sub: `Run ${runs.toString()} of ${runs.toString()}`,
        time: null,
        under: [],
        status: liveStatus.running,
        idle: false,
        lastVisit: current.recorded
          ? current.index
          : last
            ? record.visits.lastIndexOf(last)
            : null,
        runs: [
          ...visits.slice(0, done.length),
          {
            index: current.recorded ? current.index : record.visits.length,
            ...liveStatus.running,
            recorded: current.recorded,
          },
        ],
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
      runs: visits,
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

  const others = [...groups.entries()].filter(([, g]) => g.from !== g.to);
  // The drawing with each label's lines at most wrap wide where they can
  // be, and those lines by the group's key. A card is tall enough for its
  // title's lines and those under them, and for its loop's label.
  const place = (wrap: number) => {
    const lines = new Map(
      [...groups.entries()].map(([key, g]) => [
        key,
        labelLines(g.parts, wrap, measure),
      ]),
    );
    const linesOf = (key: string) => must(lines.get(key));
    const drawing = layered(
      nodes.map((n) => {
        const loop = lines.get(`${n.key}\0${n.key}`);
        return {
          key: n.key,
          width: n.width,
          height: Math.max(
            textHeight(n),
            loop ? loop.length * LINE + LOOP_WIDTH : 0,
          ),
          beside: loop ? LOOP + LOOP_GAP + linesWidth(loop, measure) : 0,
        };
      }),
      others.map(([key, g]) => ({
        key,
        from: g.from,
        to: g.to,
        stub: stubbed.has(key),
        label: {
          width: linesWidth(linesOf(key), measure),
          height: linesOf(key).length * LINE,
        },
      })),
    );
    return { drawing, linesOf };
  };
  // Where the graph is wider than room, its labels' lines are made as
  // narrow as it takes to fit, down to LABEL_LEAST: the widest wrap from
  // there that fits, found by halving, or LABEL_LEAST where none does.
  let placed = place(Infinity);
  const widestLabel = Math.max(
    0,
    ...[...groups.keys()].map((key) =>
      linesWidth(placed.linesOf(key), measure),
    ),
  );
  if (placed.drawing.width > room && widestLabel > LABEL_LEAST) {
    let fits = place(LABEL_LEAST);
    let lo = LABEL_LEAST;
    let hi = Math.ceil(widestLabel);
    while (fits.drawing.width <= room && hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      const tried = place(mid);
      if (tried.drawing.width <= room) {
        fits = tried;
        lo = mid;
      } else hi = mid;
    }
    placed = fits;
  }
  const drawn = placed.drawing;
  for (const node of nodes) {
    const at = must(drawn.boxes.get(node.key));
    node.x = at.x;
    node.y = at.y;
    node.width = at.width;
    node.height = at.height;
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
      const own = placed.linesOf(key);
      const lines = moved(own, node.y + (node.height - own.length * LINE) / 2);
      return {
        ...common,
        path: `M${x} ${a} C ${x + LOOP} ${a}, ${x + LOOP} ${b}, ${x + 3} ${b}`,
        label: {
          x: x + LOOP + LOOP_GAP,
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
    const lines = moved(placed.linesOf(key), r.label.y);
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
 * A part's line of an edge's label: its outcome's label, how many times the
 * attempt took it and, for a stub, where it goes.
 */
export function labelLine(part: LabelPart): string {
  const taken = part.taken > 0 ? ` ×${part.taken.toString()}` : "";
  const to = part.to === null ? "" : ` → ${part.to}`;
  return `${part.label}${taken}${to}`;
}

/**
 * A part's text in pieces that a line may break between: its words, a
 * "·" or "→" kept with the word after it and the words in parentheses
 * kept together.
 */
function pieces(text: string): string[] {
  const out: string[] = [];
  let open = false;
  for (const word of text.split(" ")) {
    const last = out.at(-1);
    if (last !== undefined && (open || last === "·" || last === "→"))
      out[out.length - 1] = `${last} ${word}`;
    else out.push(word);
    if (word.startsWith("(")) open = true;
    if (word.endsWith(")")) open = false;
  }
  return out;
}

/**
 * Where each part of a label is drawn, from 0 down: its text on as many
 * lines as it needs to be at most room wide, a piece too wide for a line
 * on one of its own, each line LINE tall.
 */
function labelLines(
  parts: LabelPart[],
  room: number,
  measure: Measure,
): LabelLine[] {
  const lines: LabelLine[] = [];
  parts.forEach((part, p) => {
    const mine: string[] = [];
    for (const piece of pieces(labelLine(part))) {
      const last = mine.at(-1);
      const text = last === undefined ? piece : `${last} ${piece}`;
      if (last !== undefined && Math.ceil(measure(text, "label")) <= room)
        mine[mine.length - 1] = text;
      else mine.push(piece);
    }
    for (const text of mine) {
      const top = lines.length * LINE;
      lines.push({ part: p, text, top, y: top + LINE - DESCENT });
    }
  });
  return lines;
}

/** Moves a label's lines from 0 down to top. */
function moved(lines: LabelLine[], top: number): LabelLine[] {
  return lines.map((line) => ({
    ...line,
    top: line.top + top,
    y: line.y + top,
  }));
}

/** How wide a label's lines are. */
function linesWidth(lines: LabelLine[], measure: Measure): number {
  return Math.max(
    0,
    ...lines.map((line) => Math.ceil(measure(line.text, "label"))),
  );
}

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("not in the graph");
  return value;
}
