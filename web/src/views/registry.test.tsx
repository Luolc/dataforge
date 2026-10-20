import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import type { Deliverable } from "../api/types";
import type { DeliverableProps } from "./kind";
import { KindTab } from "./registry";

// Each view stands in as a heading that names its module, so these tests hold
// whatever the views themselves show.
vi.mock("./files", () => ({ default: stub("files") }));
vi.mock("./report", () => ({ default: stub("report") }));
vi.mock("./trials", () => ({ default: stub("trials") }));

function stub(module: string) {
  return function View({ deliverable }: DeliverableProps) {
    return <h3>{`${module} view: ${deliverable.title}`}</h3>;
  };
}

const item = (name: string, kind: string): Deliverable => ({
  name,
  kind,
  title: `Title of ${name}`,
  state: "author",
  output: null,
  visit: null,
  visit_index: null,
  path: null,
  reason: "author was never visited",
});

function show(kind: string, items: Deliverable[]) {
  return render(
    <KindTab
      kind={kind}
      project={sampleProject}
      attempt="a/b"
      record={records.passed}
      items={items}
    />,
  );
}

describe("the kind registry", () => {
  it("a view is chosen by kind only", () => {
    for (const kind of ["files", "report", "trials"]) {
      const { unmount } = show(kind, [item("one", kind)]);
      expect(screen.getByRole("heading")).toHaveTextContent(
        `${kind} view: Title of one`,
      );
      unmount();
    }
  });

  it("an unknown kind falls back to a card", () => {
    show("dataset-card", [item("card", "dataset-card")]);

    expect(
      screen.getByRole("heading", { name: "Title of card" }),
    ).toBeVisible();
    expect(screen.getByText("dataset-card")).toBeVisible();
    expect(screen.getByText("author was never visited")).toBeVisible();
    expect(screen.queryByText(/ view: /)).toBeNull();
  });

  it("reports stack; files show one, chosen from a menu", () => {
    const { unmount } = show("report", [
      item("one", "report"),
      item("two", "report"),
    ]);
    expect(screen.getAllByRole("heading")).toHaveLength(2);
    unmount();

    show("files", [item("one", "files"), item("two", "files")]);
    expect(screen.getAllByRole("heading").map((h) => h.textContent)).toEqual([
      "files view: Title of one",
    ]);
    expect(
      screen.getByRole("button", { name: "Title of one" }),
    ).toHaveAttribute("aria-haspopup", "menu");
  });
});
