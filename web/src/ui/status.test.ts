import { describe, expect, it } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import {
  attemptStatus,
  endWord,
  liveStatus,
  outcomeOf,
  statusOf,
} from "./status";

describe("status words", () => {
  it("the engine's raised and interrupted read as Exception and Run interrupted, any other unmapped word as itself", () => {
    expect(statusOf(sampleProject, "raised")).toEqual({
      label: "Exception",
      tone: "bad",
    });
    expect(statusOf(sampleProject, "interrupted")).toEqual({
      label: "Run interrupted",
      tone: "warn",
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
    expect(attemptStatus(sampleProject, records.running)).toBeNull();
    expect(attemptStatus(sampleProject, records.raised)).toBe("raised");
    const interrupted = {
      ...records.raised,
      visits: records.raised.visits.map((v, i, all) =>
        i === all.length - 1 ? { ...v, outcome: "interrupted" } : v,
      ),
    };
    expect(attemptStatus(sampleProject, interrupted)).toBe("interrupted");
    expect(endWord(sampleProject, interrupted)).toBeNull();
    expect(attemptStatus(sampleProject, records.passed)).toBe("passed");
  });

  it("a status the project names as not ended has not ended", () => {
    const running = { ...records.passed, status: "running" };
    const project = { ...sampleProject, not_ended: ["running"] };

    expect(attemptStatus(project, running)).toBeNull();
    expect(attemptStatus(sampleProject, running)).toBe("running");
    expect(attemptStatus(project, records.passed)).toBe("passed");
    expect(attemptStatus(project, records.running)).toBe("No status");
    expect(statusOf(project, "No status")).toEqual({
      label: "No status",
      tone: "neutral",
    });
  });

  it("a visit with no outcome is No outcome once its attempt has ended", () => {
    expect(outcomeOf(sampleProject, null, true)).toEqual({
      label: "No outcome",
      tone: "neutral",
    });
    expect(outcomeOf(sampleProject, null, false)).toEqual({
      label: "Not ended",
      tone: "neutral",
    });
    expect(outcomeOf(sampleProject, "passed", true)).toEqual(
      statusOf(sampleProject, "passed"),
    );
  });

  it("live words have the viewer's own labels and tones", () => {
    expect(liveStatus).toEqual({
      running: { label: "Running", tone: "neutral" },
      "may have stopped": { label: "May have stopped", tone: "warn" },
      "not ended": { label: "Not ended", tone: "neutral" },
    });
  });
});
