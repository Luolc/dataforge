import { describe, expect, it } from "vitest";

import type { Project } from "../api/types";
import { attemptsPage, firstPath, projectCrumbs } from "./pages";

const base = {
  id: "my project",
  name: "Example",
  color: "#e5a54b",
  states: {},
  statuses: {},
  item: { depth: 1, title: "Batch" },
  columns: [],
  session: { thinking: "hidden" },
} satisfies Project;

const withPages = {
  ...base,
  pages: [
    { kind: "snapshot", name: "steps", title: "Steps", roots: [] },
    { kind: "attempts", name: null, title: "Attempts", roots: [] },
  ],
} satisfies Project;

const ok = (data: Project) => ({ state: "ok" as const, data });

describe("a project's pages", () => {
  it("opens the first page, or the home page with none", () => {
    expect(firstPath(withPages)).toBe("/my%20project/steps");
    expect(firstPath(base)).toBe("/my%20project");
  });

  it("puts the attempts page's title after the project's name", () => {
    expect(
      projectCrumbs(ok(withPages), "my project", attemptsPage, true),
    ).toEqual([
      { label: "Projects", to: "/" },
      { label: "Example", to: "/my%20project/steps" },
      { label: "Attempts" },
    ]);
    expect(
      projectCrumbs(ok(withPages), "my project", attemptsPage, false).at(-1),
    ).toEqual({ label: "Attempts", to: "/my%20project" });
  });

  it("keeps the crumbs of a project with no pages as they were", () => {
    expect(projectCrumbs(ok(base), "my project", attemptsPage, true)).toEqual([
      { label: "Projects", to: "/" },
      { label: "Example" },
    ]);
    expect(projectCrumbs(ok(base), "my project", attemptsPage, false)).toEqual([
      { label: "Projects", to: "/" },
      { label: "Example", to: "/my%20project" },
    ]);
  });
});
