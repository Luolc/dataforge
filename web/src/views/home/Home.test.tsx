import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POLL_MS } from "../../api/poll";
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
  const heading = screen.getByRole("button", {
    name: new RegExp(`^${title}\\d`),
  });
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
    show("/sample?older=1");
    await screen.findByText("t-run");

    expect(itemsOf("Not ended")).toEqual(["t-run"]);
    expect(itemsOf("Ended")).toEqual(["t-new", "t-old"]);
    expect(itemsOf("Unreadable record")).toEqual(["t-bad"]);
    expect(screen.getByRole("button", { name: /^Ended\d/ })).toHaveTextContent(
      "Ended1 of 2 bad or warn",
    );
    expect(screen.getByText("t-run").closest("tr")).toHaveTextContent(
      "Running",
    );
  });

  it("folds a group", async () => {
    show();
    const heading = await screen.findByRole("button", { name: /^Ended\d/ });

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
  });

  it("shows an item's newest attempt's cost, with what all its attempts spent on focus", async () => {
    show();
    const item = (await screen.findByText("t-old")).closest("tr");
    if (!item) throw new Error("no row");
    // Its newest attempt, t-old/2, as the row's other cells are.
    expect(item).toHaveTextContent("$2.00");
    expect(item).not.toHaveTextContent("$3.50");

    const spent = within(item).getByText("$2.00").closest("[tabindex]");
    if (!(spent instanceof HTMLElement)) throw new Error("no hint");
    fireEvent.focus(spent);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "$3.50 spent over its 2 attempts.",
    );
    // An item of one attempt has no sum to give.
    const one = screen.getByText("t-new").closest("tr");
    expect(within(one ?? document.body).getByText("$0.50")).not.toHaveAttribute(
      "tabindex",
    );
  });

  it("says on focus that a cost is its runs' when its record has no budget", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify([
          row("t-runs/1", { cost_usd: 10.77, cost_from: "runs" }),
          row("t-budget/1", { cost_usd: 0.5, cost_from: "budget" }),
        ]),
        { status: 200 },
      ),
    );
    show();
    const item = (await screen.findByText("t-runs")).closest("tr");
    if (!item) throw new Error("no row");

    const spent = within(item).getByText("$10.77").closest("[tabindex]");
    if (!(spent instanceof HTMLElement)) throw new Error("no hint");
    fireEvent.focus(spent);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "From its runs; its record has no budget.",
    );
    const budget = screen.getByText("t-budget").closest("tr");
    expect(
      within(budget ?? document.body).getByText("$0.50"),
    ).not.toHaveAttribute("tabindex");
  });

  it("sorts an item by its newest attempt's cost, not by its attempts' sum", async () => {
    // a's newest spent $2.00 and its two attempts $5.00; b's one $3.00.
    const rows = [
      row("a/2", { started_at: at(8), ended_at: at(9), cost_usd: 2 }),
      row("a/1", { started_at: at(5), ended_at: at(6), cost_usd: 3 }),
      row("b/1", { started_at: at(6), ended_at: at(7), cost_usd: 3 }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show("/sample?sort=-spent");
    await screen.findByText("a");
    expect(itemsOf("Ended")).toEqual(["b", "a"]);
  });

  it("filters by a status chip and says how many it hides", async () => {
    show("/sample?older=1");
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
      screen.getByText("3 of 4 Tasks hidden by the status filter."),
    ).toBeVisible();
  });

  it("searches item IDs from the URL", async () => {
    show("/sample?q=OLD&older=1");
    await screen.findByText("t-old");

    expect(screen.queryByText("t-run")).toBeNull();
    expect(
      screen.getByText("3 of 4 Tasks hidden by the search."),
    ).toBeVisible();
  });

  it("reads the listing again on refresh", async () => {
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

  describe("a column the page filters by", () => {
    const kinds: Project = {
      ...project,
      columns: [
        {
          title: "Kind",
          field: [],
          format: "text",
          cases: ["first", "second"],
          labels: { first: "First" },
          filter: true,
        },
      ],
    };
    const rows: AttemptRow[] = [
      row("t-a/1", { values: ["first"] }),
      row("t-b/1", { values: ["second"] }),
      row("t-c/1", { values: [null] }),
    ];
    beforeEach(() => {
      vi.stubGlobal(
        "fetch",
        vi.fn(() =>
          Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
        ),
      );
    });

    it("filters by a value's chip, says it hides items, and shows all again", async () => {
      show("/sample", kinds);
      const none = (await screen.findByText("t-c")).closest("tr");
      // No case held: the cell is empty, with no dash.
      expect(none?.querySelector("td:last-child")?.textContent).toBe("");
      const chips = screen.getByRole("group", { name: "Filter by Kind" });
      expect(
        within(chips)
          .getAllByRole("button")
          .map((b) => b.textContent),
      ).toEqual(["All3", "First1", "second1"]);

      act(() => {
        within(chips)
          .getByRole("button", { name: /^First/ })
          .click();
      });

      expect(screen.getByLabelText("where")).toHaveTextContent(
        "column-0=first",
      );
      expect(itemsOf("Ended")).toEqual(["t-a"]);
      expect(
        screen.getByText("2 of 3 Tasks hidden by the Kind filter."),
      ).toBeVisible();

      act(() => {
        screen.getByRole("button", { name: "Show all" }).click();
      });

      expect(screen.getByLabelText("where")).not.toHaveTextContent("column-0");
      expect(itemsOf("Ended")).toHaveLength(3);
    });

    it("names every filter that hides items", async () => {
      show("/sample?q=t-&status=passed&column-0=first", kinds);
      expect(
        await screen.findByText(
          "2 of 3 Tasks hidden by the search, the status filter and the Kind filter.",
        ),
      ).toBeVisible();
    });
  });

  it("names both filters when both hide items", async () => {
    show("/sample?q=t-&status=too_easy&older=1");
    expect(
      await screen.findByText(
        "3 of 4 Tasks hidden by the search and the status filter.",
      ),
    ).toBeVisible();
  });

  it("says Stopped, with its job, for an attempt that may have stopped and whose job has ended", async () => {
    const rows = [
      row("t-gone/1", {
        status: null,
        live: "may have stopped",
        held: false,
        stopped: { job: 12, state: "failed" },
      }),
      row("t-maybe/1", {
        status: null,
        live: "may have stopped",
        held: false,
        stopped: null,
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show();
    const gone = (await screen.findByText("t-gone")).closest("tr");
    if (!gone) throw new Error("no row");

    expect(within(gone).getByRole("img", { name: "Stopped" })).toHaveClass(
      "text-warn",
    );
    const label = within(gone).getByText("Stopped").closest("[tabindex]");
    if (!(label instanceof HTMLElement)) throw new Error("no focusable label");
    fireEvent.focus(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Job 12, which ran this attempt, ended Failed; nothing runs it now.",
    );
    expect(screen.getByText("t-maybe").closest("tr")).toHaveTextContent(
      "May have stopped",
    );
    expect(screen.getByRole("button", { name: /^Stopped/ })).toHaveTextContent(
      "Stopped1",
    );
  });

  it("with the store of runs, shows an interrupted attempt's word, what a reconcile has yet to do and when its cost was read, on focus", async () => {
    const note = "its machine ended passed; a reconcile has not ended it yet";
    const rows: AttemptRow[] = [
      row("t-stop/1", {
        live: "may have stopped",
        state: "interrupted",
        status: "canceled",
        note,
        cost_usd: 4,
        progress_at: "2026-03-02T09:30:00+00:00",
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show("/sample", {
      ...project,
      statuses: {
        ...project.statuses,
        canceled: { label: "Canceled", tone: "warn" },
      },
      runs: { stopped_by: { person: "canceled" } },
    });

    const line = must(
      (await screen.findByText("t-stop")).closest("tr") ?? undefined,
    );
    const label = within(line).getByText("Canceled").closest("[tabindex]");
    if (!(label instanceof HTMLElement)) throw new Error("no focusable label");
    fireEvent.focus(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(note);
    fireEvent.blur(label);
    await waitFor(() => {
      expect(screen.queryByRole("tooltip")).toBeNull();
    });
    const cost = within(line).getByText("$4.00").closest("[tabindex]");
    if (!(cost instanceof HTMLElement)) throw new Error("no hint");
    fireEvent.focus(cost);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/^Read /);
  });

  it("shows the record's reason, or why it cannot be read, on focus", async () => {
    show("/sample?older=1");
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

  it("shows a round with no outcome as No outcome once its attempt ended, Running while it runs", async () => {
    const open = [{ round: 1, state: "solve", outcome: null }];
    const rows = [
      row("t-done/1", { status: null, rounds: open }),
      row("t-live/1", { status: null, rounds: open, live: "running" }),
      row("t-lost/1", {
        status: null,
        rounds: open,
        live: "may have stopped",
      }),
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

    expect(await roundOf("t-done")).toHaveTextContent("R1Pass@kNo outcome");
    const live = await roundOf("t-live");
    expect(live).toHaveTextContent(/^R1Pass@kRunning$/);
    expect(live?.querySelector("svg")).toHaveClass(
      "lucide-loader-circle",
      "motion-reduce:animate-none",
    );
    // Only an attempt that runs now has a round that runs.
    expect(await roundOf("t-lost")).toHaveTextContent("R1Pass@kNot ended");
    // Each round on one line, as its height is one line: Not / ended
    // folded over two ran into the round below it.
    expect(await roundOf("t-lost")).toHaveClass("h-5", "whitespace-nowrap");
  });

  it("numbers each round, and puts rounds in a row that ended alike in one cell with their count", async () => {
    const rounds = [
      { round: 1, state: "solve", outcome: "exhausted" },
      { round: 2, state: "solve", outcome: "exhausted" },
      { round: 3, state: "solve", outcome: "exhausted" },
      { round: 4, state: "solve", outcome: "pass" },
      { round: 5, state: "solve", outcome: "exhausted" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify([row("t-many/1", { rounds })]), {
            status: 200,
          }),
        ),
      ),
    );
    show();
    const cells = (await screen.findByText("t-many"))
      .closest("tr")
      ?.querySelectorAll("ol li");

    expect([...(cells ?? [])].map((c) => c.textContent)).toEqual([
      "R1–R3Pass@kExhausted×3",
      "R4Pass@kPass",
      "R5Pass@kExhausted",
    ]);
  });

  it("gives each attempt's end in a column of its own, none while it has not ended", async () => {
    show();
    await screen.findByText("t-run");
    const headings = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headings.slice(5, 9)).toEqual([
      "Spent",
      "Started",
      "Ended",
      "Duration",
    ]);
    const endOf = (id: string) =>
      screen.getByText(id).closest("tr")?.querySelector("td:nth-child(8)");
    expect(endOf("t-new")).toHaveTextContent("2026-03-02 11:00 UTC");
    expect(endOf("t-new")).toHaveClass("text-right", "tabular-nums");
    // t-run's listing has an ended_at, its last action's end, but it runs.
    expect(endOf("t-run")).toHaveTextContent(/^—$/);
  });

  it("says what the chips count, on focus", async () => {
    show("/sample?older=1");
    const all = await screen.findByRole("button", { name: /^All/ });
    fireEvent.focus(all);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/^4 Tasks$/);
  });

  it("keeps the status icon, the item and a group's heading at the left as the table scrolls sideways", async () => {
    show();
    const item = (await screen.findByText("t-old")).closest("tr");
    const [icon, id, status] = [...(item?.querySelectorAll("td") ?? [])];
    expect(icon).toHaveClass("sticky", "left-0", "bg-panel");
    // Beside the icon's column, as wide as it is for a fine or a coarse
    // pointer, and edged on its right.
    expect(icon).toHaveClass("w-row-icon");
    expect(id).toHaveClass(
      "sticky",
      "left-row-icon",
      "bg-panel",
      "shadow-stuck",
    );
    expect(status).not.toHaveClass("sticky");
    // On the row's surface as a pointer hovers it, and a sub-row's.
    expect(icon).toHaveClass("group-hover/row:bg-hover");
    expect(item).toHaveClass("group/row");
    const sub = document.querySelector(
      `tr[id] a[href="/sample/attempt?id=${encodeURIComponent("t-old/1")}"]`,
    );
    expect(sub?.closest("td")).toHaveClass("sticky", "bg-app");
    const heads = screen.getAllByRole("columnheader");
    expect(heads[0]).toHaveClass("sticky", "left-0", "bg-head");
    expect(heads[0]).toHaveClass("w-row-icon");
    expect(heads[1]).toHaveClass("sticky", "left-row-icon", "bg-head");
    expect(screen.getByRole("button", { name: /^Ended\d/ })).toHaveClass(
      "sticky",
      "left-1",
    );
  });

  it("shows a round's interrupted as Run interrupted, an attempt's status as the project file names it", async () => {
    const cut = [{ round: 1, state: "solve", outcome: "interrupted" }];
    const rows = [row("t-cut/1", { status: "interrupted", rounds: cut })];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show("/sample", {
      ...project,
      statuses: {
        ...project.statuses,
        interrupted: { label: "Cut short", tone: "neutral" },
      },
    });
    const cells = must(
      (await screen.findByText("t-cut")).closest("tr") ?? undefined,
    );

    expect(cells.querySelector("ol li")).toHaveTextContent(
      "R1Pass@kRun interrupted",
    );
    expect(within(cells).getByText("Cut short")).toBeVisible();
  });

  it("scrolls a table wider than the page sideways on its own, under the filters", async () => {
    show();
    const table = (await screen.findByText("t-run")).closest("table");

    expect(table?.parentElement).toHaveClass("overflow-x-auto");
    expect(table?.parentElement).not.toContainElement(
      screen.getByRole("group", { name: "Filter by status" }),
    );
  });

  it("says above a table wider than the page that it scrolls sideways", async () => {
    show();
    const box = (await screen.findByText("t-run")).closest(
      "table",
    )?.parentElement;
    if (!box) throw new Error("no box");
    Object.defineProperty(box, "scrollWidth", { value: 1600 });
    Object.defineProperty(box, "clientWidth", { value: 1100 });

    fireEvent.scroll(box);

    expect(
      screen.getByRole("button", { name: "Scroll the table right" }),
    ).toHaveAttribute("aria-controls", box.id);
    expect(
      screen.getByText("The table is wider than the page; scroll it sideways."),
    ).toBeVisible();
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

  it("shows a column's value by its label, and one with no label as it is", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        {
          title: "Tone",
          field: ["tone"],
          format: "text",
          labels: { bug_report: "Bug report", true: "Kept" },
        },
        {
          title: "Kept",
          field: ["kept"],
          format: "text",
          labels: { true: "Kept" },
        },
      ],
    };
    ROWS[3] = { ...must(ROWS[3]), values: ["bug_report", true] };
    ROWS[1] = { ...must(ROWS[1]), values: ["chat_message", false] };
    try {
      show("/sample", withColumns);
      const cellsOf = async (id: string) =>
        [
          ...((await screen.findByText(id))
            .closest("tr")
            ?.querySelectorAll("td") ?? []),
        ]
          .slice(-2)
          .map((c) => c.textContent);
      expect(await cellsOf("t-new")).toEqual(["Bug report", "Kept"]);
      expect(await cellsOf("t-old")).toEqual(["chat_message", "false"]);
    } finally {
      ROWS[3] = { ...must(ROWS[3]), values: [] };
      ROWS[1] = { ...must(ROWS[1]), values: [] };
    }
  });

  it("sorts a column's values by their labels, as they show", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        {
          title: "Verdict",
          field: ["verdict"],
          format: "text",
          labels: {
            aa_reject: "Rejected",
            zz_accept: "Accepted",
            true: "Kept",
          },
        },
      ],
    };
    const rows = [
      row("a/1", { started_at: at(5), ended_at: at(6), values: ["aa_reject"] }),
      row("b/1", { started_at: at(6), ended_at: at(7), values: ["zz_accept"] }),
      row("c/1", { started_at: at(7), ended_at: at(8), values: [true] }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(rows), { status: 200 })),
      ),
    );
    show("/sample?sort=column-0", withColumns);
    await screen.findByText("a");

    // A to Z by Accepted, Kept, Rejected, not by the values themselves.
    expect(itemsOf("Ended")).toEqual(["b", "c", "a"]);
  });

  it("says what a column holds on its heading's focus", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        {
          title: "Cap",
          field: ["budget", "max_usd"],
          format: "usd",
          description: "The most this attempt may spend.",
        },
        { title: "Note", field: ["note"], format: "text" },
      ],
    };
    show("/sample", withColumns);
    const cap = await screen.findByRole("button", { name: "Cap" });
    expect(cap.firstElementChild).toHaveClass("decoration-dotted");
    fireEvent.focus(cap);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "The most this attempt may spend.",
    );
    // A column with no description has no hint, nor its sign.
    const note = screen.getByRole("button", { name: "Note" });
    expect(note.firstElementChild).not.toHaveClass("decoration-dotted");
  });

  it("tells a column's file not there from its value null", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        { title: "A", field: ["a"], format: "text" },
        { title: "B", field: ["b"], format: "text" },
        { title: "C", field: ["c"], format: "text" },
        { title: "D", field: ["d"], format: "usd" },
      ],
    };
    ROWS[3] = {
      ...must(ROWS[3]),
      values: [null, null, null, 2],
      reads: ["not run", "missing", "found", "found"],
    };
    try {
      show("/sample", withColumns);
      const item = (await screen.findByText("t-new")).closest("tr");
      const cells = [...(item?.querySelectorAll("td") ?? [])].slice(-4);
      expect(cells.map((c) => c.textContent)).toEqual([
        "Not run",
        "—",
        "Not given",
        "$2.00",
      ]);
      // A word of the viewer's, kept on one line and set apart from values.
      expect(cells[0]?.firstElementChild).toHaveClass("whitespace-nowrap");
      const given = cells[2]?.querySelector(".text-fg-3");
      expect(given).toHaveClass("decoration-dotted");
      fireEvent.focus(must(given?.closest("[tabindex]") ?? undefined));
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        "Its file gives null for this value.",
      );
    } finally {
      ROWS[3] = { ...must(ROWS[3]), values: [], reads: null };
    }
  });

  it("says in a column of a tab not run how the tab's job stands", async () => {
    const withColumns: Project = {
      ...project,
      columns: [
        { title: "A", field: ["a"], format: "text" },
        { title: "B", field: ["b"], format: "text" },
        { title: "C", field: ["c"], format: "text" },
        { title: "D", field: ["d"], format: "text" },
      ],
    };
    const job = {
      id: 98,
      queue: "slow",
      labels: { action: "note", item: "t-new" },
      state: "canceled",
      memory_mib: 1,
      queued_at: 1772442900,
      started_at: null,
      ended_at: 1772443200,
      exit_code: null,
      cancel_requested_at: 1772443200,
    };
    const pause = { queue: "slow", paused_at: 1772442000, reason: null };
    const failed = { ...job, id: 99, state: "failed", exit_code: 1 };
    ROWS[3] = {
      ...must(ROWS[3]),
      values: [null, null, null, null],
      reads: ["not run", "not run", "not run", "not run"],
      queued: [
        { job, paused: pause, reason: null },
        { job: failed, paused: null, reason: null },
        { job: null, paused: pause, reason: null },
        { job: null, paused: null, reason: null },
      ],
    };
    try {
      show("/sample", withColumns);
      const item = (await screen.findByText("t-new")).closest("tr");
      const cells = [...(item?.querySelectorAll("td") ?? [])].slice(-4);
      // A tab its job never made is not run, whatever the job's state (L40).
      expect(cells.map((c) => c.textContent)).toEqual([
        "Not run · job 98 canceled · queue paused",
        "Not run · job 99 failed",
        "Not run · queue paused",
        "Not run",
      ]);
    } finally {
      ROWS[3] = { ...must(ROWS[3]), values: [], reads: null, queued: null };
    }
  });

  it("shows each row's status as an icon first, a spinner while it runs", async () => {
    // With older records shown, so the unreadable one has its row too.
    show("/sample?older=1");
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
    // In the colour of Running's tone, as every other status icon (L38).
    expect(running).toHaveClass("text-neutral");

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

describe("the home page's empty attempts", () => {
  const empty = { status: null, rounds: [], started_at: null, ended_at: null };
  const OLDER: AttemptRow[] = [
    row("t-new/1", { started_at: at(6), ended_at: at(11), status: "too_easy" }),
    // An item with an attempt that has a status and two older records, one
    // of them the newest.
    row("t-mixed/3", { ...empty, modified: at(12) }),
    row("t-mixed/2", { started_at: at(7), ended_at: at(8) }),
    unreadable("t-mixed/1"),
    // Items with older records only.
    row("t-empty/1", empty),
    unreadable("t-gone/1"),
    // An unreadable attempt its item's held lock is for: it may have just
    // started; and an older one of that item, which stays hidden.
    { ...unreadable("t-started/2"), held: true, holder: true },
    { ...unreadable("t-started/1"), held: true, holder: false },
  ];

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(OLDER), { status: 200 })),
      ),
    );
  });

  const ids = () =>
    // The item is in the second cell: the first holds its status icon.
    [...document.querySelectorAll("tbody td:nth-child(2) a")].map(
      (a) => a.textContent,
    );

  it("hides them, and says how many", async () => {
    show();
    await screen.findByText("t-new");

    expect(ids()).toEqual(["t-new", "t-mixed", "t-started"]);
    expect(screen.getByText("5 older attempts hidden (4 Tasks)")).toBeVisible();
    expect(screen.getByRole("button", { name: "Show" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("takes an item's newest from the attempts it shows", async () => {
    show();
    const item = (await screen.findByText("t-mixed")).closest("tr");

    expect(item).toHaveTextContent("Passed");
    expect(item).not.toHaveTextContent("No status");
  });

  it("shows them on Show, kept in the URL, and hides them again", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Show" }));

    expect(screen.getByLabelText("where")).toHaveTextContent("/sample?older=1");
    expect(screen.getByText("5 older attempts shown (4 Tasks)")).toBeVisible();
    expect(ids()).toEqual(
      expect.arrayContaining(["t-mixed", "t-empty", "t-gone", "t-started"]),
    );

    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.getByLabelText("where")).toHaveTextContent(/^\/sample$/);
    expect(ids()).not.toContain("t-gone");
  });

  it("counts only what it shows in the chips, a chosen one at 0", async () => {
    show("/sample?status=~no-status");
    await screen.findByText("5 older attempts hidden (4 Tasks)");

    expect(
      screen.getByText("Nothing matches the search and the status filter."),
    ).toBeVisible();
    const chips = screen
      .getAllByRole("button", { pressed: true })
      .map((b) => b.textContent);
    expect(chips).toEqual(["No status0"]);
    // The unreadable attempt of the item whose lock is held stays counted.
    expect(
      screen.getByRole("button", { name: /^Unreadable/ }),
    ).toHaveTextContent("Unreadable1");
  });

  it("says when every attempt is an older attempt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify([unreadable("t-gone/1")]), {
            status: 200,
          }),
        ),
      ),
    );
    show();

    expect(
      await screen.findByText(
        "Every attempt of the project is an older attempt.",
      ),
    ).toBeVisible();
    expect(screen.getByText("1 older attempt hidden (1 Task)")).toBeVisible();
  });
});

describe("the home page while an attempt runs", () => {
  // Each read of the listing answers with the next of reads, the last one
  // again once they run out.
  function serve(...reads: AttemptRow[][]) {
    let n = 0;
    const fetch = vi.fn(() => {
      const rows = must(reads[Math.min(n++, reads.length - 1)]);
      return Promise.resolve(
        new Response(JSON.stringify(rows), { status: 200 }),
      );
    });
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  const tick = async (ms = POLL_MS) => {
    // Let the effects of what has landed run first, the poll's timer among
    // them: a page found on screen may not have set it yet.
    await act(() => Promise.resolve());
    act(() => {
      vi.advanceTimersByTime(ms);
    });
    await act(() => Promise.resolve());
  };

  /** The Duration cell of an item's row. */
  const durationOf = (id: string) =>
    must(
      screen.getByText(id).closest("tr")?.querySelector("td:nth-child(9)") ??
        undefined,
    );

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-03-02T09:45:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts a running attempt's duration on from its start", async () => {
    serve(ROWS);
    show();
    await screen.findByText("t-run");

    expect(durationOf("t-run")).toHaveTextContent("45m 0s");
    await tick(1000);
    expect(durationOf("t-run")).toHaveTextContent("45m 1s");
    // One that has ended keeps the time it took.
    expect(durationOf("t-old")).toHaveTextContent("1h 0m");
  });

  it("reads the listing again while an attempt runs, with no Reading…", async () => {
    const ended = row("t-run/1", {
      started_at: at(9),
      ended_at: at(10),
      cost_usd: 3,
    });
    const fetch = serve(ROWS, [ended, ...ROWS.slice(1)]);
    show();
    await screen.findByText("t-run");
    expect(fetch).toHaveBeenCalledTimes(1);

    await act(() => Promise.resolve());
    act(() => {
      vi.advanceTimersByTime(POLL_MS);
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Reading…")).toBeNull();
    await act(() => Promise.resolve());

    const item = must(screen.getByText("t-run").closest("tr") ?? undefined);
    await waitFor(() => {
      expect(within(item).getByText("Passed")).toBeVisible();
    });
    expect(within(item).getByText("$3.00")).toBeVisible();
    expect(durationOf("t-run")).toHaveTextContent("1h 0m");

    // None runs now, so it reads on no timer.
    await tick();
    await tick();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps an item's attempts open when the listing is read again", async () => {
    const two = [
      row("t-run/2", { started_at: at(9), live: "running", status: null }),
      row("t-run/1", { started_at: at(7), ended_at: at(8) }),
    ];
    const fetch = serve(two, two);
    show();
    await screen.findByText("t-run");
    const toggle = screen.getByRole("button", { name: "2 attempts of t-run" });
    fireEvent.click(toggle);
    // The table takes the new state on its own schedule.
    await waitFor(() => {
      expect(toggle).toHaveAttribute("aria-expanded", "true");
    });

    await tick();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("button", { name: "2 attempts of t-run" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("reads it on no timer when no attempt runs", async () => {
    const fetch = serve(ROWS.slice(1));
    show();
    await screen.findByText("t-old");

    await tick();
    await tick();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
