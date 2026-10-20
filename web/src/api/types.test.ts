import { describe, expect, it } from "vitest";

import { attemptRow } from "./types";

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
