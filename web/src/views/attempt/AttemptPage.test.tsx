import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { sampleProject } from "../../../fixtures/sample";
import passed from "../../../fixtures/attempts/batch-1/task-0001/record.json";

const PROJECTS = [
  { ...sampleProject, id: "first", name: "First dataset", not_ended: null },
  { ...sampleProject, not_ended: null },
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = url === "/api/projects" ? PROJECTS : passed;
      return Promise.resolve(
        new Response(JSON.stringify(body), { status: 200 }),
      );
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="Path">{pathname + search}</output>;
}

const ATTEMPT = "/sample/attempt?id=batch-1%2Ftask-0001";
const RUN = `${ATTEMPT}&tab=runs&run=0`;

function open(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
      <Where />
    </MemoryRouter>,
  );
  return within(screen.getByRole("navigation", { name: "Breadcrumb" }));
}

const path = () => screen.getByLabelText("Path");

describe("the attempt page's breadcrumb", () => {
  it.each([ATTEMPT, RUN])(
    "on %s, Projects opens the projects page",
    async (entry) => {
      fireEvent.click(
        await open(entry).findByRole("link", { name: "Projects" }),
      );

      expect(path()).toHaveTextContent(/^\/$/);
      const list = within(
        await screen.findByRole("list", { name: "Projects" }),
      );
      expect(list.getByRole("link", { name: "First dataset" })).toHaveAttribute(
        "href",
        "/first",
      );
      expect(
        list.getByRole("link", { name: "Sample dataset" }),
      ).toHaveAttribute("href", "/sample");
    },
  );

  it.each([ATTEMPT, RUN])(
    "on %s, the project's name opens its home page",
    async (entry) => {
      fireEvent.click(
        await open(entry).findByRole("link", { name: "Sample dataset" }),
      );

      expect(path()).toHaveTextContent(/^\/sample$/);
    },
  );

  it("on a run page, the attempt ID opens the attempt's Summary", async () => {
    fireEvent.click(
      await open(RUN).findByRole("link", { name: "batch-1/task-0001" }),
    );

    expect(path()).toHaveTextContent(
      /^\/sample\/attempt\?id=batch-1%2Ftask-0001$/,
    );
    expect(
      await screen.findByRole("link", { name: "Summary" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("on the Summary, the attempt ID is the page, not a link", async () => {
    const crumbs = open(ATTEMPT);

    expect(await crumbs.findByText("batch-1/task-0001")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      crumbs.queryByRole("link", { name: "batch-1/task-0001" }),
    ).toBeNull();
  });
});

describe("the projects page", () => {
  it("says so when the site has no projects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify([]), { status: 200 })),
      ),
    );
    open("/");

    expect(await screen.findByText("The site has no projects.")).toBeVisible();
  });
});
