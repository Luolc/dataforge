import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import { RefreshContext } from "../api/client";
import type { Deliverable } from "../api/types";
import { fakeFiles } from "./files/fake-api";
import type { DeliverableProps } from "./kind";
import { KindCount, KindTab } from "./registry";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

describe("a files tab's count", () => {
  it("counts again with the page's reads, keeping the last count meanwhile", async () => {
    const root = "artifacts/author/1/task";
    const files: Record<string, string> = {
      [`${root}/a.txt`]: "a",
      [`${root}/src/b.txt`]: "b",
    };
    fakeFiles(files);
    const task = { ...item("task", "files"), path: root, reason: null };
    const shown = (refresh: number) => (
      <RefreshContext value={refresh}>
        <p data-testid="count">
          <KindCount
            kind="files"
            project="sample"
            attempt="counted/again"
            item={task}
            items={[task]}
          />
        </p>
      </RefreshContext>
    );
    const { rerender } = render(shown(0));
    await waitFor(() => {
      expect(screen.getByTestId("count")).toHaveTextContent(/^2$/);
    });

    files[`${root}/src/c.txt`] = "c";
    rerender(shown(1));
    expect(screen.getByTestId("count")).toHaveTextContent(/^2$/);

    await waitFor(() => {
      expect(screen.getByTestId("count")).toHaveTextContent(/^3$/);
    });
  });
});
