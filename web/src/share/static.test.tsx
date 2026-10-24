import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ShareApp } from "./App";
import { fakeStatic } from "./fake-static";
import { staticSource } from "./static-source";

function open(path: string, older = false) {
  const fetch = fakeStatic({ older });
  render(
    <MemoryRouter initialEntries={[path]}>
      <ShareApp source={staticSource} />
    </MemoryRouter>,
  );
  return fetch;
}

const requested = (fetch: ReturnType<typeof fakeStatic>) =>
  fetch.mock.calls.map(([url]) => url);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the share site as a static site", () => {
  it("reads only the site's data, with no sign-in, and signs out at the host", async () => {
    const fetch = open("/");

    const table = await screen.findByRole("table");
    expect(
      within(table)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["k7m2q9xr4t-r2", "p3w8n5c2vd-r1", "h6t1z4b9qm-r1"]);
    expect(screen.queryByLabelText("Access token")).toBeNull();
    expect(screen.getByRole("link", { name: "Sign out" })).toHaveAttribute(
      "href",
      "/cdn-cgi/access/logout",
    );
    expect(requested(fetch)).toEqual([
      "/data/projects.json",
      "/data/demo/items.json",
    ]);
  });

  it("draws an item's three tabs from its files", async () => {
    const fetch = open("/demo/items/k7m2q9xr4t?tab=report");

    expect((await screen.findAllByText("Pass")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("link", { name: /^Pass@k/ }));
    expect(await screen.findByText("3 / 4 solved")).toBeVisible();
    await screen.findByRole("button", { name: "Expand all" });
    fireEvent.click(screen.getByRole("link", { name: /^Harbor task/ }));
    expect(await screen.findByText(/Fix the header reader/)).toBeVisible();
    expect(
      await screen.findByRole("link", { name: /^Harbor task9/ }),
    ).toBeVisible();
    expect(requested(fetch).every((url) => url.startsWith("/data/"))).toBe(
      true,
    );
  });

  it("lists an older export's items by their IDs alone", async () => {
    open("/demo", true);

    const table = await screen.findByRole("table");
    await within(table).findByRole("link", { name: "k7m2q9xr4t-r2" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["ID"]);
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("gives an older export's trials their trace alone", async () => {
    open("/demo/items/k7m2q9xr4t?tab=trials", true);

    const panes = await screen.findByRole("tablist", { name: "Trial" });
    expect(
      within(panes)
        .getAllByRole("tab")
        .map((t) => t.textContent),
    ).toEqual(["Trace"]);
  });

  it("lists a file listed only by its name and size, with no way to open it", async () => {
    const fetch = open("/demo/items/k7m2q9xr4t?tab=task");

    fireEvent.click(await screen.findByRole("button", { name: "environment" }));
    const row = await screen.findByText("source.tar.gz");
    expect(row.closest("a")).toBeNull();
    // The fake reads the fixture as text, so its size is not the file's.
    expect(row).toHaveTextContent(/^source\.tar\.gz\d+ B$/);
    expect(screen.queryByRole("link", { name: /source\.tar\.gz/ })).toBeNull();
    expect(requested(fetch).some((url) => url.includes("source.tar.gz"))).toBe(
      false,
    );
  });

  it("does not show a file listed only, even when the page names it", async () => {
    const fetch = open(
      "/demo/items/k7m2q9xr4t?tab=task&file=task/environment/source.tar.gz",
    );

    expect(
      await screen.findByText(
        "task/environment/source.tar.gz: not a file this site shows",
      ),
    ).toBeVisible();
    expect(requested(fetch).some((url) => url.includes("source.tar.gz"))).toBe(
      false,
    );
  });

  it("offers an item's download with its size", async () => {
    open("/demo/items/k7m2q9xr4t");

    const link = await screen.findByRole("link", { name: /Download all/ });
    expect(link).toHaveTextContent("Download all6.1 KiB");
    expect(link).toHaveAttribute(
      "href",
      "/data/demo/items/k7m2q9xr4t/k7m2q9xr4t-r2.zip",
    );
    expect(link).toHaveAttribute("download");
  });

  it("offers no download for an item without one", async () => {
    open("/demo/items/p3w8n5c2vd");

    await screen.findByRole("heading", { level: 1, name: "p3w8n5c2vd-r1" });
    expect(screen.queryByRole("link", { name: /Download all/ })).toBeNull();
  });

  it("downloads a file it shows from the site's copy of it", async () => {
    open("/demo/items/k7m2q9xr4t?tab=task&file=task/instruction.md");

    expect(
      await screen.findByRole("link", { name: "Download" }),
    ).toHaveAttribute(
      "href",
      "/data/demo/items/k7m2q9xr4t/files/task/instruction.md",
    );
  });
});
