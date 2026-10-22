/// <reference types="node" />
import { readFileSync } from "node:fs";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import busy from "../../../../fixtures/graph/attempts/batch-1/task-0301/record.json";
import early from "../../../../fixtures/graph/attempts/batch-1/task-0302/record.json";
import { measured, must, sampleProject } from "../../../../fixtures/sample";
import { type Project, record } from "../../../api/types";
import { TEMPLATES } from "../../../ui/template";
import { Graph } from "./Graph";
import { END, type Layout, layout, TEXT_WIDTH } from "./graph";

// As fixtures/graph/project.yaml, as /api/projects would serve it.
const project: Project = {
  ...sampleProject,
  id: "graph-sample",
  states: {
    draft: { title: "Draft" },
    smoke: { title: "Smoke test" },
    recheck: { title: "Regrade check" },
    solve: { title: "Pass@k" },
  },
  statuses: {
    ...sampleProject.statuses,
    pass: { label: "Pass", tone: "good" },
    regrade: { label: "Regrade", tone: "warn" },
  },
};

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function show(json: unknown) {
  const drawn = must(layout(record.parse(json), project));
  const view = render(
    <MemoryRouter>
      <Graph
        project={project}
        layout={drawn}
        runHref={(i) => `/run/${String(i)}`}
      />
      <Where />
    </MemoryRouter>,
  );
  const graph = screen.getByRole("group", { name: "State machine" });
  // An edge's key holds a NUL, which a CSS selector cannot.
  const edge = (from: string, to: string) =>
    must(
      [...graph.querySelectorAll("[data-edge]")].find(
        (g) => g.getAttribute("data-edge") === `${from}\0${to}`,
      ),
    );
  const node = (key: string) =>
    must(graph.querySelector(`[data-node="${key}"]`));
  return { ...view, graph, edge, node };
}

const lit = (el: Element) => el.getAttribute("data-lit");
const where = () => screen.getByTestId("where").textContent;
const scaleOf = (graph: Element) =>
  /scale\(([\d.]+)\)/.exec(
    must(graph.querySelector("g")).getAttribute("transform") ?? "",
  )?.[1];

afterEach(() => {
  vi.restoreAllMocks();
});

// jsdom lays nothing out: give every element a size, as a browser would.
function frameOf(width: number, height: number) {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(width);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(
    height,
  );
}

// A press, a move of dx and a release on el, as a drag.
function drag(el: Element, dx: number) {
  fireEvent.pointerDown(el, { button: 0, clientX: 100, clientY: 50 });
  fireEvent.pointerMove(el, { clientX: 100 + dx, clientY: 50 });
  fireEvent.pointerUp(el, { clientX: 100 + dx, clientY: 50 });
}

describe("the machine's graph", () => {
  it("lights a hovered state's edges and dims the rest", () => {
    const { edge, node } = show(busy);

    fireEvent.pointerEnter(node("check"));

    expect(lit(edge("smoke", "check"))).toBe("true");
    expect(lit(edge("check", "review"))).toBe("true");
    expect(lit(edge("check", "draft"))).toBe("true");
    expect(lit(edge("draft", "build"))).toBe("false");
    expect(edge("draft", "build").getAttribute("class")).toContain(
      "opacity-[var(--opacity-idle)]",
    );
    expect(node("build").innerHTML).toContain("opacity-[var(--opacity-idle)]");
    expect(node("smoke").innerHTML).not.toContain(
      "opacity-[var(--opacity-idle)]",
    );

    fireEvent.pointerLeave(node("check"));
    expect(lit(edge("check", "review"))).toBe("false");
    expect(node("build").innerHTML).not.toContain(
      "opacity-[var(--opacity-idle)]",
    );
  });

  it("lights a stub with the state it leaves and the state it names", () => {
    const { graph, edge, node } = show(busy);
    // An edge's key is its two ends with a NUL between; the end's own key
    // starts with one.
    const stubs = [...graph.querySelectorAll('[data-stub="true"]')].map(
      (g): [string, string] => {
        const key = must(g.getAttribute("data-edge"));
        const i = key.indexOf("\0");
        return [key.slice(0, i), key.slice(i + 1)];
      },
    );
    // A stub back to another state, and a state at neither of its ends.
    const [from, to] = must(stubs.find(([, t]) => t !== END));
    const other = must(
      busy.graph.states
        .map((st) => st.name)
        .find((k) => k !== from && k !== to),
    );

    fireEvent.pointerEnter(node(to));
    expect(lit(edge(from, to))).toBe("true");
    fireEvent.pointerLeave(node(to));
    fireEvent.pointerEnter(node(from));
    expect(lit(edge(from, to))).toBe("true");
    fireEvent.pointerLeave(node(from));
    fireEvent.pointerEnter(node(other));
    expect(lit(edge(from, to))).toBe("false");
    expect(edge(from, to).getAttribute("class")).toContain(
      "opacity-[var(--opacity-idle)]",
    );
  });

  it("lights a focused state, but a hovered one wins over it", () => {
    const { edge, node } = show(busy);

    fireEvent.focus(node("check"));
    expect(lit(edge("check", "review"))).toBe("true");

    fireEvent.pointerEnter(node("draft"));
    expect(lit(edge("check", "review"))).toBe("false");
    expect(lit(edge("draft", "build"))).toBe("true");

    // The pointer leaving gives the light back to the focus.
    fireEvent.pointerLeave(node("draft"));
    expect(lit(edge("check", "review"))).toBe("true");
  });

  it("links a state to its last run, named by its status, runs and time", () => {
    const { graph } = show(busy);

    const check = within(graph).getByRole("link", {
      name: "check, Pass, 4 runs, 12m 0s: open its last run",
    });
    expect(check).toHaveAttribute("href", "/run/25");
  });

  it("opens at scale 1 in a frame narrower than the graph", () => {
    frameOf(600, 300);
    const { graph } = show(busy);

    expect(scaleOf(graph)).toBe("1");
  });

  it("zooms in and out with its buttons", () => {
    const { graph } = show(busy);
    const scale = () => scaleOf(graph);

    expect(scale()).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(scale()).toBe("1.25");
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(scale()).toBe("0.8");
  });

  it("does not open a state a drag ends on, but opens the next one tapped", () => {
    // jsdom has no pointer capture.
    Element.prototype.setPointerCapture = () => undefined;
    const { node } = show(busy);

    drag(node("check"), 40);
    fireEvent.click(node("check"));
    expect(where()).toBe("/");

    fireEvent.pointerDown(node("check"), { button: 0, clientX: 100 });
    fireEvent.pointerUp(node("check"), { clientX: 100 });
    fireEvent.click(node("check"));
    expect(where()).toBe("/run/25");
  });

  it("opens the state tapped after a touch drag, which brings no click", () => {
    Element.prototype.setPointerCapture = () => undefined;
    const { node } = show(busy);

    drag(node("check"), 40);
    fireEvent.pointerDown(node("draft"), { button: 0, clientX: 100 });
    fireEvent.pointerUp(node("draft"), { clientX: 100 });
    fireEvent.click(node("draft"));
    expect(where()).toBe("/run/22");
  });

  it("offers full screen only where the browser does", () => {
    show(busy);

    // jsdom has no full screen.
    expect(screen.queryByRole("button", { name: "Full screen" })).toBeNull();
  });
});

describe("edges not taken", () => {
  it("are drawn under the taken ones, lit or not", () => {
    const { graph, edge, node } = show(busy);
    // Draft's edges back include ones not taken, now lit.
    fireEvent.pointerEnter(node("draft"));
    expect(lit(edge("smoke", "draft"))).toBe("true");
    expect(edge("smoke", "draft").getAttribute("data-taken")).toBe("false");
    const order = [...graph.querySelectorAll("[data-edge]")].map(
      (g) => g.getAttribute("data-taken") === "true",
    );

    expect(order.indexOf(true)).toBeGreaterThan(0);
    expect(order.slice(order.indexOf(true))).not.toContain(false);
  });
});

// Tests run from web/.
const theme = readFileSync("src/ui/theme.css", "utf8");
const hex = (name: string) =>
  must(new RegExp(`--color-${name}:\\s*(#[0-9a-f]{6});`).exec(theme)?.[1]);
// WCAG 2 contrast ratio of two opaque colours.
const contrast = (a: string, b: string) => {
  const luminance = (c: string) => {
    const [r = 0, g = 0, b = 0] = [1, 3, 5].map((i) => {
      const v = parseInt(c.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi = 0, lo = 0] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const colors = (el: Element, kind: "fill" | "stroke") =>
  [
    ...(el.getAttribute("class") ?? "").matchAll(
      new RegExp(`(?:^|\\s)${kind}-([a-z0-9-]+)`, "g"),
    ),
  ]
    .map((m) => m[1] ?? "")
    .filter((c) => !/^(none|\d|\[)/.test(c));

describe("what was not run or not taken, at rest", () => {
  it("marks a state not run with Lucide's dashed circle, drawn by no hand", () => {
    const { node } = show(early);
    const icon = node("check").querySelector("svg.lucide-circle-dashed");

    expect(icon).not.toBeNull();
    expect(icon?.getAttribute("class")).toContain("text-icon");
  });

  it("has text at least 4.5:1 and lines at least 3:1 on the graph", () => {
    const { graph, node } = show(early);
    const text = [...node("check").querySelectorAll("text")].flatMap((t) =>
      colors(t, "fill"),
    );
    const lines = [
      ...graph.querySelectorAll('[data-taken="false"] path'),
      ...node("check").querySelectorAll("rect"),
    ].flatMap((p) => colors(p, "stroke"));
    const labels = [
      ...graph.querySelectorAll('[data-taken="false"] tspan'),
    ].flatMap((t) => colors(t, "fill"));

    expect(text.length).toBeGreaterThan(0);
    expect(lines.length).toBeGreaterThan(0);
    // A state not run is filled with app, and the graph is drawn on card.
    for (const c of text)
      expect(contrast(hex(c), hex("app"))).toBeGreaterThanOrEqual(4.5);
    for (const c of labels)
      expect(contrast(hex(c), hex("card"))).toBeGreaterThanOrEqual(4.5);
    for (const c of lines)
      for (const ground of ["app", "card"])
        expect(contrast(hex(c), hex(ground))).toBeGreaterThanOrEqual(3);
  });
});

// Each colour template's value of a token: the root's, else contrast's.
const templateHex = (template: string, name: string) => {
  const block =
    new RegExp(`html\\[data-template="${template}"\\] \\{([^}]*)\\}`).exec(
      theme,
    )?.[1] ?? "";
  return (
    new RegExp(`--color-${name}:\\s*(#[0-9a-f]{6});`).exec(block)?.[1] ??
    hex(name)
  );
};

describe("the graph's ground", () => {
  it("is the card's body, as the Deliverables table's is, not its head", () => {
    const { graph } = show(early);
    const frame = must(graph.parentElement);

    expect(frame).toHaveClass("bg-card");
    expect(frame).not.toHaveClass("bg-app");
    for (const { id: template } of TEMPLATES)
      expect(templateHex(template, "card")).not.toBe(
        templateHex(template, "head"),
      );
  });
});

describe("a running attempt's machine", () => {
  const smoke = {
    state: "smoke",
    visit: 1,
    index: 2,
    recorded: true,
    since: "2026-03-09T13:16:40+00:00",
  };

  let rerenderWith: (drawn: Layout) => void = () => undefined;

  function showRunning() {
    const drawn = must(layout(record.parse(early), project, smoke));
    const shown = (at: Layout) => (
      <MemoryRouter>
        <Graph project={project} layout={at} runHref={() => "/run"} />
      </MemoryRouter>
    );
    const view = render(shown(drawn));
    rerenderWith = (at) => {
      view.rerender(shown(at));
    };
    const graph = screen.getByRole("group", { name: "State machine" });
    const x = Number(
      /translate\((-?[\d.]+)/.exec(
        must(graph.querySelector("g")).getAttribute("transform") ?? "",
      )?.[1],
    );
    return { drawn, graph, x };
  }

  it("opens with the state it runs in the middle of the frame", () => {
    frameOf(240, 300);
    const { drawn, x } = showRunning();
    const node = must(drawn.nodes.find((n) => n.key === "smoke"));

    expect(x).toBeLessThan(0);
    expect(x + (node.x + node.width / 2)).toBeCloseTo(120);
  });

  it("keeps its view when the attempt goes on to another state", () => {
    frameOf(240, 300);
    const { graph, x } = showRunning();
    const on = {
      ...smoke,
      state: "solve",
      index: 3,
      recorded: false,
      since: null,
    };
    const later = must(layout(record.parse(early), project, on));
    // The state it runs in now would sit elsewhere in the frame.
    const solve = must(later.nodes.find((n) => n.key === "solve"));
    expect(120 - (solve.x + solve.width / 2)).not.toBeCloseTo(x);

    rerenderWith(later);

    expect(
      within(
        must(graph.querySelector<HTMLElement>('[data-node="solve"]')),
      ).getByRole("img", { name: "Running" }),
    ).toBeInTheDocument();
    expect(must(graph.querySelector("g")).getAttribute("transform")).toMatch(
      new RegExp(`^translate\\(${String(x)} 0\\)`),
    );
  });

  it("keeps each line of a card within it, a running state's time on a line of its own", () => {
    // Draft runs again, its second visit not recorded yet.
    const draft = {
      state: "draft",
      visit: 2,
      index: 3,
      recorded: false,
      since: "2026-03-09T13:16:40+00:00",
    };
    const drawn = must(layout(record.parse(early), project, draft, measured));
    const { container } = render(
      <MemoryRouter>
        <Graph project={project} layout={drawn} runHref={() => "/run"} />
      </MemoryRouter>,
    );
    const linesOf = (key: string) => {
      const card = must(
        [...container.querySelectorAll("[data-node]")].find(
          (g) => g.getAttribute("data-node") === key,
        ),
      );
      // A tspan with no y of its own goes on along its line, as SVG draws it.
      return [...card.querySelectorAll("text")].flatMap((text) => {
        const font = (text.getAttribute("class") ?? "").includes(
          "font-semibold",
        )
          ? ("title" as const)
          : ("sub" as const);
        const spans = [...text.querySelectorAll("tspan")];
        if (spans.length === 0)
          return [
            { text: text.textContent, y: Number(text.getAttribute("y")), font },
          ];
        const lines: { text: string; y: number; font: typeof font }[] = [];
        for (const span of spans) {
          const y = span.getAttribute("y");
          const last = lines.at(-1);
          if (y === null && last) last.text += span.textContent;
          else lines.push({ text: span.textContent, y: Number(y), font });
        }
        return lines;
      });
    };

    for (const n of drawn.nodes)
      for (const line of linesOf(n.key)) {
        expect(measured(line.text, line.font)).toBeLessThanOrEqual(TEXT_WIDTH);
        // Its baseline in the card, room left under it for the descenders.
        expect(line.y).toBeGreaterThan(0);
        expect(line.y).toBeLessThanOrEqual(n.height - 4);
      }
    const under = linesOf("draft")
      .filter((l) => l.font === "sub")
      .map((l) => l.text);
    expect(under).toEqual([
      "2 runs · Running",
      expect.stringMatching(/^\d+h \d+m$/),
    ]);
  });

  it("opens at its left end when no state runs", () => {
    frameOf(600, 300);
    const { graph } = show(early);

    expect(must(graph.querySelector("g")).getAttribute("transform")).toMatch(
      /^translate\(0 0\)/,
    );
  });

  it("turns the running state's icon, standing still for reduced motion", () => {
    frameOf(600, 300);
    const { graph } = showRunning();
    const node = must(graph.querySelector<HTMLElement>('[data-node="smoke"]'));
    const icon = within(node).getByRole("img", {
      name: "Running",
    });

    expect(icon.querySelector("svg")).toHaveClass(
      "animate-spin",
      "motion-reduce:animate-none",
    );
  });
});
