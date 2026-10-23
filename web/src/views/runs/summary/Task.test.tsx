import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { must, records, sampleProject } from "../../../../fixtures/sample";
import type { AttemptRecord, Project, TaskMeta } from "../../../api/types";
import { cutAt, PRINCIPLE_CUT } from "./cut";
import { TaskCard } from "./Task";

const project: Project = {
  ...sampleProject,
  task: { file: "task/task.toml", deliverable: null },
  trials: {
    diff: null,
    tests: "verifier/ctrf.json",
    kinds: {
      security: { label: "Security" },
      behavior: { label: "Behavior" },
    },
  },
};

const LONG =
  "When a change lets a new kind of name through, that name goes through the same normalising, length limits and logging as the names that came before it, not a shorter path of its own that skips them.";
const SHORT =
  "A limit the caller can raise is not a check the caller can skip.";

// As the task route serves fixtures/trialdiff's pass-at-k, but for p2p's
// project, which comes last here.
const found: TaskMeta = {
  read: "found",
  reason: null,
  path: "task/task.toml",
  legacy: false,
  tests: {
    f2p: { security: 5, behavior: 2, alignment: 2 },
    p2p: { behavior: 3, alignment: 0, project: 148 },
  },
  principles: [
    {
      id: "p1",
      text: LONG,
      type: "best_practice",
      source: "new",
      tests: [
        "f2p::test_folded_names_are_normalised",
        "f2p::test_folded_names_keep_their_limits",
      ],
    },
    { id: "p3", text: SHORT, type: null, source: null, tests: [] },
  ],
};

function serve(meta: TaskMeta) {
  const fetch = vi.fn(() => Promise.resolve(Response.json(meta)));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function card(
  meta: TaskMeta,
  shown: Project = project,
  record: AttemptRecord = records.passed,
) {
  serve(meta);
  render(<TaskCard project={shown} attempt="pass-at-k" record={record} />);
  const heading = await screen.findByRole("heading", { name: "Task" });
  return heading.closest("section") ?? document.body;
}

/** Each row of the table under a suite's heading, as [kind, count]. */
function rows(section: HTMLElement, suite: string) {
  const heading = within(section).getByRole("heading", {
    name: new RegExp(`^${suite} · `),
  });
  const table = heading.parentElement?.querySelector("table");
  if (!table) throw new Error(`no table for ${suite}`);
  return [...table.querySelectorAll("tr")]
    .slice(1)
    .map((tr) => [...tr.children].map((c) => c.textContent));
}

describe("TaskCard", () => {
  it("counts each suite's tests by kind, the project's own in a row of their own", async () => {
    const section = await card(found);

    expect(rows(section, "f2p")).toEqual([
      ["Security", "5"],
      ["Behavior", "2"],
      ["alignment", "2"],
      ["Total", "9"],
    ]);
    expect(rows(section, "p2p")).toEqual([
      ["Project's own", "148"],
      ["Behavior", "3"],
      ["alignment", "0"],
      ["Total", "151"],
    ]);
    const count = within(section).getByRole("cell", { name: "148" });
    expect(count).toHaveClass("text-right", "tabular-nums");
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/sample/task?attempt=pass-at-k",
      expect.anything(),
    );
  });

  it("lists each principle with its type, source and tests", async () => {
    const section = await card(found);

    const list = within(section).getByRole("list", { name: "Principles" });
    const [first, second] = list.querySelectorAll<HTMLLIElement>(":scope > li");
    if (!first || !second) throw new Error("no principles");
    expect(within(section).getByText("Principles · 2")).toBeVisible();
    expect(first).toHaveTextContent("Best practice");
    expect(within(first).getByText("new")).toBeVisible();
    expect(
      within(first).getByText("f2p::test_folded_names_keep_their_limits"),
    ).toBeVisible();
    expect(second).toHaveTextContent(
      "TypeNot recordedSourceNot recordedTestsNo test names it.",
    );
  });

  it("cuts a long principle, with a control that shows the whole text", async () => {
    const section = await card(found);

    expect(within(section).queryByText(LONG)).toBeNull();
    const more = within(section).getByRole("button", { name: "Show all" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    const text = document.getElementById(
      more.getAttribute("aria-controls") ?? "",
    );
    expect(text?.textContent).toBe(cutAt(LONG, PRINCIPLE_CUT));

    fireEvent.click(more);

    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(text?.textContent).toBe(LONG);
    expect(within(section).getByText(SHORT)).toBeVisible();
  });

  it("says what a task of the format before tests does not record, not 0", async () => {
    const section = await card({
      read: "found",
      reason: null,
      path: "task/task.toml",
      legacy: true,
      tests: { f2p: { security: 3 } },
      principles: [
        { id: "p1", text: SHORT, type: null, source: null, tests: [] },
      ],
    });

    expect(rows(section, "f2p")).toEqual([
      ["Security", "3"],
      ["Total", "3"],
    ]);
    expect(
      within(section).getByText("The format before tests records no p2p."),
    ).toBeVisible();
    expect(within(section).getByRole("listitem")).toHaveTextContent(
      "TestsNot recorded: The format before tests does not record which tests check it.",
    );
    expect(
      within(section).getByText("task/task.toml · the format before tests"),
    ).toBeVisible();
  });

  it("shows a task.toml that is refused with its word, icon and reason, and no counts", async () => {
    const reason =
      "task/task.toml: metadata.principles[0].type: is not one of best_practice, trade_off";
    const section = await card({
      read: "missing",
      reason,
      path: "task/task.toml",
      legacy: false,
      tests: null,
      principles: null,
    });

    const status = within(section).getByText("Unreadable");
    expect(status.querySelector(".lucide-circle-x")).not.toBeNull();
    expect(within(section).getByText(reason)).toBeVisible();
    expect(within(section).queryByRole("table")).toBeNull();
    expect(within(section).queryByText(/Principles/)).toBeNull();
  });

  it("says in its own words that a task.toml is not there, not the system's", async () => {
    const absent: TaskMeta = {
      read: "not run",
      reason: "task/task.toml: No such file or directory",
      path: "task/task.toml",
      legacy: false,
      tests: null,
      principles: null,
    };
    const section = await card(absent);

    const status = within(section).getByText(
      "Not run: the attempt left no task/task.toml.",
    );
    expect(status.querySelector(".lucide-circle-dashed")).not.toBeNull();
    expect(within(section).queryByText(/No such file/)).toBeNull();
    expect(within(section).queryByRole("table")).toBeNull();
  });

  it("says a running attempt has no task.toml yet", async () => {
    const section = await card(
      {
        read: "not run",
        reason: "task/task.toml: No such file or directory",
        path: "task/task.toml",
        legacy: false,
        tests: null,
        principles: null,
      },
      project,
      { ...records.passed, status: null },
    );

    expect(
      within(section).getByText(
        "Not run: the attempt has no task/task.toml yet.",
      ),
    ).toBeVisible();
  });

  it("is not there, and reads nothing, for a project that names no task", () => {
    const fetch = serve(found);

    const { container } = render(
      <TaskCard
        project={{ ...project, task: null }}
        attempt="pass-at-k"
        record={records.passed}
      />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("a TaskCard whose task is in a deliverable", () => {
  // passed's task, made by its second author run, run 3 of the attempt.
  const inTask: Project = {
    ...project,
    states: { ...project.states, author: { title: "Author" } },
    deliverables: [{ name: "task", kind: "files", title: "Task" }],
    task: { file: "task.toml", deliverable: "task" },
  };
  const made = must(
    records.passed.deliverables?.find((d) => d.name === "task"),
  );
  // As the page hands the record on, the reason in the viewer's words.
  const withTask = (task: Partial<typeof made>): AttemptRecord => ({
    ...records.passed,
    status: null,
    deliverables: [{ ...made, ...task }],
  });

  it("reads the one the record resolves, and says which run made it", async () => {
    const section = await card(
      { ...found, path: "artifacts/author/2/task/task.toml" },
      inTask,
      withTask({}),
    );

    expect(
      within(section).getByText("Author · run 3 ·", { exact: false }),
    ).toBeVisible();
    expect(rows(section, "f2p")).toHaveLength(4);
  });

  it("reads again when the record names a later run's", async () => {
    const fetch = serve(found);
    const { rerender } = render(
      <TaskCard project={inTask} attempt="pass-at-k" record={withTask({})} />,
    );
    await screen.findByText("Principles · 2");

    rerender(
      <TaskCard
        project={inTask}
        attempt="pass-at-k"
        record={withTask({
          visit: 3,
          visit_index: 4,
          path: "artifacts/author/3/task",
        })}
      />,
    );

    expect(await screen.findByText(/Author · run 5 ·/)).toBeVisible();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(2);
    });
  });

  it("reads nothing while the record has not made it, and says why", async () => {
    const fetch = serve(found);
    render(
      <TaskCard
        project={inTask}
        attempt="pass-at-k"
        record={withTask({
          state: "review",
          visit: null,
          visit_index: null,
          path: null,
          reason: "the attempt has not reached Review yet.",
        })}
      />,
    );

    expect(
      await screen.findByText(
        "Not run: the attempt has not reached Review yet.",
      ),
    ).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("says which run made no task.toml", async () => {
    const section = await card(
      {
        read: "not run",
        reason: "artifacts/author/2/task/task.toml: No such file or directory",
        path: "artifacts/author/2/task/task.toml",
        legacy: false,
        tests: null,
        principles: null,
      },
      inTask,
      withTask({}),
    );

    expect(
      within(section).getByText("Not run: Author · run 3 made no task.toml."),
    ).toBeVisible();
  });

  it("names a deliverable the record lacks by the project's title", async () => {
    const section = await card(
      {
        read: "not run",
        reason: null,
        path: null,
        legacy: false,
        tests: null,
        principles: null,
      },
      {
        ...inTask,
        deliverables: [{ name: "task", kind: "files", title: "Harbor task" }],
      },
      { ...records.passed, deliverables: [] },
    );

    expect(
      within(section).getByText("Not run: this record has no Harbor task."),
    ).toBeVisible();
  });
});

describe("cutAt", () => {
  it("leaves a text of max characters whole and cuts a longer one at a space", () => {
    const text = `${"a".repeat(PRINCIPLE_CUT - 4)} bcd`;

    expect(cutAt(text, PRINCIPLE_CUT)).toBeNull();
    expect(cutAt(`${text}e`, PRINCIPLE_CUT)).toBe(
      `${"a".repeat(PRINCIPLE_CUT - 4)}…`,
    );
  });
});
