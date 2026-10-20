import { describe, expect, it } from "vitest";

import busy from "../../../../fixtures/graph/attempts/batch-1/task-0301/record.json";
import { must, records, sampleProject } from "../../../../fixtures/sample";
import { record } from "../../../api/types";
import { END, lanes, layout, takenCounts } from "./graph";

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

  it("puts the edges back to one state in one lane", () => {
    const drawn = must(layout(record.parse(busy), sampleProject));
    const laneOf = (to: string) =>
      new Set(
        drawn.edges.filter((e) => e.back && e.to === to).map((e) => e.label.y),
      );

    expect(drawn.edges.filter((e) => e.back && e.to === "draft").length).toBe(
      7,
    );
    expect(laneOf("draft").size).toBe(1);
    expect(laneOf("build").size).toBe(1);
    expect(laneOf("draft")).not.toEqual(laneOf("build"));
  });
});

describe("lanes", () => {
  it("puts a shorter span inside a longer one, and disjoint ones together", () => {
    expect(
      lanes([
        [0, 9],
        [1, 3],
        [4, 6],
        [2, 5],
      ]),
    ).toEqual([2, 0, 0, 1]);
  });
});
