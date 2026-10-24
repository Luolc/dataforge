import { describe, expect, it } from "vitest";

import { attemptRow, type Project } from "../../api/types";
import type { Rule } from "../../ui/rule";
import {
  byRules,
  type Field,
  fieldCounts,
  filterItems,
  flagged,
  isEmptyAttempt,
  NO_STATUS_KEY,
  NO_VALUE,
  NOT_ENDED_KEY,
  notEnded,
  roundRuns,
  rowStatus,
  STATUS_FIELD,
  toItems,
  UNREADABLE_KEY,
  withHomeStatuses,
} from "./items";
import { project, row, unreadable } from "./rows.test-data";

const at = (hour: number) =>
  `2026-03-02T${hour.toString().padStart(2, "0")}:00:00+00:00`;

describe("whether an attempt has not ended", () => {
  it("is live not being null, whatever the record's status", () => {
    expect(notEnded(row("a/1", { status: "passed", live: "running" }))).toBe(
      true,
    );
    expect(notEnded(row("a/1", { status: null, live: "not ended" }))).toBe(
      true,
    );
    expect(notEnded(row("a/1", { status: null, live: null }))).toBe(false);
  });

  it("reads a listing without live as ended", () => {
    const old: Partial<ReturnType<typeof row>> = row("a/1", { status: null });
    delete old.live;
    expect(attemptRow.parse(old)).not.toHaveProperty("live");
    expect(notEnded(attemptRow.parse(old))).toBe(false);
  });
});

describe("isEmptyAttempt", () => {
  const empty = { status: null, rounds: [], started_at: null, ended_at: null };

  it("is an unreadable record, or one with no status and no visit", () => {
    expect(isEmptyAttempt(unreadable("t/1"))).toBe(true);
    expect(isEmptyAttempt(row("t/1", empty))).toBe(true);
    expect(isEmptyAttempt(row("t/1", { ...empty, held: false }))).toBe(true);
  });

  it("is never the attempt its item's held lock is for", () => {
    const holds = { held: true, holder: true };
    expect(isEmptyAttempt({ ...unreadable("t/1"), ...holds })).toBe(false);
    expect(isEmptyAttempt(row("t/1", { ...empty, ...holds }))).toBe(false);
  });

  it("is an attempt of an item whose lock is held for another", () => {
    const another = { held: true, holder: false };
    expect(isEmptyAttempt({ ...unreadable("t/1"), ...another })).toBe(true);
    expect(isEmptyAttempt(row("t/1", { ...empty, ...another }))).toBe(true);
  });

  it("is never an attempt that is live, has a status or has a visit", () => {
    expect(isEmptyAttempt(row("t/1", { ...empty, live: "not ended" }))).toBe(
      false,
    );
    expect(isEmptyAttempt(row("t/1", { ...empty, status: "error" }))).toBe(
      false,
    );
    expect(
      isEmptyAttempt(
        row("t/1", {
          ...empty,
          rounds: [{ round: 1, state: "solve", outcome: "raised" }],
        }),
      ),
    ).toBe(false);
    expect(isEmptyAttempt(row("t/1"))).toBe(false);
  });
});

describe("rowStatus", () => {
  it("is No status for an ended attempt with no status word", () => {
    expect(rowStatus(row("a/1", { status: null, live: null }))).toBe(
      NO_STATUS_KEY,
    );
    expect(rowStatus(row("a/1", { status: null, live: "not ended" }))).toBe(
      null,
    );
    const raised = [{ round: 1, state: "solve", outcome: "raised" }];
    expect(rowStatus(row("a/1", { status: null, rounds: raised }))).toBe(
      "raised",
    );
    const interrupted = [{ round: 1, state: "solve", outcome: "interrupted" }];
    expect(rowStatus(row("a/1", { status: null, rounds: interrupted }))).toBe(
      "interrupted",
    );
  });
});

describe("rowStatus with the store of runs", () => {
  it("is an interrupted row's word for how it was stopped, not May have stopped", () => {
    const stopped = {
      live: "may have stopped" as const,
      state: "interrupted" as const,
    };
    expect(rowStatus(row("a/1", { ...stopped, status: "canceled" }))).toBe(
      "canceled",
    );
    expect(rowStatus(row("a/1", { ...stopped, status: "interrupted" }))).toBe(
      "interrupted",
    );
    // A running row whose holder is gone, and a row of a project with a
    // lock, keep live's word.
    expect(
      rowStatus(
        row("a/1", {
          live: "may have stopped",
          state: "running",
          status: null,
        }),
      ),
    ).toBe("~may-have-stopped");
    expect(
      rowStatus(row("a/1", { live: "may have stopped", status: "running" })),
    ).toBe("~may-have-stopped");
  });
});

describe("toItems", () => {
  it("stands an item for its newest attempt by started_at", () => {
    const [item] = toItems([
      row("a/1", { started_at: at(9) }),
      row("a/3", { started_at: at(7) }),
      row("a/2", { started_at: at(11), status: "exhausted" }),
    ]);
    expect(item?.attempts.map((r) => r.id)).toEqual(["a/2", "a/1", "a/3"]);
    expect(item?.newest.id).toBe("a/2");
  });

  it("takes an attempt with no started_at yet as the newest", () => {
    const [item] = toItems([
      row("a/1", { started_at: at(9) }),
      row("a/2", { started_at: null, live: "running", status: null }),
    ]);
    expect(item?.newest.id).toBe("a/2");
    expect(item?.group).toBe("open");
  });

  it("takes an ended attempt with no started_at as older than one that has", () => {
    const [item] = toItems([
      row("a/1", { started_at: null, ended_at: null, status: "error" }),
      row("a/2", { started_at: at(9), ended_at: at(10), status: "too_easy" }),
    ]);
    expect(item?.newest.id).toBe("a/2");
    expect(item?.attempts.map((r) => r.id)).toEqual(["a/2", "a/1"]);
    expect(item?.group).toBe("ended");
  });

  it("orders not ended by start, ended by end, unreadable last", () => {
    const items = toItems([
      unreadable("z/1"),
      row("e1/1", { ended_at: at(10) }),
      row("o1/1", { started_at: at(8), live: "running" }),
      unreadable("y/1"),
      row("e2/1", { ended_at: at(12) }),
      row("o2/1", { started_at: at(9), live: "may have stopped" }),
    ]);
    expect(items.map((i) => [i.id, i.group])).toEqual([
      ["o2", "open"],
      ["o1", "open"],
      ["e2", "ended"],
      ["e1", "ended"],
      ["y", "unreadable"],
      ["z", "unreadable"],
    ]);
  });

  it("puts an item in the group of its newest attempt only", () => {
    const [item] = toItems([
      row("a/1", { started_at: at(8), live: "may have stopped" }),
      row("a/2", { started_at: at(9) }),
    ]);
    expect(item?.group).toBe("ended");
  });
});

describe("filterItems", () => {
  const items = toItems([
    row("alpha/1", { live: "running" }),
    row("alpha-2/1", { status: "too_easy" }),
    row("beta/1"),
    row("gamma/1", { live: "not ended", status: null }),
    row("delta/1", {
      status: null,
      rounds: [{ round: 1, state: "solve", outcome: "raised" }],
    }),
    unreadable("omega/1"),
  ]);
  const full = withHomeStatuses(project);
  const status = (op: "any" | "none", ...values: string[]): Rule => ({
    field: STATUS_FIELD,
    op,
    values,
  });

  it("searches item IDs, ignoring case, and counts what it hides", () => {
    const found = filterItems(full, items, " ALPHA ", []);
    expect(found.shown.map((i) => i.id)).toEqual(["alpha", "alpha-2"]);
    expect(found.fields[0]?.all).toBe(2);
    expect(found.hidden).toBe(4);
  });

  it("counts each status over the searched items, live words first", () => {
    const found = filterItems(full, items, "", []);
    expect(found.fields[0]).toEqual({
      field: "status",
      all: 6,
      values: [
        { key: "~running", count: 1 },
        { key: NOT_ENDED_KEY, count: 1 },
        { key: "passed", count: 1 },
        { key: "too_easy", count: 1 },
        { key: "raised", count: 1 },
        { key: UNREADABLE_KEY, count: 1 },
      ],
    });
    expect(filterItems(full, items, "alpha", []).fields[0]?.values).toEqual([
      { key: "~running", count: 1 },
      { key: "too_easy", count: 1 },
    ]);
  });

  it("keeps a status rule's items, and its value when search empties it", () => {
    const chosen = filterItems(full, items, "", [status("any", "too_easy")]);
    expect(chosen.shown.map((i) => i.id)).toEqual(["alpha-2"]);
    expect(chosen.hidden).toBe(5);

    const emptied = filterItems(full, items, "beta", [
      status("any", "too_easy"),
    ]);
    expect(emptied.shown).toEqual([]);
    expect(emptied.fields[0]?.values).toContainEqual({
      key: "too_easy",
      count: 0,
    });
  });

  it("counts the items of tone bad or warn", () => {
    expect(flagged(full, items)).toBe(3); // too_easy, raised, unreadable
  });

  it("gives No status a value of its own, apart from Not ended", () => {
    const more = toItems([
      row("a/1", { live: "not ended", status: null }),
      row("b/1", { status: null }),
    ]);
    expect(filterItems(full, more, "", []).fields[0]?.values).toEqual([
      { key: NOT_ENDED_KEY, count: 1 },
      { key: NO_STATUS_KEY, count: 1 },
    ]);
    expect(full.statuses[NO_STATUS_KEY]).toEqual({
      label: "No status",
      tone: "neutral",
    });
  });
});

describe("filterItems by the rules", () => {
  const kinds: Project = {
    ...project,
    columns: [
      { title: "Note", field: ["note"], format: "text" },
      {
        title: "Kind",
        field: [],
        format: "text",
        cases: ["second", "first"],
        labels: { first: "A first", zeta: "Zeta", alpha: "Alpha" },
        filter: true,
      },
    ],
  };
  const full = withHomeStatuses(kinds);
  const items = toItems([
    row("a/1", { values: ["x", "first"] }),
    row("b/1", { values: ["x", "second"] }),
    row("c/1", { values: ["x", "first"], status: "too_easy" }),
    row("d/1", { values: ["x", "zeta"] }),
    row("e/1", { values: ["x", "alpha"] }),
    // No case held: no value.
    row("f/1", { values: ["x", null] }),
  ]);
  const kind = (op: "any" | "none", ...values: string[]): Rule => ({
    field: "col.Kind",
    op,
    values,
  });
  const ids = (rules: Rule[], q = "") =>
    filterItems(full, items, q, rules).shown.map((i) => i.id);

  it("gives a field per column with filter, its values in the order of its cases, then by label, no value last", () => {
    const found = filterItems(full, items, "", []);
    expect(found.fields.map((f) => f.field)).toEqual(["status", "col.Kind"]);
    expect(found.fields[1]).toEqual({
      field: "col.Kind",
      all: 6,
      values: [
        { key: "second", count: 1 },
        { key: "first", count: 2 },
        { key: "alpha", count: 1 },
        { key: "zeta", count: 1 },
        { key: NO_VALUE, count: 1 },
      ],
    });
  });

  it("keeps an item whose value is any of a rule's values, or none of them", () => {
    expect(ids([kind("any", "first", "second")])).toEqual(["a", "b", "c"]);
    expect(ids([kind("none", "first", "second")])).toEqual(["d", "e", "f"]);
    expect(ids([kind("any", NO_VALUE)])).toEqual(["f"]);
    expect(ids([kind("none", NO_VALUE)])).toHaveLength(5);
  });

  it("keeps the items every rule keeps, and counts each field over what the other fields' rules keep", () => {
    const rules: Rule[] = [
      { field: STATUS_FIELD, op: "any", values: ["passed"] },
      kind("any", "first"),
    ];
    const found = filterItems(full, items, "", rules);
    expect(found.shown.map((i) => i.id)).toEqual(["a"]);
    expect(found.hidden).toBe(5);
    // The status values count what the Kind rule keeps.
    expect(found.fields[0]).toEqual({
      field: "status",
      all: 2,
      values: [
        { key: "passed", count: 1 },
        { key: "too_easy", count: 1 },
      ],
    });
    // The Kind values count what the Status rule keeps.
    expect(found.fields[1]?.all).toBe(5);
    expect(found.fields[1]?.values).toContainEqual({ key: "first", count: 1 });
  });

  it("keeps a chosen value when the rest leaves it none", () => {
    const found = filterItems(full, items, "b", [kind("any", "first", "zeta")]);
    expect(found.shown).toEqual([]);
    expect(found.fields[1]?.values).toEqual([
      { key: "second", count: 1 },
      { key: "first", count: 0 },
      { key: "zeta", count: 0 },
    ]);
  });

  it("filters rows of any kind, by fields of their own", () => {
    interface Plain {
      name: string;
      size: string;
    }
    const rows: Plain[] = [
      { name: "a", size: "big" },
      { name: "b", size: "small" },
      { name: "c", size: "big" },
    ];
    const fields: Field<Plain>[] = [
      {
        key: "size",
        valueOf: (r) => r.size,
        order: (x, y) => x.localeCompare(y),
      },
    ];
    const rules: Rule[] = [{ field: "size", op: "none", values: ["big"] }];
    expect(rows.filter((r) => byRules(r, fields, rules))).toEqual([rows[1]]);
    expect(fieldCounts(fields, rows, rules)).toEqual([
      {
        field: "size",
        all: 3,
        values: [
          { key: "big", count: 2 },
          { key: "small", count: 1 },
        ],
      },
    ]);
  });
});

describe("roundRuns", () => {
  const r = (round: number, outcome: string | null) => ({
    round,
    state: "solve",
    outcome,
  });

  it("puts rounds in a row with the same state and outcome in one run", () => {
    expect(
      roundRuns(
        [r(1, "fail"), r(2, "fail"), r(3, "pass"), r(4, "fail")],
        false,
      ),
    ).toEqual([
      { first: 1, last: 2, count: 2, state: "solve", outcome: "fail" },
      { first: 3, last: 3, count: 1, state: "solve", outcome: "pass" },
      { first: 4, last: 4, count: 1, state: "solve", outcome: "fail" },
    ]);
  });

  it("keeps the last round alone while it runs", () => {
    expect(roundRuns([r(1, null), r(2, null)], true)).toEqual([
      { first: 1, last: 1, count: 1, state: "solve", outcome: null },
      { first: 2, last: 2, count: 1, state: "solve", outcome: null },
    ]);
    expect(roundRuns([r(1, null), r(2, null)], false)).toHaveLength(1);
  });
});
