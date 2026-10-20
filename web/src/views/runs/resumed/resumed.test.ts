import { describe, expect, it } from "vitest";

import { must, records } from "../../../../fixtures/sample";
import type { AttemptRecord, Resumption } from "../../../api/types";
import { carried, discardedAt, placeResumptions } from "./resumed";

const base = records.resumed;
const first = base.resumptions?.[0];
if (first === undefined) throw new Error("the fixture has no resumption");

function withResumptions(resumptions: Resumption[]): AttemptRecord {
  return { ...base, resumptions };
}

describe("where resumptions fall", () => {
  it("after their first kept visits, numbering the discarded across them", () => {
    const record = withResumptions([
      first,
      { ...first, kept: 3, carried_usd: 0 },
      { ...first, kept: 5 },
    ]);
    const places = placeResumptions(record);

    expect([...places.keys()]).toEqual([3, 5]);
    expect(places.get(3)?.map((p) => p.first)).toEqual([0, 1]);
    expect(places.get(5)?.map((p) => p.first)).toEqual([2]);
  });

  it("after the last visit when kept is past it", () => {
    const places = placeResumptions(withResumptions([{ ...first, kept: 99 }]));

    expect([...places.keys()]).toEqual([base.visits.length]);
  });

  it("nowhere in a record with none", () => {
    expect(placeResumptions(records.passed).size).toBe(0);
    expect(carried(records.passed)).toEqual({ usd: 0, count: 0 });
  });
});

describe("a discarded visit by its number", () => {
  it("counts across the resumptions in order", () => {
    const later = {
      ...first,
      discarded: [{ ...must(first.discarded[0]), visit: 7 }],
    };
    const record = withResumptions([first, later]);

    expect(discardedAt(record, 0)?.visit.visit).toBe(2);
    expect(discardedAt(record, 1)?.resumption).toBe(later);
    expect(discardedAt(record, 1)?.visit.visit).toBe(7);
    expect(discardedAt(record, 2)).toBeUndefined();
  });
});

describe("what was carried", () => {
  it("sums carried_usd over every resumption", () => {
    const record = withResumptions([first, { ...first, carried_usd: 1.5 }]);

    expect(carried(record)).toEqual({ usd: 1.87, count: 2 });
  });
});
