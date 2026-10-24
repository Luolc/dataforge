import { afterEach, describe, expect, it, vi } from "vitest";

import type { Column } from "../../api/types";
import {
  columnKey,
  loadHidden,
  loadRules,
  readQuery,
  saveHidden,
  saveRules,
  viewKey,
  writeQuery,
} from "./view";

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

describe("the home page's rules", () => {
  const FIELDS = ["status", "col.Path"];
  const path = { field: "col.Path", op: "any", values: ["alpha"] } as const;

  it("are kept with the rest of the view, and read back", () => {
    keep({ version: 1, hidden: ["rounds"], later: 1 });

    saveRules("sample", [{ ...path, values: ["alpha"] }]);

    expect(keptView()).toEqual({
      version: 1,
      hidden: ["rounds"],
      later: 1,
      rules: [path],
    });
    expect(loadRules("sample", FIELDS)).toEqual([path]);
    expect(loadHidden("sample", KEYS)).toEqual(["rounds"]);
  });

  it("are none for a view kept with none", () => {
    keep({ version: 1, hidden: ["rounds"] });
    expect(loadRules("sample", FIELDS)).toEqual([]);
    expect(loadRules("other", FIELDS)).toEqual([]);
  });

  it("drop a rule of a field the project no longer filters by, or not well formed", () => {
    keep({
      version: 1,
      rules: [
        { field: "col.Gone", op: "any", values: ["x"] },
        { field: "status", op: "all", values: ["passed"] },
        { field: "status", op: "none", values: [] },
        "status",
        { ...path, values: ["alpha", "alpha", 3] },
        { field: "col.Path", op: "none", values: ["beta"] },
      ],
    });
    // One rule per field: the first that holds.
    expect(loadRules("sample", FIELDS)).toEqual([path]);
  });

  it("are dropped with a view of another version, or one that does not parse", () => {
    keep({ version: 2, rules: [path] });
    expect(loadRules("sample", FIELDS)).toEqual([]);
    localStorage.setItem(viewKey("sample"), "{not json");
    expect(loadRules("sample", FIELDS)).toEqual([]);
  });
});

describe("the home page's URL", () => {
  const columns: Column[] = [
    { title: "Note", field: ["note"], format: "text" },
    { title: "Kept visit", field: [], format: "text", filter: true },
  ];
  const kept = "col.Kept visit";

  it("gives the rules as f. keys, each value encoded, a comma in it too", () => {
    const search = writeQuery(new URLSearchParams("q=a b"), [
      { field: "status", op: "none", values: ["~running", "too_easy"] },
      { field: kept, op: "any", values: ["a,b", "c d", "1"] },
    ]);

    expect(search).toBe(
      "?q=a+b&f.status=none:~running,too_easy&f.col.Kept%20visit=any:a%2Cb,c%20d,1",
    );
    const read = readQuery(search, columns);
    expect(read.rules).toEqual([
      { field: "status", op: "none", values: ["~running", "too_easy"] },
      { field: kept, op: "any", values: ["a,b", "c d", "1"] },
    ]);
    expect(read.others.toString()).toBe("q=a+b");
    expect(writeQuery(new URLSearchParams(), [])).toBe("");
  });

  it("reads a link of the older kind as the rules it stands for, a column by its place", () => {
    const read = readQuery(
      "?status=passed&column-1=x%2Cy&sort=-spent",
      columns,
    );
    expect(read.rules).toEqual([
      { field: "status", op: "any", values: ["passed"] },
      { field: kept, op: "any", values: ["x,y"] },
    ]);
    expect(read.others.toString()).toBe("sort=-spent");
  });

  it("drops a key for a field it does not filter by, with another op, or for a column not there", () => {
    const read = readQuery(
      "?f.col.Note=any:x&f.col.Gone=any:x&f.status=all:passed&f.status&column-0=x&column-7=x&older=1",
      columns,
    );
    expect(read.rules).toEqual([]);
    expect(read.others.toString()).toBe("older=1");
  });

  it("takes a field's f. key over its key of the older kind", () => {
    expect(
      readQuery("?status=passed&f.status=none:too_easy", columns).rules,
    ).toEqual([{ field: "status", op: "none", values: ["too_easy"] }]);
  });
});
