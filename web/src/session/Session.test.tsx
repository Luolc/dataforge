import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import solved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import { sampleProject } from "../../fixtures/sample";
import type { Project } from "../api/types";
import { SessionView } from ".";
import { parseTrace } from "./parse";
import { SessionBody, type ThinkingMode } from "./Session";

function show(text: string, thinking: ThinkingMode = "collapsed") {
  return render(
    <SessionBody
      session={parseTrace(text)}
      thinking={thinking}
      badge={<span>the badge</span>}
    />,
  );
}

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

const toolLine = (name: string | RegExp) =>
  screen.getByRole("button", { name });

describe("the session view", () => {
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

  it("hides all thinking with one toggle, and starts so when hidden", () => {
    show(solved, "collapsed");
    fireEvent.click(screen.getByRole("button", { name: "Hide thinking" }));
    expect(screen.queryByText(/Thinking ·/)).toBeNull();
    screen.getByRole("button", { name: "Show thinking · 2 hidden" });

    show(solved, "hidden");
    expect(screen.queryByText(/Thinking ·/)).toBeNull();
  });

  it("renders a trace with no thinking as usual, with no toggle", () => {
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
    expect(await screen.findByText("Hide thinking")).toBeVisible();
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
    expect(screen.queryByText("Hide thinking")).toBeNull();
  });
});
