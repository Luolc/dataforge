import { describe, expect, it } from "vitest";

import solved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-1/trace.jsonl?raw";
import unsolved from "../../fixtures/p3/attempts/pass-at-k/artifacts/solve/1/trial-2/trace.jsonl?raw";
import { must } from "../../fixtures/sample";
import {
  type Block,
  type Message,
  parseTrace,
  type ToolCall,
  transcriptOutput,
} from "./parse";

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
      model: "example-model",
    });
    expect(session.last).toEqual({
      id: "msg_0009",
      input: 12,
      output: 80,
      final: null,
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

  it("counts only thinking with text, and times each message", () => {
    expect(parseTrace(solved).thinking).toBe(2);
    const session = parseTrace(unsolved);
    expect(session.thinking).toBe(0);
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
    expect(session.totals.turns).toBeNull();
    expect(session.last).toBeNull();
  });

  it("takes the last top-level call, past a subagent's and a retracted one", () => {
    const event = (id: string, usage: object, more: object = {}): string =>
      JSON.stringify({
        type: "assistant",
        message: { id, content: [{ type: "text", text: id }], usage },
        ...more,
      });
    const usage = {
      input_tokens: 2,
      cache_read_input_tokens: 38852,
      cache_creation_input_tokens: 897,
      output_tokens: 3,
    };
    const session = parseTrace(
      [
        JSON.stringify({ type: "system", subtype: "init", session_id: "s-9" }),
        event("m1", { input_tokens: 5, output_tokens: 9 }),
        event("m2", usage, { uuid: "u2a" }),
        event("m2", usage, { uuid: "u2b" }),
        event("m3", { input_tokens: 7 }, { parent_tool_use_id: "t1" }),
        event("m4", { input_tokens: 1 }, { uuid: "u4" }),
        JSON.stringify({
          type: "system",
          subtype: "model_refusal_fallback",
          retracted_message_uuids: ["u4"],
        }),
      ].join("\n"),
    );

    expect(session.sessionId).toBe("s-9");
    expect(session.last).toEqual({
      id: "m2",
      input: 39751,
      output: 3,
      final: null,
    });
  });

  it("reads a one-call session's final output from its result line, and none for more calls", () => {
    const usage = { input_tokens: 2, cache_creation_input_tokens: 16889 };
    const call = (id: string) =>
      JSON.stringify({
        type: "assistant",
        message: {
          id,
          content: [{ type: "text", text: id }],
          usage: { ...usage, output_tokens: 2 },
        },
      });
    const result = (input: number, output: number) =>
      JSON.stringify({
        type: "result",
        usage: { input_tokens: input, output_tokens: output },
      });

    expect(
      parseTrace([call("m1"), result(16891, 1831)].join("\n")).last,
    ).toEqual({ id: "m1", input: 16891, output: 2, final: 1831 });
    // Two calls: the result sums them, so it is not the last call's.
    expect(
      parseTrace([call("m1"), call("m2"), result(33782, 3662)].join("\n")).last
        ?.final,
    ).toBeNull();
    // A subagent's calls counted in the result's input.
    expect(
      parseTrace([call("m1"), result(20000, 2500)].join("\n")).last?.final,
    ).toBeNull();
    expect(parseTrace(call("m1")).last?.final).toBeNull();
  });

  it("reads a call's final output from a transcript, and none for a call it lacks", () => {
    const line = (id: string, output: number) =>
      JSON.stringify({
        type: "assistant",
        message: { id, usage: { input_tokens: 2, output_tokens: output } },
      });
    const transcript = [
      line("m1", 89),
      JSON.stringify({ type: "user", message: { content: "m2" } }),
      // Another call whose ID holds this one's.
      line("m21", 5),
      line("m2", 886),
      line("m2", 886),
    ].join("\n");

    expect(transcriptOutput(transcript, "m2")).toBe(886);
    expect(transcriptOutput(transcript, "m3")).toBeNull();
  });
});
