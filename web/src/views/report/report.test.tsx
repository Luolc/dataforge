import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { must, records, sampleProject } from "../../../fixtures/sample";
import type { Deliverable } from "../../api/types";
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
  ...must(records.passed.deliverables.find((d) => d.kind === "report")),
  path: reached ? path : null,
  reason: reached ? null : "review was never visited",
});

function show(files: Record<string, FakeEntry>, reached = true) {
  const fetch = fakeFiles(files);
  render(
    <ReportView
      project={sampleProject}
      attempt="a/b"
      record={records.passed}
      deliverable={deliverable(reached)}
    />,
  );
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the report view", () => {
  it("draws a table cell that is a status as its label", async () => {
    show({ [path]: report });

    const table = await screen.findByRole("table");
    const passed = screen.getByText("Passed");
    const fail = screen.getByText("Fail");
    expect(passed.closest("td")).toHaveTextContent(/^Passed$/);
    expect(passed).toHaveClass("text-good");
    expect(fail).toHaveClass("text-bad");
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
});
