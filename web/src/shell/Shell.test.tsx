import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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
});
