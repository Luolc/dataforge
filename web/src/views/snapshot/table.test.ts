import { describe, expect, it } from "vitest";

import { filterRows, pageNumber, pageOf, statusWord } from "./table";
import type { Entries } from "./types";

const entries: Entries = {
  id_title: "Batch",
  columns: [
    { name: "state", title: "State", format: "status", filter: true },
    { name: "kind", title: "Kind", filter: true },
    { name: "files", title: "Files", format: "number" },
  ],
  rows: [
    { id: "batch-1", values: { state: "done", kind: "a", files: 1 } },
    { id: "batch-2", values: { state: "open", kind: "a", files: 2 } },
    { id: "batch-3", values: { state: "done", kind: "b", files: null } },
    { id: "other-4", values: { state: null, kind: "b" } },
  ],
};

describe("a step's rows", () => {
  it("counts each filter's chips over the search and the other filters alone", () => {
    const filtered = filterRows(entries, "batch", new Map([["kind", "a"]]));

    expect(filtered.shown.map((r) => r.id)).toEqual(["batch-1", "batch-2"]);
    expect(filtered.hidden).toBe(2);
    expect(filtered.searched).toBe(3);
    const [state, kind] = filtered.chips;
    expect(state).toMatchObject({
      all: 2,
      values: [
        { value: "done", count: 1 },
        { value: "open", count: 1 },
      ],
    });
    expect(kind).toMatchObject({
      all: 3,
      values: [
        { value: "a", count: 2 },
        { value: "b", count: 1 },
      ],
    });
  });

  it("searches the IDs whatever their case, and leaves a null out of the chips", () => {
    const filtered = filterRows(entries, "OTHER", new Map());

    expect(filtered.shown.map((r) => r.id)).toEqual(["other-4"]);
    expect(filtered.chips[0]?.values).toEqual([]);
  });

  it("holds a page to the pages there are, 50 rows each", () => {
    const rows = Array.from({ length: 120 }, (_, i) => ({
      id: i.toString(),
      values: {},
    }));

    expect(pageOf(rows, 2)).toMatchObject({ page: 2, pages: 3, from: 50 });
    expect(pageOf(rows, 2).rows).toHaveLength(50);
    expect(pageOf(rows, 7)).toMatchObject({ page: 3, from: 100 });
    expect(pageOf(rows, 7).rows).toHaveLength(20);
    expect(pageOf([], 4)).toMatchObject({ page: 1, pages: 1, from: 0 });
  });

  it("reads a page number that is not a whole number from 1 as the first", () => {
    for (const value of [null, "", "0", "-2", "1.5", "two"])
      expect(pageNumber(value)).toBe(1);
    expect(pageNumber("3")).toBe(3);
  });

  it("draws a status by the snapshot's words, one with none as itself, neutral", () => {
    const statuses = { done: { label: "Done", tone: "good" as const } };

    expect(statusWord(statuses, "done")).toEqual({
      label: "Done",
      tone: "good",
    });
    expect(statusWord(statuses, "open")).toEqual({
      label: "open",
      tone: "neutral",
    });
    expect(statusWord(undefined, "open").tone).toBe("neutral");
  });
});
