import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import solved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import { sampleProject } from "../../fixtures/sample";
import type { Project } from "../api/types";
import { SessionView } from ".";
import { parseTrace } from "./parse";
import { SessionBody, type ThinkingMode } from "./Session";

function show(
  text: string,
  thinking: ThinkingMode = "collapsed",
  options: { startOpen?: boolean; totals?: "all" | "model" } = {},
) {
  return render(
    <SessionBody
      session={parseTrace(text)}
      thinking={thinking}
      badge={<span>the badge</span>}
      {...options}
    />,
  );
}

const folds = () =>
  screen
    .getAllByRole("button")
    .filter((button) => button.hasAttribute("aria-expanded"));

const call = (id: string, name = "Bash") =>
  JSON.stringify({
    type: "assistant",
    message: {
      id: "m1",
      content: [
        { type: "tool_use", id, name, input: { command: `run ${id}` } },
      ],
    },
  });

const emptyThinking = [
  {
    type: "assistant",
    message: {
      id: "m0",
      content: [{ type: "thinking", thinking: "", signature: "sig" }],
    },
  },
  {
    type: "assistant",
    message: {
      id: "m1",
      content: [
        { type: "thinking", thinking: " \n", signature: "sig" },
        {
          type: "tool_use",
          id: "c1",
          name: "Bash",
          input: { command: "run c1" },
        },
      ],
    },
  },
  {
    type: "assistant",
    message: {
      id: "m2",
      content: [{ type: "text", text: "Done." }],
    },
  },
]
  .map((line) => JSON.stringify(line))
  .join("\n");

/** The text of each message's time in the gutter. */
const gutter = (container: HTMLElement) =>
  [...container.querySelectorAll("ol > li > span:first-child")].map(
    (cell) => cell.textContent,
  );

const toolLine = (name: string | RegExp) =>
  screen.getByRole("button", { name });

describe("the session view", () => {
  // The page's tab bar sticks at the top of the scroll, so the totals stick
  // below it, as far down as its height token; jsdom lays nothing out, so the
  // test reads the class that sets the offset.
  it("sticks its totals below the page's tab bar, not over it", () => {
    show(solved);

    const header = screen.getByText("the badge").closest("header");
    expect(header).toHaveClass("sticky", "top-tabs");
    expect(header).not.toHaveClass("top-0");
  });

  it("opens a failed call from the start and the others on a click", () => {
    show(solved);

    const failed = toolLine(/^Bashpython -c .*failed$/);
    expect(failed).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/AssertionError: \{b'host'/)).toBeVisible();

    const passing = toolLine(/^Bashpython -m pytest -q$/);
    expect(passing).toHaveAttribute("aria-expanded", "false");
    const part = document.getElementById(
      must(passing.getAttribute("aria-controls")),
    );
    expect(part).toHaveTextContent(/41 passed in 3.02s/);
    expect(part).not.toBeVisible();
    fireEvent.click(passing);
    expect(passing).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/41 passed in 3.02s/)).toBeVisible();
    expect(
      document.getElementById(must(passing.getAttribute("aria-controls"))),
    ).not.toBeNull();
  });

  it("folds more than 4 calls of one message into one line", () => {
    show(solved);

    const group = toolLine(
      /^6 tool callsRead ×3, Grep ×1, Bash ×20 of 6 failed$/,
    );
    expect(group).toHaveAttribute("aria-expanded", "false");
    const part = document.getElementById(
      must(group.getAttribute("aria-controls")),
    );
    expect(part).not.toBeVisible();
    expect(screen.queryByRole("button", { name: /^Grepdef parse/ })).toBeNull();
    fireEvent.click(group);
    expect(part).toBeVisible();
    expect(toolLine(/^Grepdef parse/)).toBeVisible();
  });

  it("keeps 4 calls of one message as 4 lines", () => {
    show([1, 2, 3, 4].map((i) => call(`c${i}`)).join("\n"));

    expect(
      screen.getAllByRole("button", { name: /^Bashrun c\d/ }),
    ).toHaveLength(4);
    expect(screen.queryByText(/tool calls Bash/)).toBeNull();
  });

  it("indents a subagent's calls under the call that started it", () => {
    show(solved);

    const subagent = screen.getByRole("list", { name: "Subagent of Task" });
    expect(
      within(subagent).getByRole("button", { name: /^Grep_split_line/ }),
    ).toBeVisible();
    expect(
      toolLine(/^TaskFind the callers of _split_linesubagent$/),
    ).toBeVisible();
  });

  it("shows thinking as one line by default, which opens", () => {
    show(solved, "collapsed");

    const line = toolLine(/^Thinking · 21 wordsStart by reproducing/);
    expect(line).toHaveAttribute("aria-expanded", "false");
    const part = must(line.getAttribute("aria-controls"));
    expect(document.getElementById(part)).not.toBeVisible();
    fireEvent.click(line);
    expect(document.getElementById(part)).toHaveTextContent(
      /the parser splits on CRLF\.$/,
    );
  });

  it("shows thinking in full when the project says expanded", () => {
    show(solved, "expanded");

    const line = toolLine(/^Thinking · 21 words$/);
    expect(line).toHaveAttribute("aria-expanded", "true");
    expect(
      document.getElementById(must(line.getAttribute("aria-controls"))),
    ).toHaveTextContent(/the parser splits on CRLF\.$/);
  });

  it("shows no thinking when the project says hidden, and has no toggle", () => {
    show(solved, "hidden");

    expect(screen.queryByText(/Thinking ·/)).toBeNull();
    expect(screen.queryByRole("button", { name: /thinking/i })).toBeNull();
  });

  it("has no show or hide thinking button when thinking shows", () => {
    show(solved, "collapsed");

    expect(
      screen.queryByRole("button", { name: /^(Show|Hide) thinking/ }),
    ).toBeNull();
  });

  it("draws a thinking block with no text as nothing, not even a row", () => {
    const { container } = show(emptyThinking);

    expect(screen.queryByText(/Thinking ·/)).toBeNull();
    expect(screen.queryByText(/not in the trace/)).toBeNull();
    expect(toolLine(/^Bashrun c1/)).toBeVisible();
    expect(container.querySelectorAll("section > ol > li")).toHaveLength(2);
  });

  it("opens every fold with Expand all and closes every one with Collapse all", () => {
    show(solved, "collapsed");
    expect(folds().some((b) => b.ariaExpanded === "false")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(folds().every((b) => b.ariaExpanded === "true")).toBe(true);
    expect(screen.getByText(/41 passed in 3.02s/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(folds().every((b) => b.ariaExpanded === "false")).toBe(true);

    fireEvent.click(toolLine(/^Bashpython -m pytest -q$/));
    expect(toolLine(/^Bashpython -m pytest -q$/)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("opens a thinking block with text on Expand all and closes it on Collapse all", () => {
    show(solved, "collapsed");
    const line = toolLine(/^Thinking · 21 words/);
    expect(line).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(toolLine(/^Thinking · 21 words$/)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(toolLine(/^Thinking · 21 words/)).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("starts with every fold open when told to", () => {
    show(solved, "collapsed", { startOpen: true });

    expect(folds().every((b) => b.ariaExpanded === "true")).toBe(true);
    expect(screen.getByRole("button", { name: "Collapse all" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(folds().every((b) => b.ariaExpanded === "false")).toBe(true);
  });

  it("shows every total in the header by default", () => {
    const { container } = show(solved);

    const header = container.querySelector("header");
    expect(header).toHaveTextContent("session 9m 3s");
    expect(header).toHaveTextContent("cost $0.31");
    expect(header).toHaveTextContent("turns 9");
    expect(header).toHaveTextContent("tokens 195K in · 14K out");
    expect(header).toHaveTextContent("model example-model");
    expect(header).toHaveTextContent("12 tool calls · 1 failed");
    expect(
      gutter(container).filter((cell) => cell !== "").length,
    ).toBeGreaterThan(0);
  });

  it("shows the model alone in the header when told to", () => {
    const { container } = show(solved, "collapsed", { totals: "model" });

    const header = container.querySelector("header");
    expect(header).toHaveTextContent("model example-model");
    expect(header).not.toHaveTextContent(
      /duration|cost|turns|tokens|tool calls/,
    );
    expect(header).toHaveTextContent("the badge");
    expect(screen.getByRole("button", { name: "Expand all" })).toBeVisible();
    expect(gutter(container).every((cell) => cell === "")).toBe(true);
  });

  it("renders a trace with no thinking as usual", () => {
    show(call("c1"));

    expect(toolLine(/^Bashrun c1/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /thinking/ })).toBeNull();
  });

  it("shows raw HTML from the trace as text", () => {
    const { container } = show(unsolved);

    expect(
      screen.getByText(/<b>bold<\/b> and <img src=x onerror/),
    ).toBeVisible();
    fireEvent.click(toolLine(/^Bashcat \/app\/docs\/index.html/));
    expect(screen.getByText(/<script>alert\(3\)<\/script>/)).toBeVisible();
    expect(container.querySelector("b, img, script, h1")).toBeNull();
  });

  it("folds the lines before the session into one line, as text", () => {
    const { container } = show(
      `Ignoring 2 entries: <img src=x onerror=alert(1)>\nsecond line\n${unsolved}`,
    );

    const line = screen.getByRole("button", { name: /^Ignoring 2 entries/ });
    expect(line).toHaveTextContent(
      "Ignoring 2 entries: <img src=x onerror=alert(1)> · 1 more line",
    );
    expect(line).toHaveAttribute("aria-expanded", "false");
    const all = document.getElementById(
      must(line.getAttribute("aria-controls")),
    );
    if (all === null) throw new Error("missing");
    expect(all).not.toBeVisible();
    expect(screen.queryByText(/^Line [12] is not JSON/)).toBeNull();

    fireEvent.click(line);

    expect(all).toBeVisible();
    expect(all.textContent).toBe(
      "Ignoring 2 entries: <img src=x onerror=alert(1)>\nsecond line",
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("reaches the folded lines before the session with Expand all and Collapse all", () => {
    show(`first line\nsecond line\n${unsolved}`);
    const line = screen.getByRole("button", { name: /^first line/ });
    expect(line).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(line).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(line).toHaveAttribute("aria-expanded", "false");
  });

  it("shows a trace with no JSON object line by line, not folded", () => {
    show("Error: no session\nsecond line\n");

    expect(screen.queryByRole("button", { name: /^Error/ })).toBeNull();
    expect(
      screen.getByText("Line 1 is not JSON, shown as it is:"),
    ).toBeVisible();
    expect(screen.getByText("Error: no session")).toBeVisible();
    expect(screen.getByText("second line")).toBeVisible();
  });

  it("shows a line that is not JSON as text", () => {
    show(unsolved);

    expect(
      screen.getByText("Line 6 is not JSON, shown as it is:"),
    ).toBeVisible();
    expect(screen.getByText("this line is not JSON")).toBeVisible();
  });
});

function must(value: string | null): string {
  if (value === null) throw new Error("missing");
  return value;
}

const project: Project = sampleProject;

function respond(response: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(response)),
  );
}

describe("SessionView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a trace over FILE_LIMIT as its path and the 413's reason", async () => {
    respond(
      Response.json(
        { error: "too large", reason: "the file has 70000000 bytes" },
        { status: 413 },
      ),
    );
    render(
      <SessionView
        project={project}
        attempt="a/b"
        path="artifacts/solve/1/trial-1/trace.jsonl"
        format="claude-code-stream-json"
      />,
    );

    expect(
      await screen.findByText(/413: the file has 70000000 bytes/),
    ).toBeVisible();
    expect(
      screen.getByText("artifacts/solve/1/trial-1/trace.jsonl"),
    ).toBeVisible();
  });

  it("parses stream-json and shows another format as text", async () => {
    respond(new Response(solved));
    const { unmount } = render(
      <SessionView
        project={project}
        attempt="a/b"
        path="t"
        format="claude-code-stream-json"
      />,
    );
    expect(await screen.findByText("Expand all")).toBeVisible();
    unmount();

    respond(new Response('{"type": "assistant"}\n<b>log</b>'));
    render(
      <SessionView
        project={project}
        attempt="a/b"
        path="t"
        format="plain-text-log"
      />,
    );
    expect(
      await screen.findByText(/\{"type": "assistant"\}\s+<b>log<\/b>/),
    ).toBeVisible();
    expect(screen.queryByText("Expand all")).toBeNull();
  });
});
