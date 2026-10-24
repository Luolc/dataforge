import { afterEach, describe, expect, it, vi } from "vitest";

import { columnKey, loadHidden, saveHidden, viewKey } from "./view";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

const KEYS = ["status", "rounds", columnKey("Cost"), columnKey("Kind")];

const keep = (v: unknown) => {
  localStorage.setItem(viewKey("sample"), JSON.stringify(v));
};
const keptView = (): unknown =>
  JSON.parse(localStorage.getItem(viewKey("sample")) ?? "null");

describe("the home page's view", () => {
  it("is kept per project, as the columns it hides", () => {
    saveHidden("sample", ["rounds", "col.Cost"]);

    expect(viewKey("sample")).toBe("viz.home.sample");
    expect(keptView()).toEqual({ version: 1, hidden: ["rounds", "col.Cost"] });
    expect(loadHidden("sample", KEYS)).toEqual(["rounds", "col.Cost"]);
    expect(loadHidden("other", KEYS)).toEqual([]);
  });

  it("drops a column the project file no longer has, and keeps the rest", () => {
    keep({ version: 1, hidden: ["col.Gone", "rounds", "col.Kind"] });

    expect(loadHidden("sample", KEYS)).toEqual(["rounds", "col.Kind"]);
  });

  it("keeps what else the view holds, its rules among them, when it saves", () => {
    const rules = [{ field: "status", op: "any", values: ["passed"] }];
    keep({ version: 1, hidden: ["status"], rules, later: { a: 1 } });

    saveHidden("sample", ["rounds"]);

    expect(keptView()).toEqual({
      version: 1,
      hidden: ["rounds"],
      rules,
      later: { a: 1 },
    });
  });

  it("drops a view of another version whole", () => {
    keep({ version: 2, hidden: ["rounds"], rules: [{ field: "status" }] });

    expect(loadHidden("sample", KEYS)).toEqual([]);
    saveHidden("sample", ["status"]);
    expect(keptView()).toEqual({ version: 1, hidden: ["status"] });
  });

  it("reads one it cannot parse as none", () => {
    localStorage.setItem(viewKey("sample"), "{not json");
    expect(loadHidden("sample", KEYS)).toEqual([]);
    keep({ version: 1, hidden: "rounds" });
    expect(loadHidden("sample", KEYS)).toEqual([]);
    keep([1, 2]);
    expect(loadHidden("sample", KEYS)).toEqual([]);
  });

  it("does without localStorage when it cannot be used", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(loadHidden("sample", KEYS)).toEqual([]);
    expect(() => {
      saveHidden("sample", ["rounds"]);
    }).not.toThrow();
  });
});
