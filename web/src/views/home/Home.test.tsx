import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AttemptRow } from "../../api/types";
import Home from "./index";
import { project, row, unreadable } from "./rows.test-data";

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

function show(url = "/sample") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route
          path=":project"
          element={
            <>
              <Home project={project} />
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
  return [...body.querySelectorAll("tr:not([hidden]) td:first-child a")].map(
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
      screen.getByText(/3 of 4 hidden by the search and the status filter/),
    ).toBeVisible();
  });

  it("searches item IDs from the URL", async () => {
    show("/sample?q=OLD");
    await screen.findByText("t-old");

    expect(screen.queryByText("t-run")).toBeNull();
    expect(screen.getByText(/3 of 4 hidden/)).toBeVisible();
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
});
