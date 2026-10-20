import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AttemptRow, Project } from "../../api/types";
import Home from "./index";
import { project, row, unreadable } from "./rows.test-data";

function must<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("missing");
  return value;
}

const at = (hour: number) =>
  `2026-03-02T${hour.toString().padStart(2, "0")}:00:00+00:00`;

const ROWS: AttemptRow[] = [
  row("t-run/1", { started_at: at(9), live: "running", status: null }),
  row("t-old/2", { started_at: at(8), ended_at: at(9), cost_usd: 2 }),
  row("t-old/1", {
    started_at: at(5),
    ended_at: at(6),
    cost_usd: 1.5,
    status: "exhausted",
    reason: "the budget ran out",
  }),
  row("t-new/1", {
    started_at: at(6),
    ended_at: at(11),
    cost_usd: 0.5,
    status: "too_easy",
  }),
  unreadable("t-bad/1"),
];

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(ROWS), { status: 200 })),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const location = useLocation();
  return (
    <output aria-label="where">{location.pathname + location.search}</output>
  );
}

function show(url = "/sample", shown: Project = project) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route
          path=":project"
          element={
            <>
              <Home project={shown} />
              <Where />
            </>
          }
        />
        <Route path=":project/attempt" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** The item IDs of a group, in the order shown. */
function itemsOf(title: string): string[] {
  const heading = screen.getByRole("button", { name: new RegExp(`^${title}`) });
  const body = document.getElementById(
    heading.getAttribute("aria-controls") ?? "",
  );
  if (!body) throw new Error(`no body for ${title}`);
  return [...body.querySelectorAll("tr:not([hidden]) td:nth-child(2) a")].map(
    (a) => a.textContent,
  );
}

describe("the home page", () => {
  it("groups items: not ended, then ended, then unreadable", async () => {
    show();
    await screen.findByText("t-run");

    expect(itemsOf("Not ended")).toEqual(["t-run"]);
    expect(itemsOf("Ended")).toEqual(["t-new", "t-old"]);
    expect(itemsOf("Unreadable record")).toEqual(["t-bad"]);
    expect(screen.getByRole("button", { name: /^Ended/ })).toHaveTextContent(
      "Ended1 of 2 bad or warn",
    );
    expect(screen.getByText("t-run").closest("tr")).toHaveTextContent(
      "Running",
    );
  });

  it("folds a group", async () => {
    show();
    const heading = await screen.findByRole("button", { name: /^Ended/ });

    act(() => {
      heading.click();
    });

    expect(heading).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("t-new")).not.toBeVisible();
    expect(screen.getByText("t-run")).toBeVisible();
  });

  it("sorts within the groups by a header, again to reverse", async () => {
    show();
    await screen.findByText("t-run");
    expect(itemsOf("Ended")).toEqual(["t-new", "t-old"]);
    const spent = screen.getByRole("button", { name: "Spent" });

    // A column of numbers sorts the largest first.
    act(() => {
      spent.click();
    });
    expect(itemsOf("Ended")).toEqual(["t-old", "t-new"]);
    expect(itemsOf("Not ended")).toEqual(["t-run"]);
    expect(screen.getByLabelText("where")).toHaveTextContent("?sort=-spent");

    act(() => {
      spent.click();
    });
    expect(itemsOf("Ended")).toEqual(["t-new", "t-old"]);
    expect(screen.getByLabelText("where")).toHaveTextContent("?sort=spent");
  });

  it("opens an item to its attempts, newest first", async () => {
    show();
    const open = await screen.findByRole("button", {
      name: "2 attempts of t-old",
    });
    const sub = (id: string) =>
      document.querySelector(
        `tr[id] a[href="/sample/attempt?id=${encodeURIComponent(id)}"]`,
      );
    expect(sub("t-old/2")).not.toBeVisible();

    act(() => {
      open.click();
    });

    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(itemsOf("Ended")).toEqual(["t-new", "t-old", "2", "1"]);
    expect(sub("t-old/2")).toBeVisible();
    expect(sub("t-old/1")).toHaveTextContent("1");
    // The item sums its attempts; a sub-row shows its own cost.
    const item = screen.getByText("t-old").closest("tr");
    expect(item).toHaveTextContent("$3.50");
  });

  it("filters by a status chip and says how many it hides", async () => {
    show();
    await screen.findByText("t-run");
    const chips = screen.getByRole("group", { name: "Filter by status" });
    expect(
      within(chips).getByRole("button", { name: /^All/ }),
    ).toHaveTextContent("All4");

    act(() => {
      within(chips)
        .getByRole("button", { name: /^Too easy/ })
        .click();
    });

    expect(screen.getByLabelText("where")).toHaveTextContent("status=too_easy");
    expect(itemsOf("Ended")).toEqual(["t-new"]);
    expect(screen.queryByRole("button", { name: /^Not ended/ })).toBeNull();
    expect(
      screen.getByText("3 of 4 hidden by the status filter."),
    ).toBeVisible();
  });

  it("searches item IDs from the URL", async () => {
    show("/sample?q=OLD");
    await screen.findByText("t-old");

    expect(screen.queryByText("t-run")).toBeNull();
    expect(screen.getByText("3 of 4 hidden by the search.")).toBeVisible();
  });

  it("reads the listing again only on refresh", async () => {
    show();
    await screen.findByText("t-run");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "Refresh" }).click();
    });

    await screen.findByText(/^Read /);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("opens an attempt when its row is clicked", async () => {
    show();
    const cell = await screen.findByText("t-new");

    act(() => {
      cell
        .closest("tr")
        ?.querySelector("td:nth-child(3)")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(screen.getByLabelText("where")).toHaveTextContent(
      "/sample/attempt?id=t-new%2F1",
    );
  });

  it("names both filters when both hide items", async () => {
    show("/sample?q=t-&status=too_easy");
    expect(
      await screen.findByText(
        "3 of 4 hidden by the search and the status filter.",
      ),
    ).toBeVisible();
  });

  it("shows the record's reason, or why it cannot be read, on focus", async () => {
    show();
    const label = (await screen.findByText("Exhausted")).closest("[tabindex]");
    if (!(label instanceof HTMLElement)) throw new Error("no focusable label");
    fireEvent.focus(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "the budget ran out",
    );
    fireEvent.blur(label);

    const unreadable = screen
      .getByText("t-bad")
      .closest("tr")
      ?.querySelector("[tabindex]");
    if (!(unreadable instanceof HTMLElement)) throw new Error("no label");
    fireEvent.focus(unreadable);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "the record: not found",
    );
  });

  it("titles each round by its state and shows its outcome", async () => {
    show();
    const cells = (await screen.findByText("t-new")).closest("tr");
    const round = cells?.querySelector("ol li");
    expect(round).toHaveTextContent("Pass@kPass");
  });

  it("shows a round with no outcome as No outcome once its attempt ended", async () => {
    const open = [{ round: 1, state: "solve", outcome: null }];
    const rows = [
      row("t-done/1", { status: null, rounds: open }),
      row("t-live/1", { status: null, rounds: open, live: "running" }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show();
    const roundOf = async (id: string) =>
      (await screen.findByText(id)).closest("tr")?.querySelector("ol li");

    expect(await roundOf("t-done")).toHaveTextContent("Pass@kNo outcome");
    expect(await roundOf("t-live")).toHaveTextContent("Pass@kNot ended");
  });

  it("draws the project's columns in their formats", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        { title: "Cap", field: ["budget", "max_usd"], format: "usd" },
        { title: "Seen", field: ["seen"], format: "time" },
        { title: "Note", field: ["note"], format: "text" },
      ],
    };
    ROWS[3] = { ...must(ROWS[3]), values: [15, at(7), null] };
    try {
      show("/sample", withColumns);
      const item = (await screen.findByText("t-new")).closest("tr");
      const cells = [...(item?.querySelectorAll("td") ?? [])].slice(-3);
      expect(cells.map((c) => c.textContent)).toEqual([
        "$15.00",
        "2026-03-02 07:00 UTC",
        "—",
      ]);
      expect(cells[0]).toHaveClass("text-right");
      expect(cells[2]).not.toHaveClass("text-right");
    } finally {
      ROWS[3] = { ...must(ROWS[3]), values: [] };
    }
  });

  it("shows each row's status as an icon first, a spinner while it runs", async () => {
    show();
    await screen.findByText("t-run");

    const first = (id: string) =>
      must(
        screen.getByText(id).closest("tr")?.querySelector("td") ?? undefined,
      );
    const running = within(first("t-run")).getByRole("img", {
      name: "Running",
    });
    const spinner = must(running.querySelector("svg") ?? undefined);
    expect(spinner).toHaveClass("lucide-loader-circle", "animate-spin");
    // It stands still when the reader asks for reduced motion (R243).
    expect(spinner).toHaveClass("motion-reduce:animate-none");

    const icon = (id: string, name: string) =>
      must(
        within(first(id)).getByRole("img", { name }).querySelector("svg") ??
          undefined,
      );
    expect(icon("t-new", "Too easy")).toHaveClass("lucide-circle-alert");
    expect(icon("t-old", "Passed")).toHaveClass("lucide-circle-check");
    expect(icon("t-bad", "Unreadable")).toHaveClass("lucide-circle-x");
    // The icon's box is the same target box as the item's link beside it,
    // so the two stand centred on one line.
    expect(running.parentElement).toHaveClass("target", "items-center");
    expect(
      within(
        screen.getByText("t-run").closest("tr") ?? document.body,
      ).getByRole("link", { name: "t-run" }),
    ).toHaveClass("target", "items-center");
  });
});
