import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Project } from "../../api/types";
import { project as base, row } from "../home/rows.test-data";
import { AttemptTags } from "./tags";

const columns: Project["columns"] = [
  {
    title: "Language",
    field: ["language"],
    format: "text",
    labels: { py: "Python" },
    tag: true,
  },
  {
    title: "Kind",
    field: [],
    format: "text",
    cases: ["vuln"],
    labels: { vuln: "vuln" },
    hints: { vuln: "No principle." },
    tag: true,
  },
  { title: "Cost", field: ["cost"], format: "text" },
];
const project: Project = { ...base, columns };

function show(shown: Project, id: string) {
  const fetch = vi.fn(() =>
    Promise.resolve(
      new Response(
        JSON.stringify([
          row("a/1", { values: ["py", "vuln", "$1"] }),
          row("a/2", { values: ["py", null, "$2"], status: "exhausted" }),
        ]),
        { status: 200 },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <AttemptTags project={shown} attempt={id} />
    </MemoryRouter>,
  );
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("an attempt's tags", () => {
  it("shows the Language and the Kind of a column as the home table shows them", async () => {
    show(project, "a/1");

    expect(await screen.findByText("Python")).toBeVisible();
    expect(screen.getByText("vuln")).toBeVisible();
    expect(screen.getByText("Kind:")).toBeInTheDocument();
    expect(screen.queryByText("$1")).toBeNull();
  });

  it("draws no Kind for an attempt whose cases none holds", async () => {
    show(project, "a/2");

    expect(await screen.findByText("Python")).toBeVisible();
    expect(screen.queryByText(/Kind/)).toBeNull();
  });

  it("reads nothing when no column asks for a tag", () => {
    const fetch = show(
      { ...project, columns: columns.map((c) => ({ ...c, tag: false })) },
      "a/1",
    );

    expect(fetch).not.toHaveBeenCalled();
  });
});
