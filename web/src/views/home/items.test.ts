import { describe, expect, it } from "vitest";

import { attemptRow } from "../../api/types";
import {
  filterItems,
  flagged,
  NOT_ENDED_KEY,
  notEnded,
  toItems,
  UNREADABLE,
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

  it("searches item IDs, ignoring case, and counts what it hides", () => {
    const found = filterItems(full, items, " ALPHA ", null);
    expect(found.shown.map((i) => i.id)).toEqual(["alpha", "alpha-2"]);
    expect(found.searched).toBe(2);
    expect(found.hidden).toBe(4);
  });

  it("counts each chip over the searched items, live words first", () => {
    const found = filterItems(full, items, "", null);
    expect(found.chips).toEqual([
      { key: "~running", count: 1 },
      { key: NOT_ENDED_KEY, count: 1 },
      { key: "passed", count: 1 },
      { key: "too_easy", count: 1 },
      { key: "raised", count: 1 },
      { key: UNREADABLE, count: 1 },
    ]);
    expect(filterItems(full, items, "alpha", null).chips).toEqual([
      { key: "~running", count: 1 },
      { key: "too_easy", count: 1 },
    ]);
  });

  it("keeps a chosen chip's items, and the chip when search empties it", () => {
    const chosen = filterItems(full, items, "", "too_easy");
    expect(chosen.shown.map((i) => i.id)).toEqual(["alpha-2"]);
    expect(chosen.hidden).toBe(5);

    const emptied = filterItems(full, items, "beta", "too_easy");
    expect(emptied.shown).toEqual([]);
    expect(emptied.chips).toContainEqual({ key: "too_easy", count: 0 });
  });

  it("counts the items of tone bad or warn", () => {
    expect(flagged(full, items)).toBe(3); // too_easy, raised, unreadable
  });
});
