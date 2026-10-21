import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { Frame, Shell } from "./Shell";

describe("the breadcrumb", () => {
  it("styles each crumb before the page as a link, the page as plain text", () => {
    render(
      <MemoryRouter>
        <Frame
          crumbs={[
            { label: "Projects", to: "/" },
            { label: "Example", to: "/example" },
            { label: "task-0001" },
          ]}
        >
          body
        </Frame>
      </MemoryRouter>,
    );
    const crumbs = within(
      screen.getByRole("navigation", { name: "Breadcrumb" }),
    );

    for (const [name, href] of [
      ["Projects", "/"],
      ["Example", "/example"],
    ] as const) {
      const link = crumbs.getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      expect(link).toHaveClass("link", "hover:underline");
    }
    expect(crumbs.queryByRole("link", { name: "task-0001" })).toBeNull();
    expect(crumbs.getByText("task-0001")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(crumbs.getByText("task-0001")).not.toHaveClass("link");
  });

  it("wraps a crumb too long for the bar onto more lines, cutting none", () => {
    const long = "batch-0009/task-0901-an-attempt-identifier-that-runs-on";
    render(
      <MemoryRouter>
        <Frame
          crumbs={[
            { label: "Projects", to: "/" },
            { label: long, to: "/example" },
            { label: "Run 2 · Build" },
          ]}
        >
          body
        </Frame>
      </MemoryRouter>,
    );
    const bar = screen.getByRole("navigation", { name: "Breadcrumb" });

    expect(bar).toHaveClass("min-h-header");
    expect(bar).not.toHaveClass("h-header");
    expect(within(bar).getByRole("list")).toHaveClass("flex-wrap");
    expect(within(bar).getByRole("list")).not.toHaveClass("overflow-x-auto");
    for (const crumb of [
      within(bar).getByRole("link", { name: long }),
      within(bar).getByText("Run 2 · Build"),
    ]) {
      expect(crumb).toHaveClass("min-w-0", "wrap-anywhere");
      expect(crumb).not.toHaveClass("whitespace-nowrap");
      expect(crumb).not.toHaveClass("truncate");
    }
  });
});

describe("the sidebar", () => {
  it("leads to the Monitor and Queue pages", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("[]", { status: 200 }))),
    );
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    const site = within(screen.getByRole("navigation", { name: "Site" }));

    expect(site.getByRole("link", { name: "Monitor" })).toHaveAttribute(
      "href",
      "/monitor",
    );
    expect(site.getByRole("link", { name: "Queue" })).toHaveAttribute(
      "href",
      "/queue",
    );
    vi.unstubAllGlobals();
  });

  it("is fixed from lg up, and below it a drawer the breadcrumb's button opens", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("[]", { status: 200 }))),
    );
    render(
      <MemoryRouter initialEntries={["/example"]}>
        <Routes>
          <Route element={<Shell />}>
            <Route
              path="*"
              element={<Frame crumbs={[{ label: "Projects" }]}>body</Frame>}
            />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    const fixed = screen.getByRole("navigation", { name: "Site" });
    expect(fixed).toHaveClass("hidden", "lg:flex");
    const open = screen.getByRole("button", { name: "Open the site menu" });
    expect(open).toHaveClass("lg:hidden", "target");
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(open);
    const drawer = screen.getByRole("dialog", { name: "Site" });
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(open).toHaveAttribute("aria-controls", drawer.id);
    expect(drawer).toHaveClass("lg:hidden");
    expect(drawer.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(drawer, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(open);
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Site" })).getByRole("link", {
        name: "Queue",
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    vi.unstubAllGlobals();
  });
});
