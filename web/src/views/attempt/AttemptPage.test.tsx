import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import older from "../../../fixtures/oldrec/attempts/older/task-0301/record.json";
import other from "../../../fixtures/oldrec/attempts/older/task-0302/record.json";
import resumed from "../../../fixtures/attempts/resume/task-0401/record.json";
import {
  inReview,
  mayHaveStopped,
  must,
  records,
  runningIn,
  sampleProject,
} from "../../../fixtures/sample";
import { POLL_MS } from "../../api/poll";
import type { LiveInfo, Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { AttemptPage } from "./AttemptPage";

const project: Project = {
  ...sampleProject,
  states: { author: { title: "Author" }, check: { title: "Check" } },
};

function show(body: unknown, at = "") {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(body), { status: 200 })),
    ),
  );
  render(
    <ProjectsContext value={{ state: "ok", data: [project] }}>
      <MemoryRouter
        initialEntries={[`/${project.id}/attempt?id=older/task-0301${at}`]}
      >
        <Routes>
          <Route path=":project/attempt" element={<AttemptPage />} />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const tabs = () =>
  within(screen.getByRole("navigation", { name: "Attempt" })).getAllByRole(
    "link",
  );

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a record an older dataforge wrote", () => {
  it("shows its Summary and says what it lacks", async () => {
    show(older);

    expect(await screen.findByText("This record has no budget.")).toBeVisible();
    expect(screen.getByText("This record has no graph.")).toBeVisible();
    expect(screen.getByText("This record has no summaries.")).toBeVisible();
    expect(screen.getByText("This record has no deliverables.")).toBeVisible();
    expect(
      screen.getByText("Deliverables", { selector: "dt" }).nextSibling,
    ).toHaveTextContent("—");
    expect(screen.getByText("Passed")).toBeVisible();
    expect(tabs().map((t) => t.textContent)).toEqual(["Runs4"]);
  });

  it("opens a run with no round in its heading", async () => {
    show(older, "&tab=runs&run=2");

    const run = await screen.findByRole("region", { name: "Author · run 3" });
    expect(
      within(run).getByText(/^Run 3 of 4 · visit 2 of Author/),
    ).toBeVisible();
    expect(within(run).queryByText(/round/)).toBeNull();
    const input = within(run)
      .getAllByRole("button")
      .find((b) => b.textContent.startsWith("Input"));
    fireEvent.click(input ?? run);
    expect(
      within(run).getByText(/This record has no graph, so whether/),
    ).toBeVisible();
  });
});

describe("a resumed record", () => {
  it("opens the discarded run the address names", async () => {
    show(resumed, "&tab=runs&discarded=0");

    expect(
      await screen.findByRole("heading", { name: "Check · visit 2" }),
    ).toBeVisible();
    expect(
      screen.getByText(/^Left out when the attempt was resumed/),
    ).toBeVisible();
    expect(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole(
        "link",
        { name: "older/task-0301" },
      ),
    ).toHaveAttribute("href", "/sample/attempt?id=older%2Ftask-0301");
  });
});

describe("a record of another shape", () => {
  it("shows the record as written, under why it is not read", async () => {
    show(other);

    expect(
      await screen.findByText(
        "This record is not in a shape the viewer knows (it has no visits). Below is the record as written.",
      ),
    ).toBeVisible();
    const written = screen.getByRole("region", {
      name: "The record as written",
    });
    expect(written).toHaveTextContent('"kept_round": null');
    expect(tabs().map((t) => t.textContent)).toEqual(["Runs"]);
  });

  it("shows raw text from the record as text", async () => {
    show({ visits: "<img src=x onerror=alert(1)>" });

    const written = await screen.findByRole("region", {
      name: "The record as written",
    });
    expect(written).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(written.querySelector("img")).toBeNull();
  });
});

describe("a record that is not a JSON object", () => {
  it("fails the page", async () => {
    show([older]);

    expect(
      await screen.findByText("The record is not a JSON object."),
    ).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Attempt" })).toBeNull();
  });
});

describe("an attempt that runs", () => {
  function showLive(live: unknown, status = 200, rec: unknown = inReview) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          url.includes("/live?")
            ? new Response(JSON.stringify(live), { status })
            : new Response(JSON.stringify(rec), { status: 200 }),
        ),
      ),
    );
    render(
      <ProjectsContext value={{ state: "ok", data: [project] }}>
        <MemoryRouter initialEntries={[`/${project.id}/attempt?id=b/t`]}>
          <Routes>
            <Route path=":project/attempt" element={<AttemptPage />} />
          </Routes>
        </MemoryRouter>
      </ProjectsContext>,
    );
  }

  it("turns its icon and counts the run it is in", async () => {
    showLive(runningIn());

    expect(
      await screen.findByRole("heading", { name: /Attempt/ }),
    ).toBeVisible();
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );
    expect(within(header).getByRole("img", { name: "Running" })).toBeVisible();
    expect(tabs()[0]).toHaveTextContent("Runs5");
  });

  it("marks one that may have stopped as such, with no run it is in", async () => {
    showLive(mayHaveStopped);

    expect(
      await screen.findByRole("heading", { name: /Attempt/ }),
    ).toBeVisible();
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );
    expect(
      within(header).getByRole("img", { name: "May have stopped" }),
    ).toBeVisible();
    expect(tabs()[0]).toHaveTextContent("Runs4");
  });

  it("shows a record that has ended alone over a /live read before", async () => {
    // /live read while the attempt ran, the record once it had ended.
    const visits = records.passed.visits.length;
    showLive(runningIn({ index: visits }), 200, records.passed);

    expect(
      await screen.findByRole("heading", { name: /Attempt/ }),
    ).toBeVisible();
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );
    expect(within(header).getByRole("img", { name: "Passed" })).toBeVisible();
    expect(tabs()[0]).toHaveTextContent(`Runs${String(visits)}`);
    expect(screen.queryByRole("img", { name: "Running" })).toBeNull();
    expect(
      screen.getByText(`${String(visits)} runs`, { exact: false }),
    ).toBeVisible();
  });

  it("shows the record alone when /live cannot be read", async () => {
    showLive({ error: "not found", reason: "no route" }, 404);

    expect(
      await screen.findByRole("heading", { name: /Attempt/ }),
    ).toBeVisible();
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );
    expect(
      within(header).getByRole("img", { name: "Not ended" }),
    ).toBeVisible();
    expect(tabs()[0]).toHaveTextContent("Runs4");
  });
});

describe("an attempt read again while it runs", () => {
  // Each read of the record and of /live answers with the next of reads, the
  // last one again once they run out.
  function showReads(reads: { record: unknown; live: LiveInfo }[], at = "") {
    let n = 0;
    const fetch = vi.fn((url: string) => {
      const read = must(reads[Math.min(n, reads.length - 1)]);
      if (url.includes("/live?")) n++;
      return Promise.resolve(
        new Response(
          JSON.stringify(url.includes("/live?") ? read.live : read.record),
          { status: 200 },
        ),
      );
    });
    vi.stubGlobal("fetch", fetch);
    render(
      <ProjectsContext value={{ state: "ok", data: [project] }}>
        <MemoryRouter initialEntries={[`/${project.id}/attempt?id=b/t${at}`]}>
          <Routes>
            <Route path=":project/attempt" element={<AttemptPage />} />
          </Routes>
        </MemoryRouter>
      </ProjectsContext>,
    );
    return fetch;
  }

  // The record once its review has ended, in its solve, which it has not
  // recorded yet.
  const reviewed = {
    record: { ...inReview, visits: records.passed.visits.slice(0, 5) },
    live: runningIn({ state: "solve", index: 5, since: null }),
  };

  const tick = async () => {
    act(() => {
      vi.advanceTimersByTime(POLL_MS);
    });
    // Let the reads it starts land.
    await act(() => Promise.resolve());
  };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the run it goes on to, with no reload", async () => {
    const fetch = showReads([
      { record: inReview, live: runningIn() },
      reviewed,
    ]);
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs5");
    });
    expect(fetch).toHaveBeenCalledTimes(2);

    await tick();

    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs6");
    });
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("stops reading once the record has ended", async () => {
    const fetch = showReads([
      { record: inReview, live: runningIn() },
      { record: records.passed, live: runningIn() },
    ]);
    const header = must(
      (await screen.findByRole("heading", { name: /Attempt/ })).parentElement,
    );
    expect(within(header).getByRole("img", { name: "Running" })).toBeVisible();

    await tick();
    await waitFor(() => {
      expect(within(header).getByRole("img", { name: "Passed" })).toBeVisible();
    });
    const reads = fetch.mock.calls.length;
    await tick();
    await tick();
    expect(fetch).toHaveBeenCalledTimes(reads);
  });

  it("does not read again one that may have stopped", async () => {
    const fetch = showReads([{ record: inReview, live: mayHaveStopped }]);
    await screen.findByRole("img", { name: "May have stopped" });

    await tick();
    await tick();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps the run chosen and the steps opened on it", async () => {
    showReads([{ record: inReview, live: runningIn() }, reviewed], "&run=3");
    const run = await screen.findByRole("region", { name: /run 4/ });
    const step = must(
      within(run).getAllByRole("button", { expanded: false })[0],
    );
    fireEvent.click(step);
    expect(step).toHaveAttribute("aria-expanded", "true");

    await tick();

    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs6");
    });
    expect(screen.getByRole("region", { name: /run 4/ })).toBe(run);
    expect(step).toHaveAttribute("aria-expanded", "true");
  });
});
