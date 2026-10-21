import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sampleProject } from "../../../fixtures/sample";
import type { Project } from "../../api/types";
import { ItemActions } from "./Actions";

const project: Project = {
  ...sampleProject,
  actions: [
    { name: "again", label: "Run again", queue: "fast" },
    { name: "go-on", label: "Go on", queue: "slow" },
  ],
};

const ACTIONS = "/api/projects/sample/actions?item=batch-1%2Ftask-0001";

const job = {
  id: 7,
  queue: "fast",
  labels: { project: "sample", action: "again", item: "batch-1/task-0001" },
  state: "queued",
  memory_mib: 2048,
  queued_at: 1772442900,
  started_at: null,
  ended_at: null,
  exit_code: null,
  cancel_requested_at: null,
};

type Reply = [number, unknown];

/** Answer each URL with the next of its replies, and keep each request. */
function serve(replies: Record<string, Reply[]>) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = replies[url]?.shift() ?? [404, {}];
      return Promise.resolve(new Response(JSON.stringify(body), { status }));
    }),
  );
  return calls;
}

function show() {
  render(
    <MemoryRouter>
      <ItemActions project={project} attempt="batch-1/task-0001/a1" />
    </MemoryRouter>,
  );
}

const states = (goOn: string | null, params: unknown[] = []) => [
  { name: "again", label: "Run again", queue: "fast", reason: null, params },
  { name: "go-on", label: "Go on", queue: "slow", reason: goOn, params: [] },
];

const params = [
  {
    name: "budget",
    label: "Budget",
    min: 1,
    max: 150,
    default: 75,
    format: "usd",
  },
  {
    name: "tries",
    label: "Tries",
    min: -2,
    max: 0.5,
    default: 0,
    format: "number",
  },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a data item's actions", () => {
  it("greys an action that may not be submitted, with its reason", async () => {
    serve({
      [ACTIONS]: [[200, states("The newest attempt of this item has ended.")]],
    });

    show();

    const goOn = await screen.findByRole("button", { name: "Go on" });
    expect(goOn).toBeDisabled();
    expect(goOn).toHaveAccessibleDescription(
      "The newest attempt of this item has ended.",
    );
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(screen.getByText("batch-1/task-0001")).toBeVisible();
  });

  it("asks before it queues, then posts the action's name and the item alone", async () => {
    const submit =
      "/api/projects/sample/actions/again?item=batch-1%2Ftask-0001";
    const calls = serve({
      [ACTIONS]: [
        [200, states(null)],
        [200, states("Job 7 for this item is queued in fast.")],
      ],
      [submit]: [[201, { job }]],
    });
    show();

    const again = await screen.findByRole("button", { name: "Run again" });
    expect(again).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(again);
    const question = screen.getByText(
      "Queue Run again for batch-1/task-0001 in fast?",
    );
    expect(question).toBeVisible();
    expect(again).toHaveAttribute("aria-expanded", "true");
    expect(again).toHaveAttribute(
      "aria-controls",
      question.parentElement?.id ?? "",
    );
    // The focus goes into the question, at its first button.
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Queue" }),
    );
    expect(calls.map((c) => c.url)).toEqual([ACTIONS]);
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(
      await screen.findByText("Run again: queued as job 7 in fast."),
    ).toBeVisible();
    // and then to what came of it, in an effect of the render that shows it.
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("status"));
    });
    const post = calls.find((c) => c.url === submit);
    expect(post?.init?.method).toBe("POST");
    expect(post?.init?.headers).toEqual({ "X-Viz-Write": "1" });
    expect(post?.init?.body).toBeUndefined();
    // Read again after the write: the action is now greyed.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Go on" })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });

  it("keeps nothing when told to keep", async () => {
    const calls = serve({ [ACTIONS]: [[200, states(null)]] });
    show();

    const goOn = await screen.findByRole("button", { name: "Go on" });
    fireEvent.click(goOn);
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    expect(goOn).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(goOn);
    expect(calls.every((c) => c.init?.method !== "POST")).toBe(true);
  });

  it("says why a write was refused", async () => {
    const submit =
      "/api/projects/sample/actions/go-on?item=batch-1%2Ftask-0001";
    serve({
      [ACTIONS]: [
        [200, states(null)],
        [200, states(null)],
      ],
      [submit]: [
        [
          409,
          {
            error: "conflict",
            reason: "An attempt of this item is running: its lock is held.",
          },
        ],
      ],
    });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Go on" }));
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(
      await screen.findByText(
        "Go on was not queued (409: An attempt of this item is running: its lock is held.).",
      ),
    ).toBeVisible();
  });

  it("asks for each number at its default, and posts the numbers with the item", async () => {
    const submit =
      "/api/projects/sample/actions/again?item=batch-1%2Ftask-0001&budget=12.5&tries=0";
    const calls = serve({
      [ACTIONS]: [
        [200, states(null, params)],
        [200, states(null, params)],
      ],
      [submit]: [[201, { job }]],
    });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Run again" }));
    const budget = screen.getByRole("textbox", { name: "Budget" });
    const tries = screen.getByRole("textbox", { name: "Tries" });
    expect(budget).toHaveValue("75");
    expect(tries).toHaveValue("0");
    expect(budget).toHaveAccessibleDescription("From $1.00 to $150.00.");
    expect(tries).toHaveAccessibleDescription("From -2 to 0.5.");
    // The focus goes to the first number, so that typing replaces it.
    expect(document.activeElement).toBe(budget);
    fireEvent.change(budget, { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(
      await screen.findByText(
        "Run again (Budget $12.50, Tries 0): queued as job 7 in fast.",
      ),
    ).toBeVisible();
    expect(
      calls.filter((c) => c.init?.method === "POST").map((c) => c.url),
    ).toEqual([submit]);
  });

  it.each([
    ["151"],
    ["0.5"],
    ["1e2"],
    [" 75"],
    ["75."],
    [""],
    ["1" + "0".repeat(15)],
    ["1.0000001"],
  ])("queues no number the backend would refuse, such as %j", async (value) => {
    const calls = serve({ [ACTIONS]: [[200, states(null, params)]] });
    show();

    fireEvent.click(await screen.findByRole("button", { name: "Run again" }));
    const budget = screen.getByRole("textbox", { name: "Budget" });
    fireEvent.change(budget, { target: { value } });

    const queue = screen.getByRole("button", { name: "Queue" });
    expect(queue).toBeDisabled();
    expect(budget).toHaveAttribute("aria-invalid", "true");
    expect(budget).toHaveAccessibleDescription(
      "Not a number from $1.00 to $150.00.",
    );
    // Enter in the input submits the form, which queues nothing either.
    const form = budget.closest("form");
    expect(form).not.toBeNull();
    if (form !== null) fireEvent.submit(form);
    expect(calls.every((c) => c.init?.method !== "POST")).toBe(true);
    // A control: a number in bounds makes Queue ready again.
    fireEvent.change(budget, { target: { value: "150" } });
    expect(queue).toBeEnabled();
    expect(budget).toHaveAttribute("aria-invalid", "false");
  });
});
