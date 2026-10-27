// A Claude Code stream-json trace (`claude -p --output-format stream-json`),
// parsed into what the session view draws. The trace is untrusted: every
// field is checked for its type, and a line that is not a JSON object is kept
// as text: before the first JSON object as the preamble, after it, or in a
// trace with no JSON object at all, where it stood.

/** One tool call, with its result once one arrived. */
export interface ToolCall {
  kind: "tool";
  id: string;
  name: string;
  input: unknown;
  /** The result's text; null when no result came back. */
  output: string | null;
  failed: boolean;
  /** Milliseconds from the trace's first time. */
  at: number | null;
  /** A subagent's messages, when this call started one. */
  children: Message[];
}

export type Block =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | ToolCall;

/** One message: every block one assistant message ID, or one user turn, holds. */
export interface Message {
  kind: "message";
  role: "assistant" | "user";
  at: number | null;
  blocks: Block[];
}

/** A line that is not a JSON object, shown as it is. */
export interface BadLine {
  kind: "bad";
  line: number;
  text: string;
}

interface Totals {
  seconds: number | null;
  costUsd: number | null;
  turns: number | null;
  model: string | null;
}

/**
 * The session's last call, its last assistant event at the top level that
 * a fallback did not retract: its message ID, its input tokens, cached ones
 * counted, and the output tokens the event gives, which is how far its
 * output had got when the event was written, not its final count; and its
 * final output tokens when the trace gives them.
 */
interface LastCall {
  id: string | null;
  input: number | null;
  output: number | null;
  final: number | null;
}

export interface Session {
  totals: Totals;
  /** The session's ID, from its init event. */
  sessionId: string | null;
  last: LastCall | null;
  /**
   * The lines before the first JSON object that are not one, such as a
   * notice the command printed before the session began.
   */
  preamble: BadLine[];
  entries: (Message | BadLine)[];
  tools: number;
  failed: number;
  thinking: number;
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown): string | null =>
  typeof value === "string" ? value : null;
const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const list = (value: unknown): unknown[] =>
  Array.isArray(value) ? (value as unknown[]) : [];

/** A tool result's content: a string, or blocks of which text is kept. */
function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  return list(content)
    .map((block) => {
      if (!isObject(block)) return "";
      return block["type"] === "text"
        ? (str(block["text"]) ?? "")
        : `[${String(block["type"])}]`;
    })
    .join("\n");
}

function blockOf(raw: unknown, at: number | null): Block | null {
  if (!isObject(raw)) return null;
  switch (raw["type"]) {
    case "text":
      return { kind: "text", text: str(raw["text"]) ?? "" };
    case "thinking":
      return { kind: "thinking", text: str(raw["thinking"]) ?? "" };
    case "tool_use":
      return {
        kind: "tool",
        id: str(raw["id"]) ?? "",
        name: str(raw["name"]) ?? "tool",
        input: raw["input"],
        output: null,
        failed: false,
        at,
        children: [],
      };
    default:
      return null;
  }
}

/** Parse a stream-json trace's text. */
export function parseTrace(text: string): Session {
  const preamble: BadLine[] = [];
  const entries: (Message | BadLine)[] = [];
  let begun = false;
  const calls = new Map<string, ToolCall>();
  // The open message of each thread: the top level ("") or a subagent's call.
  const open = new Map<string, { id: string; message: Message }>();
  let first: number | null = null;
  let init: Json | null = null;
  let result: Json | null = null;
  // Each top-level assistant event's uuid and usage, in order, and the
  // uuids a model fallback retracted.
  const assistants: { uuid: string | null; id: string | null; usage: Json }[] =
    [];
  const retracted = new Set<string>();

  for (const [index, line] of text.split("\n").entries()) {
    if (line.trim() === "") continue;
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      event = undefined;
    }
    if (!isObject(event)) {
      (begun ? entries : preamble).push({
        kind: "bad",
        line: index + 1,
        text: line,
      });
      continue;
    }
    begun = true;
    const time = Date.parse(str(event["timestamp"]) ?? "");
    if (!Number.isNaN(time) && first === null) first = time;
    const at = Number.isNaN(time) || first === null ? null : time - first;
    if (event["type"] === "system" && event["subtype"] === "init") init = event;
    if (event["type"] === "result") result = event;
    if (
      event["type"] === "system" &&
      event["subtype"] === "model_refusal_fallback"
    )
      for (const uuid of list(event["retracted_message_uuids"]))
        if (typeof uuid === "string") retracted.add(uuid);
    if (event["type"] !== "assistant" && event["type"] !== "user") continue;

    const message = isObject(event["message"]) ? event["message"] : {};
    const parentId = str(event["parent_tool_use_id"]) ?? "";
    if (event["type"] === "assistant" && parentId === "")
      assistants.push({
        uuid: str(event["uuid"]),
        id: str(message["id"]),
        usage: isObject(message["usage"]) ? message["usage"] : {},
      });
    const parent = parentId === "" ? null : (calls.get(parentId) ?? null);
    const thread = parent === null ? "" : parentId;
    const into = parent === null ? entries : parent.children;
    const role = event["type"];
    const content =
      typeof message["content"] === "string"
        ? [{ type: "text", text: message["content"] }]
        : list(message["content"]);

    // A user turn's tool results go to their calls; only its text is shown.
    const raws =
      role === "user"
        ? content.filter((raw) => {
            if (!isObject(raw) || raw["type"] !== "tool_result") return true;
            const call = calls.get(str(raw["tool_use_id"]) ?? "");
            if (call !== undefined) {
              call.output = resultText(raw["content"]);
              call.failed = raw["is_error"] === true;
            }
            return false;
          })
        : content;
    const blocks = raws
      .map((raw) => blockOf(raw, at))
      .filter((block): block is Block => block !== null);
    if (blocks.length === 0) continue;

    // stream-json writes each block of an assistant message as its own event,
    // all with the message's ID.
    const id = role === "assistant" ? (str(message["id"]) ?? "") : "";
    const last = open.get(thread);
    let target: Message;
    if (id !== "" && last?.id === id && into.at(-1) === last.message) {
      target = last.message;
    } else {
      target = { kind: "message", role, at, blocks: [] };
      into.push(target);
      open.set(thread, { id, message: target });
    }
    for (const block of blocks) {
      target.blocks.push(block);
      if (block.kind === "tool") calls.set(block.id, block);
    }
  }

  const all = [...calls.values()];
  const ms = num(result?.["duration_ms"]);
  const kept = assistants.filter(
    (c) => c.uuid === null || !retracted.has(c.uuid),
  );
  const last = kept.at(-1);
  const input = last === undefined ? null : inputOf(last.usage);
  return {
    totals: {
      seconds: ms === null ? null : ms / 1000,
      costUsd: num(result?.["total_cost_usd"]),
      turns: num(result?.["num_turns"]),
      model: str(init?.["model"]),
    },
    sessionId: str(init?.["session_id"]),
    last:
      last === undefined
        ? null
        : {
            id: last.id,
            input,
            output: num(last.usage["output_tokens"]),
            final: resultOutput(assistants, input, result),
          },
    preamble: begun ? preamble : [],
    entries: begun ? entries : preamble,
    tools: all.length,
    failed: all.filter((call) => call.failed).length,
    thinking: countThinking(entries),
  };
}

/**
 * The final output tokens of a session's only call, from its result line,
 * whose usage sums all its calls: null when the session made more than one
 * call at the top level, or when the result's input tokens differ from the
 * call's, as they do when a subagent's calls are counted in them.
 */
function resultOutput(
  assistants: { id: string | null }[],
  input: number | null,
  result: Json | null,
): number | null {
  if (input === null || result === null) return null;
  if (new Set(assistants.map((a) => a.id)).size !== 1) return null;
  const usage = isObject(result["usage"]) ? result["usage"] : {};
  return inputOf(usage) === input ? num(usage["output_tokens"]) : null;
}

/** A call's input tokens, cached ones counted; null when it gives none. */
function inputOf(usage: Json): number | null {
  const parts = [
    num(usage["input_tokens"]),
    num(usage["cache_read_input_tokens"]),
    num(usage["cache_creation_input_tokens"]),
  ];
  return parts.every((n) => n === null)
    ? null
    : parts.reduce<number>((sum, n) => sum + (n ?? 0), 0);
}

/**
 * A call's final output tokens, from the session's transcript, the record
 * Claude Code writes of a session once each call has ended: those of its
 * assistant events with the call's message ID; null when it has none.
 */
export function transcriptOutput(text: string, id: string): number | null {
  for (const line of text.split("\n")) {
    if (!line.includes(id)) continue;
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (!isObject(event) || event["type"] !== "assistant") continue;
    const message = isObject(event["message"]) ? event["message"] : {};
    if (message["id"] !== id) continue;
    const usage = isObject(message["usage"]) ? message["usage"] : {};
    const output = num(usage["output_tokens"]);
    if (output !== null) return output;
  }
  return null;
}

function countThinking(entries: (Message | BadLine)[]): number {
  let count = 0;
  for (const entry of entries) {
    if (entry.kind !== "message") continue;
    for (const block of entry.blocks) {
      if (block.kind === "thinking" && block.text.trim() !== "") count++;
      if (block.kind === "tool") count += countThinking(block.children);
    }
  }
  return count;
}
