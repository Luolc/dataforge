import { describe, expect, it } from "vitest";

import older from "../../fixtures/oldrec/attempts/older/task-0301/record.json";
import passed from "../../fixtures/attempts/batch-1/task-0001/record.json";
import resumed from "../../fixtures/attempts/resume/task-0401/record.json";
import {
  inReview,
  mayHaveStopped,
  records,
  runningIn,
} from "../../fixtures/sample";
import { attemptRow, currentOf, readRecord } from "./types";

const row = {
  id: "batch-1/task-1",
  item: "batch-1",
  modified: null,
  status: null,
  reason: null,
  cost_usd: null,
  started_at: null,
  ended_at: null,
  rounds: null,
  values: null,
  error: null,
};

describe("an attempt row", () => {
  it("keeps live, one of its words or null", () => {
    expect(attemptRow.parse({ ...row, live: "running" }).live).toBe("running");
    expect(attemptRow.parse({ ...row, live: null }).live).toBeNull();
    expect(attemptRow.parse(row).live).toBeUndefined();
  });

  it("refuses a live word it does not know", () => {
    expect(attemptRow.safeParse({ ...row, live: "stopped" }).success).toBe(
      false,
    );
  });
});

describe("reading a record file", () => {
  it("reads a record of the current shape or an older one", () => {
    expect(readRecord(passed).shape).toBe("record");
    expect(readRecord(older).shape).toBe("record");
  });

  it("keeps a resumed record's resumptions and an action's trace", () => {
    const read = readRecord(resumed);
    if (read.shape !== "record") throw new Error(read.why);

    expect(read.record.resumptions?.[0]).toMatchObject({
      kept: 3,
      carried_usd: 0.37,
      discarded: [{ state: "check", outcome: "raised", path: "discarded/1" }],
    });
    expect(
      readRecord({
        ...resumed,
        resumptions: [{ ...resumed.resumptions[0], kept: "3" }],
      }),
    ).toMatchObject({ shape: "other", why: /^resumptions\.0\.kept: / });
    expect(read.record.visits[0]?.actions[0]?.trace).toEqual({
      path: "runs/author-1.jsonl",
      format: "claude-code-stream-json",
    });
  });

  it("names the first key of another shape that does not fit", () => {
    expect(readRecord({ tone: "good" })).toMatchObject({
      shape: "other",
      why: "it has no visits",
    });
    expect(readRecord({ ...passed, status: 3 })).toMatchObject({
      shape: "other",
      why: "status: Invalid input: expected string, received number",
    });
  });

  it("refuses what is not a JSON object", () => {
    for (const raw of [null, [], "record", 1])
      expect(() => readRecord(raw)).toThrow("The record is not a JSON object.");
  });
});

describe("currentOf", () => {
  it("takes the visit after the record's last when the record fits", () => {
    expect(currentOf(inReview, runningIn())).toMatchObject({ state: "review" });
  });

  it("takes none for a record read before or after /live", () => {
    const older = { ...inReview, visits: inReview.visits.slice(0, 3) };

    expect(currentOf(older, runningIn())).toBeNull();
    expect(currentOf(inReview, runningIn({ recorded: true }))).toBeNull();
  });

  it("takes the record's last visit when it holds it with no outcome", () => {
    const live = runningIn({ state: "check", index: 1, recorded: true });

    expect(currentOf(records.running, live)).toMatchObject({ state: "check" });
  });

  it("takes none when /live has none", () => {
    expect(currentOf(inReview, mayHaveStopped)).toBeNull();
    expect(currentOf(inReview, null)).toBeNull();
  });
});
