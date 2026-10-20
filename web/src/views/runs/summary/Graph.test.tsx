/// <reference types="node" />
import { readFileSync } from "node:fs";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import busy from "../../../../fixtures/graph/attempts/batch-1/task-0301/record.json";
import early from "../../../../fixtures/graph/attempts/batch-1/task-0302/record.json";
import { must, sampleProject } from "../../../../fixtures/sample";
import { type Project, record } from "../../../api/types";
import { Graph } from "./Graph";
import { layout } from "./graph";

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

function show(json: unknown) {
  const drawn = must(layout(record.parse(json), project));
  const view = render(
    <MemoryRouter>
      <Graph
        project={project}
        layout={drawn}
        runHref={(i) => `/run/${String(i)}`}
      />
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

  it("zooms in and out with its buttons", () => {
    const { graph } = show(busy);
    const scale = () =>
      /scale\(([\d.]+)\)/.exec(
        must(graph.querySelector("g")).getAttribute("transform") ?? "",
      )?.[1];

    expect(scale()).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(scale()).toBe("1.25");
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(scale()).toBe("0.8");
  });

  it("offers full screen only where the browser does", () => {
    show(busy);

    // jsdom has no full screen.
    expect(screen.queryByRole("button", { name: "Full screen" })).toBeNull();
  });
});

describe("edges that share a lane", () => {
  // Dashes line up only when every edge in the lane starts where the others
  // do: at the state they go back to.
  it("start at the state they go back to, so their dashes stay dashes", () => {
    const { graph } = show(early);
    const back = [...graph.querySelectorAll('[data-taken="false"]')].filter(
      (g) => {
        const [from, to] = (g.getAttribute("data-edge") ?? "").split("\0");
        return to === "draft" && from !== "draft";
      },
    );
    const starts = back.map(
      (g) =>
        /^M[\d.]+ [\d.]+/.exec(
          g.querySelector("path")?.getAttribute("d") ?? "",
        )?.[0],
    );

    expect(back.length).toBeGreaterThan(3);
    expect(new Set(starts).size).toBe(1);
    for (const g of back)
      expect(g.querySelector("path")?.getAttribute("class")).toContain(
        "stroke-dasharray",
      );
  });

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
    for (const c of [...text, ...labels])
      expect(contrast(hex(c), hex("app"))).toBeGreaterThanOrEqual(4.5);
    for (const c of lines)
      expect(contrast(hex(c), hex("app"))).toBeGreaterThanOrEqual(3);
  });
});
