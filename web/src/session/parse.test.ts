import { describe, expect, it } from "vitest";

import solved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import { must } from "../../fixtures/sample";
import { type Block, type Message, parseTrace, type ToolCall } from "./parse";

const messages = (entries: ReturnType<typeof parseTrace>["entries"]) =>
  entries.filter((e): e is Message => e.kind === "message");
const tools = (blocks: Block[]) =>
  blocks.filter((b): b is ToolCall => b.kind === "tool");

describe("parseTrace", () => {
  it("gives each tool call its result and the totals", () => {
    const session = parseTrace(solved);

    const first = must(messages(session.entries)[0]);
    expect(first.blocks.map((b) => b.kind)).toEqual([
      "thinking",
      "text",
      "tool",
      "tool",
      "tool",
      "tool",
      "tool",
      "tool",
    ]);
    const read = must(tools(first.blocks)[0]);
    expect(read.name).toBe("Read");
    expect(read.output).toContain("def parse(block):");
    expect(read.failed).toBe(false);
    expect(session.tools).toBe(12);
    expect(session.totals).toEqual({
      seconds: 543,
      costUsd: 0.31,
      turns: 9,
      tokensIn: 3200 + 182000 + 9400,
      tokensOut: 14100,
      model: "example-model",
    });
  });

  it("marks a call whose result is an error as failed", () => {
    const session = parseTrace(solved);

    const failed = messages(session.entries)
      .flatMap((m) => tools(m.blocks))
      .filter((t) => t.failed);
    expect(failed.map((t) => t.id)).toEqual(["t7"]);
    expect(session.failed).toBe(1);
  });

  it("puts a subagent's messages under the call that started it", () => {
    const session = parseTrace(solved);

    const top = messages(session.entries).flatMap((m) => tools(m.blocks));
    const task = must(top.find((t) => t.name === "Task"));
    expect(task.children).toHaveLength(3);
    expect(
      task.children.flatMap((m) => tools(m.blocks)).map((t) => t.id),
    ).toEqual(["s1", "s2"]);
    expect(top.map((t) => t.id)).not.toContain("s1");
    expect(task.output).toBe("Two callers: parser.py:3 and stream.py:41.");
  });

  it("counts thinking, recorded or not, and times each message", () => {
    expect(parseTrace(solved).thinking).toBe(2);
    const session = parseTrace(unsolved);
    expect(session.thinking).toBe(1);
    const first = must(messages(session.entries)[0]);
    expect(first.blocks[0]).toEqual({ kind: "thinking", text: "" });
    expect(first.at).toBe(0);
    expect(messages(session.entries)[1]?.at).toBeGreaterThan(30_000);
  });

  it("keeps a line that is not a JSON object as text where it stood", () => {
    const session = parseTrace(unsolved);

    const kinds = session.entries.map((e) => e.kind);
    expect(kinds).toEqual(["message", "bad", "message", "message"]);
    expect(session.entries[1]).toEqual({
      kind: "bad",
      line: 6,
      text: "this line is not JSON",
    });
    expect(session.preamble).toEqual([]);
  });

  it("keeps the lines before the first JSON object as the preamble", () => {
    const session = parseTrace(
      `Ignoring 2 entries: not trusted\n[1]\n\n${unsolved}`,
    );

    expect(session.preamble).toEqual([
      { kind: "bad", line: 1, text: "Ignoring 2 entries: not trusted" },
      { kind: "bad", line: 2, text: "[1]" },
    ]);
    expect(session.entries.map((e) => e.kind)).toEqual([
      "message",
      "bad",
      "message",
      "message",
    ]);
  });

  it("keeps every line of a trace with no JSON object where it stood", () => {
    const none = parseTrace('Error: no session\n[1]\n"x"\n');

    expect(none.preamble).toEqual([]);
    expect(none.entries).toEqual([
      { kind: "bad", line: 1, text: "Error: no session" },
      { kind: "bad", line: 2, text: "[1]" },
      { kind: "bad", line: 3, text: '"x"' },
    ]);
  });

  it("gives no totals when the trace has no result", () => {
    const session = parseTrace("");

    expect(session.entries).toEqual([]);
    expect(session.totals.seconds).toBeNull();
    expect(session.totals.tokensIn).toBeNull();
  });
});
