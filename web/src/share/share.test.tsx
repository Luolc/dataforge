import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiSource } from "./api-source";
import { ShareApp } from "./App";
import { fakeShare, GOOD_TOKEN } from "./fake-api";
import { REFUSED } from "./Landing";

function open(path: string, signedIn = true) {
  const fake = fakeShare(signedIn);
  const shown = render(
    <MemoryRouter initialEntries={[path]}>
      <ShareApp source={apiSource} />
    </MemoryRouter>,
  );
  return { ...shown, ...fake };
}

const requested = (fetch: ReturnType<typeof fakeShare>["fetch"]) =>
  fetch.mock.calls.map(([url]) => url);

async function signInWith(token: string) {
  const field = await screen.findByLabelText("Access token");
  fireEvent.change(field, { target: { value: token } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

/** The page's own buttons: not the sidebar's, nor the one that opens it. */
const pageButtons = () =>
  screen
    .getAllByRole("button")
    .filter(
      (b) =>
        b.closest("nav[aria-label=Site]") === null &&
        b.getAttribute("aria-label") !== "Open the site menu",
    );

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("the share site's sign-in", () => {
  it("asks for a token in a field that does not show or keep it", async () => {
    open("/", false);

    const field = await screen.findByLabelText("Access token");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "off");
    expect(screen.queryByRole("navigation", { name: "Site" })).toBeNull();
  });

  it("signs in with the token in a POST body, then lists the items", async () => {
    const { fetch } = open("/", false);

    await signInWith(GOOD_TOKEN);

    expect(
      await screen.findByRole("link", { name: "k7m2q9xr4t-r2" }),
    ).toBeVisible();
    const login = fetch.mock.calls.find(([url]) => url === "/api/login");
    expect(login?.[1]).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: GOOD_TOKEN }),
    });
    expect(requested(fetch).some((url) => url.includes(GOOD_TOKEN))).toBe(
      false,
    );
    expect(JSON.stringify(Object.entries(localStorage))).not.toContain(
      GOOD_TOKEN,
    );
    expect(screen.queryByLabelText("Access token")).toBeNull();
  });

  it("tells every refused token the same thing, whatever the server's reason", async () => {
    const { state } = open("/", false);

    state.reason = "the token has expired";
    await signInWith("dfs_00000001_old");
    const first = await screen.findByRole("alert");
    expect(first).toHaveTextContent(REFUSED);

    state.reason = "the token was revoked";
    await signInWith("dfs_00000002_gone");
    expect(await screen.findByRole("alert")).toHaveTextContent(REFUSED);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/expired|revoked/);
  });

  it("says so when a sign-out fails, and stays signed in", async () => {
    const { state } = open("/demo");
    state.logoutFails = true;

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Signing out failed: Failed to fetch. You are still signed in.",
    );
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.queryByLabelText("Access token")).toBeNull();
  });

  it("asks for a token again after a sign-out", async () => {
    open("/demo");

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByLabelText("Access token")).toBeVisible();
  });
});

describe("the share site's list of items", () => {
  it("has one column, of each item's ID with its revision", async () => {
    open("/demo");

    const table = await screen.findByRole("table");
    await within(table).findByRole("link", { name: "k7m2q9xr4t-r2" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["ID"]);
    expect(
      within(table)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["k7m2q9xr4t-r2", "p3w8n5c2vd-r1", "h6t1z4b9qm-r1"]);
    expect(
      within(table).getByRole("link", { name: "p3w8n5c2vd-r1" }),
    ).toHaveAttribute("href", "/demo/items/p3w8n5c2vd");
  });

  it("searches the IDs and says how many the search hides", async () => {
    open("/demo");

    const search = await screen.findByRole("searchbox", { name: "Search IDs" });
    fireEvent.change(search, { target: { value: "P3W" } });

    const table = screen.getByRole("table");
    expect(
      within(table)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["p3w8n5c2vd-r1"]);
    expect(screen.getByText(/2 of 3 hidden by the search/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(within(table).getAllByRole("link")).toHaveLength(3);
  });

  it("has a refresh and a colour template switch, and no filter", async () => {
    const { fetch } = open("/demo");

    await screen.findByRole("table");
    expect(pageButtons().map((b) => b.textContent)).toEqual([
      "Light",
      "Refresh",
    ]);
    expect(screen.queryByRole("group")).toBeNull();

    const reads = () =>
      requested(fetch).filter((url) => url.endsWith("/items")).length;
    const before = reads();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(reads()).toBe(before + 1);
  });
});

describe("the share site's item page", () => {
  it("has exactly the three tabs of the item's deliverables, and nothing to act on", async () => {
    open("/demo/items/k7m2q9xr4t?tab=reviews");

    await screen.findByRole("heading", { name: "Quality reviews" });
    const tabs = screen.getByRole("navigation", { name: "Item" });
    expect(
      within(tabs)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Harbor task", "Pass@K", "Quality reviews"]);
    expect(
      within(tabs).getByRole("link", { name: "Quality reviews" }),
    ).toHaveAttribute("aria-current", "page");
    expect(pageButtons().map((b) => b.textContent)).toEqual(["Light"]);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "k7m2q9xr4t-r2",
    );
  });

  it("draws the reviews' result cells as status labels", async () => {
    open("/demo/items/k7m2q9xr4t?tab=reviews");

    await screen.findByRole("heading", { name: "Quality reviews" });
    expect((await screen.findAllByText("Pass")).length).toBeGreaterThan(0);
    expect(screen.getByText("Fail")).toBeVisible();
  });

  it("shows the task's files in the viewer's file view", async () => {
    open("/demo/items/k7m2q9xr4t?tab=task");

    expect(
      await screen.findByRole("heading", { name: "Harbor task" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", { level: 3, name: "instruction.md" }),
    ).toBeVisible();
    expect(await screen.findByText(/Fix the header reader/)).toBeVisible();
  });
});

describe("the share site's Pass@K tab", () => {
  it("gives each trial its number, reward, exception and model", async () => {
    open("/demo/items/k7m2q9xr4t?tab=pass_at_k");

    expect(await screen.findByText("3 / 4 solved")).toBeVisible();
    const trials = screen.getByRole("navigation", { name: "Trials" });
    expect(
      within(trials)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Trial 1solved",
      "Trial 2solved",
      "Trial 3not solved·exception",
      "Trial 4solved",
    ]);

    expect(screen.getByText("Reward").closest("dl")).toHaveTextContent(
      "Reward1Exception—Modelexample-model",
    );
    fireEvent.click(within(trials).getByRole("button", { name: /^Trial 3/ }));
    const fields = screen.getByText("Reward").closest("dl");
    expect(fields).toHaveTextContent(
      "Reward0ExceptionAgentTimeoutErrorModelexample-model",
    );
  });

  it("shows no duration, cost, turns, tokens or diff anywhere", async () => {
    const { container } = open("/demo/items/k7m2q9xr4t?tab=pass_at_k");

    await screen.findByRole("button", { name: "Expand all" });
    expect(container).not.toHaveTextContent(/duration|cost|turns|tokens|diff/i);
  });

  it("opens every fold of the trace from the start", async () => {
    open("/demo/items/k7m2q9xr4t?tab=pass_at_k");

    await screen.findByRole("button", { name: "Expand all" });
    const folds = pageButtons().filter((b) => b.hasAttribute("aria-controls"));
    expect(folds.length).toBeGreaterThan(3);
    expect(folds.every((b) => b.getAttribute("aria-expanded") === "true")).toBe(
      true,
    );
  });
});
