import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sampleProject } from "../../../../fixtures/sample";
import type { Project, TaskMeta } from "../../../api/types";
import { cutAt, PRINCIPLE_CUT } from "./cut";
import { TaskCard } from "./Task";

const project: Project = {
  ...sampleProject,
  task: "task/task.toml",
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

// As the task route serves fixtures/trialdiff's pass-at-k.
const found: TaskMeta = {
  read: "found",
  reason: null,
  legacy: false,
  tests: {
    f2p: { security: 5, behavior: 2, alignment: 2 },
    p2p: { project: 148, behavior: 3, alignment: 0 },
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

async function card(meta: TaskMeta) {
  serve(meta);
  render(<TaskCard project={project} attempt="pass-at-k" />);
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

  it.each([
    [
      "not run",
      "Not run",
      "task: No such file or directory",
      ".lucide-circle-dashed",
    ],
    [
      "missing",
      "Unreadable",
      "task/task.toml: metadata.principles[0].type: is not one of best_practice, trade_off",
      ".lucide-circle-x",
    ],
  ] as const)(
    "shows a task.toml that is %s with its word, icon and reason, and no counts",
    async (read, word, reason, icon) => {
      const section = await card({
        read,
        reason,
        legacy: false,
        tests: null,
        principles: null,
      });

      const status = within(section).getByText(word);
      expect(status.querySelector(icon)).not.toBeNull();
      expect(within(section).getByText(reason)).toBeVisible();
      expect(within(section).queryByRole("table")).toBeNull();
      expect(within(section).queryByText(/Principles/)).toBeNull();
    },
  );

  it("is not there, and reads nothing, for a project that names no task", () => {
    const fetch = serve(found);

    const { container } = render(
      <TaskCard project={{ ...project, task: null }} attempt="pass-at-k" />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(fetch).not.toHaveBeenCalled();
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
