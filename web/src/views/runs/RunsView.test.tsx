import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { records, sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { RunsView } from "./RunsView";

function show(project: Project) {
  render(
    <MemoryRouter>
      <RunsView
        project={project}
        attempt="batch-2/task-0005"
        record={records.running}
        run={null}
      />
    </MemoryRouter>,
  );
  return within(screen.getByRole("navigation", { name: "Runs" }));
}

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
