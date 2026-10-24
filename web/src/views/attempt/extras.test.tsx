import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Project } from "../../api/types";
import { project as base, row } from "../home/rows.test-data";
import { HeaderExtras } from "./extras";

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

function show(shown: Project, id: string, answer?: Response) {
  const fetch = vi.fn(() =>
    Promise.resolve(
      answer ??
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
      <HeaderExtras project={shown} attempt={id} />
    </MemoryRouter>,
  );
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("an attempt's heading tags", () => {
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

  it("says so when the listing cannot be read, instead of drawing no tag", async () => {
    show(
      project,
      "a/1",
      new Response(JSON.stringify({ error: "the store is down" }), {
        status: 500,
      }),
    );

    expect(await screen.findByText(/cannot be read/)).toBeVisible();
    expect(screen.queryByText("Python")).toBeNull();
  });

  it("says so when the listing has no row for the attempt", async () => {
    show(project, "a/9");

    expect(await screen.findByText(/no row for this attempt/)).toBeVisible();
  });

  it("draws no tag when no column asks for one", async () => {
    show(
      { ...project, columns: columns.map((c) => ({ ...c, tag: false })) },
      "a/1",
    );

    expect(await screen.findByRole("button")).toBeVisible();
    expect(screen.queryByText("Python")).toBeNull();
  });
});

describe("the menu of the item's attempts", () => {
  const rows = () => [
    row("a/1", {
      item: "a",
      started_at: "2026-03-01T09:00:00+00:00",
      status: "passed",
    }),
    row("a/2", {
      item: "a",
      started_at: "2026-03-03T09:00:00+00:00",
      status: "too_easy",
    }),
    row("b/1", { item: "b", status: "exhausted", values: ["py", null, "$1"] }),
  ];
  const open = async () => {
    const button = await screen.findByRole("button", {
      name: "2 attempts of this task",
    });
    fireEvent.pointerDown(button, { button: 0, ctrlKey: false });
    return screen.findByRole("menu");
  };

  it("lists the other attempts of the item, newest first, each with its status, and links them", async () => {
    show(project, "a/1", new Response(JSON.stringify(rows()), { status: 200 }));

    const menu = await open();

    const links = within(menu).getAllByRole("menuitem");
    expect(links.map((l) => l.textContent)).toEqual([
      expect.stringMatching(/^2.*Too easy$/),
      expect.stringMatching(/^1.*Passed$/),
    ]);
    expect(links[0]).toHaveTextContent("2026-03-03");
    expect(links[1]).toHaveTextContent("2026-03-01");
    expect(links[0]).toHaveAttribute("href", "/sample/attempt?id=a%2F2");
    expect(links[1]).toHaveAttribute("aria-current", "page");
    expect(links[0]).not.toHaveAttribute("aria-current");
    expect(within(menu).queryByText("exhausted")).toBeNull();
  });

  it("draws no button for an item that has the attempt alone", async () => {
    show(project, "b/1", new Response(JSON.stringify(rows()), { status: 200 }));

    expect(await screen.findByText("Python")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
