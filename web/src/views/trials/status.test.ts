import { describe, expect, it } from "vitest";
import { trialStatus } from "./status";

describe("trialStatus", () => {
  describe("with reward and exception", () => {
    it("reward 1 + exception: shows solved with exception marker", () => {
      const result = trialStatus({ reward: 1, exception: "some error" }, false);
      expect(result).toEqual({
        word: "solved",
        tone: "good",
        exception: true,
      });
    });

    it("reward 0 + exception: shows not solved with exception marker", () => {
      const result = trialStatus({ reward: 0, exception: "some error" }, false);
      expect(result).toEqual({
        word: "not solved",
        tone: "bad",
        exception: true,
      });
    });
  });

  describe("with reward only", () => {
    it("reward 1: shows solved", () => {
      const result = trialStatus({ reward: 1, exception: null }, false);
      expect(result).toEqual({
        word: "solved",
        tone: "good",
        exception: false,
      });
    });

    it("reward 0: shows not solved", () => {
      const result = trialStatus({ reward: 0, exception: null }, false);
      expect(result).toEqual({
        word: "not solved",
        tone: "bad",
        exception: false,
      });
    });
  });

  describe("with exception only (no reward)", () => {
    it("reward null + exception: shows exception", () => {
      const result = trialStatus(
        { reward: null, exception: "some error" },
        false,
      );
      expect(result).toEqual({
        word: "exception",
        tone: "warn",
        exception: false,
      });
    });
  });

  describe("with neither reward nor exception", () => {
    it("reward null + no exception: shows no reward", () => {
      const result = trialStatus({ reward: null, exception: null }, false);
      expect(result).toEqual({
        word: "no reward",
        tone: "neutral",
        exception: false,
      });
    });
  });

  describe("with verdict", () => {
    it("verdict overrides reward/exception", () => {
      const result = trialStatus(
        { reward: 1, exception: "some error" },
        false,
        { label: "custom verdict", tone: "good" },
      );
      expect(result).toEqual({
        word: "custom verdict",
        tone: "good",
        exception: true,
      });
    });
  });

  describe("running trials", () => {
    it("running trial with no result: shows running", () => {
      const result = trialStatus(null, true);
      expect(result).toEqual({
        word: "running",
        tone: "neutral",
        exception: false,
      });
    });
  });

  describe("not finished trials", () => {
    it("trial with no result and not running: shows not finished", () => {
      const result = trialStatus(null, false);
      expect(result).toEqual({
        word: "not finished",
        tone: "neutral",
        exception: false,
      });
    });
  });
});
