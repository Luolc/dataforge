import { describe, expect, it } from "vitest";

import { must, records, sampleProject } from "../../../../fixtures/sample";
import { END, layout, takenCounts } from "./graph";

describe("the graph", () => {
  it("counts the transitions the visits took", () => {
    expect(Object.fromEntries(takenCounts(records.passed))).toEqual({
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
    expect(node("check")).toMatchObject({ tone: "bad", lastVisit: 5 });
    expect(node(END)).toMatchObject({ sub: "Exhausted", tone: "bad" });
    // The last visit went back to author, but the attempt ended there.
    expect(takenCounts(records.exhausted).get("check\0fail")).toBe(2);
  });

  it("leaves the end unreached while the attempt runs", () => {
    const drawn = must(layout(records.running, sampleProject));

    expect(drawn.nodes.at(-1)).toMatchObject({
      idle: true,
      sub: "Not run",
    });
  });
});
