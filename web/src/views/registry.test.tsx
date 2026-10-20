import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import type { Deliverable } from "../api/types";
import { KindView } from "./registry";

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
    <KindView
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
    show("files", [item("trials", "files")]);

    expect(screen.getByText("The files view is not built yet.")).toBeVisible();
  });

  it("an unknown kind falls back to a card", () => {
    show("dataset-card", [item("card", "dataset-card")]);

    expect(
      screen.getByRole("heading", { name: "Title of card" }),
    ).toBeVisible();
    expect(screen.getByText("dataset-card")).toBeVisible();
    expect(screen.getByText("author was never visited")).toBeVisible();
    expect(screen.queryByText(/not built yet/)).toBeNull();
  });
});
