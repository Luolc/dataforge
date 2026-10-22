import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { must, records, sampleProject } from "../../../fixtures/sample";
import type { AttemptRecord, Deliverable } from "../../api/types";
import { type FakeEntry, fakeFiles } from "../files/fake-api";
import ReportView from "./index";

const path = "artifacts/review/1/report.md";

const report = [
  "## Review",
  "",
  "| Criterion | Result | Note |",
  "| -- | -- | -- |",
  "| The tests fail before the fix | passed | |",
  "| No test checks the wording | fail | not fail at all |",
  "| Unknown words stay | maybe | `fail` |",
  "",
  '<script>alert(1)</script> and <img src=x onerror="alert(2)">',
].join("\n");

const deliverable = (reached: boolean): Deliverable => ({
  ...must(records.passed.deliverables?.find((d) => d.kind === "report")),
  path: reached ? path : null,
  reason: reached ? null : "review was never visited",
});

function show(
  files: Record<string, FakeEntry>,
  reached = true,
  record: AttemptRecord = records.passed,
) {
  const fetch = fakeFiles(files);
  render(
    <MemoryRouter>
      <ReportView
        project={sampleProject}
        attempt="a/b"
        record={record}
        deliverable={deliverable(reached)}
      />
    </MemoryRouter>,
  );
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("where a report came from", () => {
  it("names its run, and the later run a files deliverable came from", async () => {
    // The task was made again in run 6, after the review of run 5.
    const later = {
      ...records.passed,
      deliverables: records.passed.deliverables?.map((d) =>
        d.kind === "files" ? { ...d, visit_index: 5 } : d,
      ),
    };
    show({ [path]: report }, true, later);

    const byline = (
      await screen.findByRole("link", { name: /· run 5$/ })
    ).closest("p");
    expect(byline).toHaveTextContent(
      /^From .* · run 5, before the .*, which is from .* · run 6$/,
    );
  });

  it("names only its run when no files deliverable is later", async () => {
    show({ [path]: report });

    const byline = (
      await screen.findByRole("link", { name: /· run 5$/ })
    ).closest("p");
    expect(byline).toHaveTextContent(/^From .* · run 5$/);
  });
});

describe("the report view", () => {
  it("draws a table cell that is a status as its label", async () => {
    show({ [path]: report });

    const table = await screen.findByRole("table");
    const passed = screen.getByText("Passed");
    const fail = screen.getByText("Fail");
    expect(passed.closest("td")).toHaveTextContent(/^Passed$/);
    expect(passed).toHaveClass("text-good-label-fg");
    expect(fail).toHaveClass("text-bad-label-fg");
    expect(table).toHaveTextContent("not fail at all");
    expect(table).toHaveTextContent("maybe");
    expect(screen.getByText("fail", { selector: "code" })).toBeVisible();
  });

  it("renders the report's raw HTML as text", async () => {
    show({ [path]: report });

    await screen.findByRole("table");
    expect(document.body.querySelector("script, img")).toBeNull();
    expect(screen.getByText(/alert\(1\)/)).toHaveTextContent(
      '<script>alert(1)</script> and <img src=x onerror="alert(2)">',
    );
  });

  it("titles the report and names its file and size", async () => {
    show({ [path]: "# Short\n" });

    expect(screen.getByRole("heading", { name: "Review" })).toBeVisible();
    expect(await screen.findByText(/· 8 B/)).toBeVisible();
    expect(screen.getByText("report.md")).toBeVisible();
  });

  it("shows the reason of a report that was not reached", () => {
    const fetch = show({}, false);

    expect(screen.getByText("Not run: review was never visited")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows only the size of a report that is not text", async () => {
    show({ [path]: new Uint8Array([0xff, 0xfe]) });

    expect(
      await screen.findByText("Not shown: not UTF-8 text, 2 B."),
    ).toBeVisible();
  });

  it("shows the route's reason for a report that is not there", async () => {
    show({});

    expect(await screen.findByText(`${path} does not exist`)).toBeVisible();
  });
});
