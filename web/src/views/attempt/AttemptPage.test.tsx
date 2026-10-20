import { fireEvent, render, screen, within } from "@testing-library/react";

import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { sampleProject } from "../../../fixtures/sample";
import passed from "../../../fixtures/attempts/batch-1/task-0001/record.json";

// "/" opens the first project, so it comes before the attempt's own.
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
  return <output aria-label="Path">{useLocation().pathname}</output>;
}

async function clickCrumb(entry: string, name: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
      <Where />
    </MemoryRouter>,
  );
  const crumbs = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  fireEvent.click(await crumbs.findByRole("link", { name }));
}

const pages = [
  ["attempt page", "/sample/attempt?id=batch-1%2Ftask-0001"],
  ["run page", "/sample/attempt?id=batch-1%2Ftask-0001&tab=runs&run=0"],
];

describe("the attempt page's breadcrumb", () => {
  it.each(pages)(
    "on the %s, Projects opens the first project",
    async (_, entry) => {
      await clickCrumb(entry, "Projects");

      expect(await screen.findByLabelText("Path")).toHaveTextContent(
        /^\/first$/,
      );
    },
  );

  it.each(pages)(
    "on the %s, the project's name opens its home page",
    async (_, entry) => {
      await clickCrumb(entry, "Sample dataset");

      expect(await screen.findByLabelText("Path")).toHaveTextContent(
        /^\/sample$/,
      );
    },
  );
});
