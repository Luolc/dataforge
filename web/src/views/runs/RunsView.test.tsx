import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { must, records, sampleProject } from "../../../fixtures/sample";
import type { AttemptRecord, Project } from "../../api/types";
import { RunsView } from "./RunsView";

function show(
  project: Project,
  record: AttemptRecord = records.running,
  discarded: number | null = null,
) {
  render(
    <MemoryRouter>
      <RunsView
        project={project}
        attempt="batch-2/task-0005"
        record={record}
        run={null}
        discarded={discarded}
      />
    </MemoryRouter>,
  );
  return within(screen.getByRole("navigation", { name: "Runs" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the Runs column", () => {
  it("shows a run with no outcome as No outcome once its attempt ended", () => {
    const runs = show({ ...sampleProject, not_ended: ["running"] });

    expect(runs.getByRole("img", { name: "No outcome" })).toBeVisible();
    expect(runs.getByText(/^No outcome ·/)).toBeVisible();
    expect(runs.queryByText(/Not ended/)).toBeNull();
  });

  it("shows a run with no outcome as Not ended while its attempt runs", () => {
    const runs = show(sampleProject);

    expect(runs.getByRole("img", { name: "Not ended" })).toBeVisible();
    expect(runs.getByText(/^Not ended ·/)).toBeVisible();
  });
});

describe("a resumed attempt in the Runs column", () => {
  it("marks where it went on after its kept runs, the discarded folded", () => {
    const runs = show(sampleProject, records.resumed);
    const nav = screen.getByRole("navigation", { name: "Runs" });
    const order = [...nav.querySelectorAll("a, button, p")]
      .filter((e) => e.closest("[hidden]") === null)
      .map((e) => e.textContent);
    const group = runs.getByRole("button", { name: /Discarded/ });
    const resumed = order.indexOf("Resumed 2026-03-02 09:52 UTC");

    expect(group).toHaveAttribute("aria-expanded", "false");
    expect(group).toHaveTextContent("Discarded1 of 1 bad or warn");
    expect(order.indexOf(must(group.textContent))).toBe(resumed - 1);
    expect(order[resumed - 2]).toMatch(/^authordone · .*3$/);
    expect(order[resumed + 1]).toBe("the machine restarted while check 2 ran");
    expect(order[resumed + 2]).toBe("$0.37 carried");
    expect(order[resumed + 3]).toMatch(/^checkpass · .*4$/);
  });

  it("opens the group to the discarded run, linked to its page", () => {
    const runs = show(sampleProject, records.resumed);
    const group = runs.getByRole("button", { name: /Discarded/ });

    fireEvent.click(group);

    expect(group).toHaveAttribute("aria-expanded", "true");
    const list = must(
      document.getElementById(must(group.getAttribute("aria-controls"))),
    );
    expect(within(list).getByRole("link")).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-2%2Ftask-0005&tab=runs&discarded=0",
    );
    expect(within(list).getByRole("img", { name: "raised" })).toBeVisible();
  });

  it("a discarded run with no copy is no link and says why", () => {
    const first = must(records.resumed.resumptions?.[0]);
    const lost = {
      ...records.resumed,
      resumptions: [
        {
          ...first,
          reason: null,
          carried_usd: 0,
          discarded: first.discarded.map((v) => ({
            ...v,
            path: null,
            reason: "the caller kept no copy",
          })),
        },
      ],
    };
    const runs = show(sampleProject, lost);

    fireEvent.click(runs.getByRole("button", { name: /Discarded/ }));

    expect(runs.getByText("the caller kept no copy")).toBeVisible();
    expect(runs.queryByRole("link", { name: /raised/ })).toBeNull();
    expect(runs.getByText("Resumed 2026-03-02 09:52 UTC")).toBeVisible();
    expect(runs.queryByText(/carried/)).toBeNull();
  });

  it("shows a discarded run's page, its group open", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const runs = show(sampleProject, records.resumed, 0);

    expect(runs.getByRole("button", { name: /Discarded/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(runs.getByRole("link", { name: /raised/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("heading", { name: "check · visit 2" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Left out when the attempt was resumed at 2026-03-02 09:52 UTC: the machine restarted while check 2 ran.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Files kept" })).toBeVisible();
  });

  it("a discarded run with no outcome has ended while its attempt runs", () => {
    const first = must(records.resumed.resumptions?.[0]);
    const running = {
      ...records.resumed,
      status: null,
      resumptions: [
        {
          ...first,
          discarded: first.discarded.map((v) => ({ ...v, outcome: null })),
        },
      ],
    };
    const runs = show(sampleProject, running);
    const group = runs.getByRole("button", { name: /Discarded/ });

    fireEvent.click(group);

    const list = must(
      document.getElementById(must(group.getAttribute("aria-controls"))),
    );
    expect(within(list).getByRole("img", { name: "No outcome" })).toBeVisible();
    expect(within(list).queryByText(/Not ended/)).toBeNull();
  });

  it("opens the group when the page moves to one of its runs", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const view = (discarded: number | null) => (
      <MemoryRouter>
        <RunsView
          project={sampleProject}
          attempt="batch-2/task-0005"
          record={records.resumed}
          run={null}
          discarded={discarded}
        />
      </MemoryRouter>
    );
    const { rerender } = render(view(null));
    const group = screen.getByRole("button", { name: /Discarded/ });
    expect(group).toHaveAttribute("aria-expanded", "false");

    rerender(view(0));

    expect(group).toHaveAttribute("aria-expanded", "true");
  });

  it("a record with no resumptions has no Discarded group", () => {
    const runs = show(sampleProject, records.passed);

    expect(runs.queryByRole("button", { name: /Discarded/ })).toBeNull();
    expect(runs.queryByText(/^Resumed/)).toBeNull();
  });
});
