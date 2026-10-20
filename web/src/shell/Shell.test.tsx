import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { Frame } from "./Shell";

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
