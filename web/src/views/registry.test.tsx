import { act, render as plain, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { records, sampleProject } from "../../fixtures/sample";
import { READ_LIMIT_MS, RefreshContext } from "../api/client";
import type { Deliverable } from "../api/types";
import { fakeFiles } from "./files/fake-api";
import type { DeliverableProps } from "./kind";
import { KindCount, KindTab } from "./registry";

// The views read the page's query.
const render = (ui: ReactElement) => plain(ui, { wrapper: MemoryRouter });

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

/** The count a files tab says in full, as its pill's title has it. */
const countSaid = () =>
  screen.getByTestId("count").firstElementChild?.getAttribute("title");

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
            title="Title of task"
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
      expect(countSaid()).toBe("2 files");
    });

    files[`${root}/src/c.txt`] = "c";
    rerender(shown(1));
    expect(countSaid()).toBe("2 files");

    await waitFor(() => {
      expect(countSaid()).toBe("3 files");
    });
  });
});

describe("a files tab's count slower than the page's reads", () => {
  it("lands once per walk, and walks one at a time", async () => {
    const root = "artifacts/author/1/task";
    // Ten folders of one file each, every listing answered after 20 ms.
    const files: Record<string, string> = {};
    for (let d = 0; d < 10; d++) files[`${root}/d${String(d)}/f.txt`] = "f";
    const answer = fakeFiles(files);
    let inFlight = 0;
    let most = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        inFlight++;
        most = Math.max(most, inFlight);
        await new Promise((r) => setTimeout(r, 20));
        inFlight--;
        return answer(url);
      }),
    );
    const task = { ...item("task", "files"), path: root, reason: null };
    const shown = (refresh: number) => (
      <RefreshContext value={refresh}>
        <p data-testid="count">
          <KindCount
            kind="files"
            title="Title of task"
            project="sample"
            attempt="counted/slowly"
            item={task}
            items={[task]}
          />
        </p>
      </RefreshContext>
    );
    const { rerender } = render(shown(0));
    await waitFor(() => {
      expect(countSaid()).toBe("10 files");
    });
    files[`${root}/new.txt`] = "new";
    // A read every 50 ms, while a walk of 11 listings takes over 200 ms.
    for (let refresh = 1; refresh <= 8; refresh++) {
      await act(() => new Promise((r) => setTimeout(r, 50)));
      rerender(shown(refresh));
    }

    await waitFor(() => {
      expect(countSaid()).toBe("11 files");
    });
    expect(most).toBe(1);
  });
});

describe("a files tab's count that takes longer than a read's limit", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("lands while the page reads again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const root = "artifacts/author/1/task";
    // Ten folders of one file each, every listing answered after 600 ms: a
    // walk of 11 listings takes 6.6 s, more than READ_LIMIT_MS.
    const files: Record<string, string> = {};
    for (let d = 0; d < 10; d++) files[`${root}/d${String(d)}/f.txt`] = "f";
    const answer = fakeFiles(files);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        await new Promise((r) => setTimeout(r, 600));
        return answer(url);
      }),
    );
    const task = { ...item("task", "files"), path: root, reason: null };
    const shown = (refresh: number) => (
      <RefreshContext value={refresh}>
        <p data-testid="count">
          <KindCount
            kind="files"
            title="Title of task"
            project="sample"
            attempt="counted/past-the-limit"
            item={task}
            items={[task]}
          />
        </p>
      </RefreshContext>
    );
    const { rerender } = render(shown(0));
    await act(() => vi.advanceTimersByTimeAsync(7000));
    expect(countSaid()).toBe("10 files");

    files[`${root}/new.txt`] = "new";
    // A read just before the limit of the read before would give up.
    for (let refresh = 1; refresh <= 12; refresh++) {
      rerender(shown(refresh));
      await act(() => vi.advanceTimersByTimeAsync(READ_LIMIT_MS - 1));
    }

    expect(countSaid()).toBe("11 files");
  });
});
