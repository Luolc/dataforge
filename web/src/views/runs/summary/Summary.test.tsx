import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { records, sampleProject } from "../../../../fixtures/sample";
import { Summary } from "./Summary";

function show(name: keyof typeof records) {
  return render(
    <MemoryRouter>
      <Summary
        project={sampleProject}
        attempt="batch-1/task-0002"
        record={records[name]}
      />
    </MemoryRouter>,
  );
}

describe("Summary", () => {
  it("draws the machine with its states linked to their last runs", () => {
    show("passed");

    const graph = screen.getByRole("img", { name: "State machine" });
    const solve = within(graph).getByRole("link", {
      name: "Pass@k: its last run",
    });
    expect(solve).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-1%2Ftask-0002&tab=runs&run=5",
    );
    expect(graph.querySelectorAll('[data-taken="true"]')).toHaveLength(5);
    expect(graph.querySelectorAll('[data-taken="false"]')).toHaveLength(2);
  });

  it("renders the actions' summaries as untrusted Markdown", () => {
    const { container } = show("tooEasy");

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/raw HTML shows as text/)).toHaveTextContent(
      "<b>raw HTML shows as text</b>",
    );
    expect(screen.getByRole("link", { name: "image: a chart" })).toBeVisible();
  });

  it("links each deliverable to its tab, or says why it was not reached", () => {
    show("exhausted");

    expect(screen.getByRole("link", { name: "Pass@k" })).toHaveAttribute(
      "href",
      "/sample/attempt?id=batch-1%2Ftask-0002&tab=trials",
    );
    expect(screen.getByText("Not run: solve was never visited")).toBeVisible();
  });
});

describe("an attempt's status", () => {
  it("is raised when a state raised, else Not ended until it ends", () => {
    const { unmount } = show("raised");
    expect(screen.getByText("raised")).toBeVisible();
    unmount();

    show("running");
    expect(screen.getByText("Not ended")).toBeVisible();
  });
});
