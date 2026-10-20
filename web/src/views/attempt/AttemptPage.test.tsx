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
import { fakeFiles } from "../files/fake-api";
import { AttemptPage } from "./AttemptPage";

const project: Project = {
  ...sampleProject,
  states: { author: { title: "Author" }, check: { title: "Check" } },
};

function show(body: unknown, at = "", shown: Project = project) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(body), { status: 200 })),
    ),
  );
  render(
    <ProjectsContext value={{ state: "ok", data: [shown] }}>
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

describe("a data item's actions", () => {
  it("show under the heading only when the project names any", async () => {
    show(older);
    await screen.findByText("Passed");
    expect(screen.queryByRole("region", { name: /^Actions of/ })).toBeNull();
  });

  it("act on the attempt's item", async () => {
    show(older, "", {
      ...project,
      actions: [{ name: "again", label: "Run again", queue: "fast" }],
    });
    await screen.findByText("Passed");
    expect(
      screen.getByRole("region", { name: "Actions of Task older/task-0301" }),
    ).toBeVisible();
  });
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
  // A read of "gone" fails both, as when the server has gone away; a live
  // of "gone" fails /live alone.
  function showReads(
    reads: ({ record: unknown; live: LiveInfo | "gone" } | "gone")[],
    at = "",
  ) {
    let n = 0;
    const fetch = vi.fn((url: string) => {
      const read = must(reads[Math.min(n, reads.length - 1)]);
      if (url.includes("/live?")) n++;
      const body =
        read === "gone"
          ? read
          : url.includes("/live?")
            ? read.live
            : read.record;
      return Promise.resolve(
        body === "gone"
          ? new Response(JSON.stringify({ reason: "Bad Gateway" }), {
              status: 502,
            })
          : new Response(JSON.stringify(body), { status: 200 }),
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
    // Let the effects of what has landed run first, the poll's timer among
    // them: a page found on screen may not have set it yet.
    await act(() => Promise.resolve());
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

  it("says when two reads again in a row fail, until one lands", async () => {
    showReads([
      { record: inReview, live: runningIn() },
      "gone",
      "gone",
      reviewed,
    ]);
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs5");
    });
    const said = () => screen.queryByText(/^Could not read the attempt again/);

    await tick();
    // Let the failed reads land.
    await act(() => new Promise((r) => setTimeout(r, 50)));
    // One failure may be a record caught half written: nothing yet.
    expect(said()).toBeNull();
    await tick();
    await waitFor(() => {
      expect(said()).toHaveTextContent(
        "Could not read the attempt again (502: Bad Gateway); what is shown was read at",
      );
    });
    expect(tabs()[0]).toHaveTextContent("Runs5");

    await tick();
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs6");
    });
    expect(said()).toBeNull();
  });

  it("takes a /live that cannot read a record not ended for a failed read", async () => {
    // As /live answers while the record is caught half written.
    const unread = {
      record: inReview,
      live: { live: null, held: true, current: null },
    };
    showReads([
      { record: inReview, live: runningIn() },
      unread,
      unread,
      reviewed,
    ]);
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs5");
    });
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );

    await tick();
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(tabs()[0]).toHaveTextContent("Runs5");
    expect(within(header).getByRole("img", { name: "Running" })).toBeVisible();
    await tick();
    expect(
      await screen.findByText(
        /^Could not read the attempt again \(\/live could not read the record\)/,
      ),
    ).toBeVisible();

    await tick();
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs6");
    });
  });

  it("keeps what it showed when /live alone cannot be read again", async () => {
    showReads([
      { record: inReview, live: runningIn() },
      { record: inReview, live: "gone" },
      { record: inReview, live: "gone" },
      reviewed,
    ]);
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs5");
    });
    const header = must(
      screen.getByRole("heading", { name: /Attempt/ }).parentElement,
    );

    await tick();
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(tabs()[0]).toHaveTextContent("Runs5");
    expect(within(header).getByRole("img", { name: "Running" })).toBeVisible();
    await tick();
    expect(
      await screen.findByText(
        /^Could not read the attempt again \(502: Bad Gateway\)/,
      ),
    ).toBeVisible();

    await tick();
    await waitFor(() => {
      expect(tabs()[0]).toHaveTextContent("Runs6");
    });
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

describe("a later visit of an attempt", () => {
  it("counts its files again", async () => {
    const root = "artifacts/author/2/task";
    const files: Record<string, string> = { [`${root}/a.txt`]: "a" };
    const answer = fakeFiles(files);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.includes("/record?")
          ? Promise.resolve(Response.json(records.passed))
          : url.includes("/live?")
            ? Promise.resolve(
                Response.json({ live: null, held: null, current: null }),
              )
            : answer(url),
      ),
    );
    const visit = () =>
      render(
        <ProjectsContext value={{ state: "ok", data: [project] }}>
          <MemoryRouter
            initialEntries={[`/${project.id}/attempt?id=later/visit`]}
          >
            <Routes>
              <Route path=":project/attempt" element={<AttemptPage />} />
            </Routes>
          </MemoryRouter>
        </ProjectsContext>,
      );
    const first = visit();
    await waitFor(() => {
      expect(tabs()[1]).toHaveTextContent("Task1");
    });
    first.unmount();

    files[`${root}/b.txt`] = "b";
    visit();

    await waitFor(() => {
      expect(tabs()[1]).toHaveTextContent("Task2");
    });
  });
});

describe("an attempt whose server stops answering", () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Advance the page's timers by ms, letting what they start land.
  const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  it("says so once two reads again have had no answer, one read out at a time", async () => {
    let answering = true;
    let out = 0;
    let most = 0;
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      if (!answering) {
        most = Math.max(most, ++out);
        init?.signal?.addEventListener("abort", () => {
          out--;
        });
        // An answer that never comes, as from a machine gone off the network.
        return new Promise<Response>(() => undefined);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify(url.includes("/live?") ? runningIn() : inReview),
          { status: 200 },
        ),
      );
    });
    vi.stubGlobal("fetch", fetch);
    render(
      <ProjectsContext value={{ state: "ok", data: [project] }}>
        <MemoryRouter initialEntries={[`/${project.id}/attempt?id=b/t`]}>
          <Routes>
            <Route path=":project/attempt" element={<AttemptPage />} />
          </Routes>
        </MemoryRouter>
      </ProjectsContext>,
    );
    await advance(0);
    expect(tabs()[0]).toHaveTextContent("Runs5");
    const said = () => screen.queryByText(/^Could not read the attempt again/);
    // The status region is there before the line, to announce it.
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    answering = false;
    // One tick at a time, so that what each starts runs before the next.
    for (let tick = 0; tick < 2; tick++) await advance(POLL_MS);
    // The first read again has had no answer: one failure, nothing said.
    expect(said()).toBeNull();
    await advance(POLL_MS);

    expect(screen.getByRole("status")).toHaveTextContent(
      /^Could not read the attempt again \(no answer in 5s\); what is shown was read at/,
    );
    expect(tabs()[0]).toHaveTextContent("Runs5");
    // Each read again is of the record and /live, given up on before the
    // next is sent: never more than one out.
    expect(most).toBe(2);
  });
});
