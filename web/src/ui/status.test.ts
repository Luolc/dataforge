import { describe, expect, it } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import { attemptStatus, statusOf } from "./status";

describe("status words", () => {
  it("an unmapped raised is bad, any other unmapped word neutral", () => {
    expect(statusOf(sampleProject, "raised")).toEqual({
      label: "raised",
      tone: "bad",
    });
    expect(statusOf(sampleProject, "skipped")).toEqual({
      label: "skipped",
      tone: "neutral",
    });
  });

  it("the project file's entry wins, raised included", () => {
    const project = {
      ...sampleProject,
      statuses: { raised: { label: "Crashed", tone: "warn" as const } },
    };

    expect(statusOf(project, "raised")).toEqual({
      label: "Crashed",
      tone: "warn",
    });
  });

  it("no status is Not ended, neutral", () => {
    expect(statusOf(sampleProject, null)).toEqual({
      label: "Not ended",
      tone: "neutral",
    });
    expect(attemptStatus(records.running)).toBeNull();
    expect(attemptStatus(records.raised)).toBe("raised");
    expect(attemptStatus(records.passed)).toBe("passed");
  });
});
