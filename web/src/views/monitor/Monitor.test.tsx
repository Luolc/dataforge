import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type Mock,
  vi,
} from "vitest";

import type { Project } from "../../api/types";
import { ProjectsContext } from "../../shell/projects";
import { groupBars, lanes } from "./containers";
import Monitor from "./Monitor";
import type { Bar } from "./types";

// uPlot draws on a canvas, which jsdom has not; the page's own parts are
// what these tests read.
vi.mock("uplot", () => ({
  default: class {
    destroy = vi.fn();
    setSize = vi.fn();
  },
}));

const project: Project = {
  id: "sample",
  name: "Sample dataset",
  color: "#e5a54b",
  states: { solve: { title: "Pass@k" } },
  statuses: {},
  item: { depth: 1, title: "Task" },
  columns: [],
  session: { thinking: "hidden" },
  monitor: true,
};

const T = 1_790_000_000;

function bar(id: string, more: Partial<Bar> = {}): Bar {
  return {
    container_id: id,
    start_t: T + 10,
    last_t: T + 100,
    image: "sample-task:1",
    item: "batch-1",
    attempt: "batch-1/task-0001",
    state: "solve",
    visit: 1,
    trial: "trial-0",
    exit_code: 0,
    open: false,
    ...more,
  };
}

const RUNS = [
  {
    run_id: 1,
    started_at: T - 5000,
    ended_at: T - 4000,
    stop_reason: "ended",
    source: "standalone",
  },
  {
    run_id: 2,
    started_at: T,
    ended_at: T + 600,
    stop_reason: "ended",
    source: "batch",
  },
];

const WINDOW = {
  run_id: 2,
  t0: T,
  t1: T + 600,
  bucket_s: 2,
  cpus: 32,
  mem_total_bytes: 2 ** 37,
  machine: [
    {
      t: T,
      cpu_cores_avg: 1.5,
      cpu_cores_max: 2,
      mem_used_bytes_avg: 2 ** 30,
      mem_used_bytes_max: 2 ** 31,
      swap_used_bytes_max: 0,
      load1_max: 1,
    },
  ],
  batch: [],
  pauses: [{ start_t: T + 50, end_t: T + 80 }],
  bars: [
    bar("c-1"),
    bar("c-2", { trial: "trial-1", exit_code: 137 }),
    bar("c-3", { attempt: "batch-1/task-0002", start_t: T + 300 }),
  ],
  bars_total: 3,
};

// jsdom has no ResizeObserver; the page measures nothing in these tests.
globalThis.ResizeObserver = class {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
};

let fetchMock: Mock<(url: string) => Promise<Response>>;

function answer(url: string): Response {
  if (url.includes("/monitor/runs"))
    return new Response(JSON.stringify(RUNS), { status: 200 });
  if (url.includes("/monitor/window"))
    return new Response(JSON.stringify(WINDOW), { status: 200 });
  return new Response(JSON.stringify([]), { status: 200 });
}

beforeEach(() => {
  fetchMock = vi.fn((url: string) => Promise.resolve(answer(url)));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const location = useLocation();
  return <output aria-label="where">{location.search}</output>;
}

function show(projects: Project[] = [project], url = "/monitor") {
  return render(
    <ProjectsContext value={{ state: "ok", data: projects }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="monitor"
            element={
              <>
                <Monitor />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </ProjectsContext>,
  );
}

const urls = () => fetchMock.mock.calls.map((c) => c[0]);

describe("the Monitor page", () => {
  it("says when no project names a monitor database", () => {
    show([{ ...project, monitor: false }]);

    expect(
      screen.getByText("No project of this site names a monitor database."),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reads the newest run's window and groups its containers by attempt", async () => {
    show();

    expect(await screen.findByText("batch-1/task-0001")).toBeInTheDocument();
    expect(urls()).toContain("/api/projects/sample/monitor/window?run=2");
    expect(
      screen.getByText(
        (_, el) => el?.textContent === "2 containers, 1 exited non-zero",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Paused after a 429 (1)")).toBeInTheDocument();
  });

  it("shows why the database cannot be read", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: "unavailable",
            reason: "the monitor database does not exist",
          }),
          { status: 503 },
        ),
      ),
    );
    show();

    expect(
      await screen.findByText(
        "The monitor cannot be read: 503: the monitor database does not exist",
      ),
    ).toBeInTheDocument();
  });

  it("draws a container's curve once it is chosen from the list", async () => {
    show();
    const heading = await screen.findByRole("button", {
      name: "batch-1/task-0001",
    });
    fireEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(
      screen.getByRole("button", { name: "Pass@k · visit 1 · trial-1" }),
    );

    expect(screen.getByLabelText("where")).toHaveTextContent("container=c-2");
    await screen.findByText("Container Pass@k · visit 1 · trial-1");
    expect(
      urls().some(
        (u) => u.includes("/monitor/curve?") && u.includes("container=c-2"),
      ),
    ).toBe(true);
  });

  it("reads again only on refresh", async () => {
    show();
    await screen.findByText("batch-1/task-0001");
    const before = fetchMock.mock.calls.length;

    await act(async () => {
      screen.getByRole("button", { name: "Refresh" }).click();
      await Promise.resolve();
    });

    expect(fetchMock.mock.calls.length).toBe(before + 2);
  });

  it("says how many containers the limit leaves out", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        url.includes("/window")
          ? new Response(JSON.stringify({ ...WINDOW, bars_total: 12000 }), {
              status: 200,
            })
          : answer(url),
      ),
    );
    show();

    expect(
      await screen.findByText(
        "3 of 12,000 containers shown; zoom in to see the rest.",
      ),
    ).toBeInTheDocument();
  });
});

describe("the containers' bars", () => {
  it("take the first lane free when they start", () => {
    const bars = [
      bar("a", { start_t: 0, last_t: 10 }),
      bar("b", { start_t: 5, last_t: 20 }),
      bar("c", { start_t: 11, last_t: 12 }),
    ];

    expect(lanes(bars)).toEqual([0, 1, 0]);
  });

  it("group by attempt, the groups by their first start", () => {
    const groups = groupBars([
      bar("a", { attempt: "x/2", start_t: 50 }),
      bar("b", { attempt: "x/1", start_t: 10 }),
      bar("c", { attempt: null, item: "x", start_t: 30 }),
    ]);

    expect(groups.map((g) => g.label)).toEqual([
      "x/1",
      "x · no attempt",
      "x/2",
    ]);
  });
});
