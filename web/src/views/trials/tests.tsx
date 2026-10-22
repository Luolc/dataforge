// A trial's Tests tab: the tests of the CTRF report at the project's
// trials.tests, grouped by suite, with a filter by status and by kind.
import { useId, useState } from "react";

import type { Project, Trial } from "../../api/types";
import { Note } from "../../ui/card";
import { Chip, control } from "../../ui/chip";
import { CodeView } from "../../ui/code";
import { duration } from "../../ui/format";
import { Group } from "../../ui/group";
import { Sideways } from "../../ui/sideways";
import { toneGlyph, toneText } from "../../ui/status";
import { size, useFile } from "../files/file";
import { type CtrfTest, labelOf, readCtrf, suiteOf } from "./ctrf";
import { inTrial } from "./path";
import { TEST_WORDS, type TestWord, testStatus, testTone } from "./status";

/** The heading of the group of tests that name no suite. */
const NO_SUITE = "No suite";

/** The trial's tests, at the project's trials.tests under the trial's path. */
export function TrialTests({
  project,
  attempt,
  trial,
  relative,
}: {
  project: Project;
  attempt: string;
  trial: Trial;
  relative: string;
}) {
  const path = inTrial(trial, relative);
  const loaded = useFile(project.id, attempt, path);
  if (loaded.state === "loading") return <Note>Loading…</Note>;
  if (loaded.state === "error") return <Note>{loaded.message}</Note>;
  const content = loaded.data;
  // A trial whose verifier never wrote the file ran no tests (L40).
  if (content.kind === "missing") {
    return (
      <Note>
        This trial recorded no test results.
        <span className="mt-1 block font-mono text-xs break-all">
          {content.reason}
        </span>
      </Note>
    );
  }
  if (content.kind === "too-large")
    return <Note>{`Not shown: ${content.reason}`}</Note>;
  if (content.kind === "binary")
    return <Note>{`Not shown: not UTF-8 text, ${size(content.size)}.`}</Note>;
  const read = readCtrf(content.text);
  return (
    <div className="min-w-0 rounded-panel border border-line bg-panel">
      <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h3 className="font-mono text-xs break-all">{path}</h3>
        <span className="flex-none text-xs text-fg-3 tabular-nums">
          {size(content.size)}
        </span>
      </header>
      {"why" in read ? (
        <>
          <Note>
            {`This file is not a CTRF report (${read.why}). Below is the file as written.`}
          </Note>
          <CodeView name={path} text={content.text} />
        </>
      ) : read.tests.length === 0 ? (
        <Note>The report lists no tests.</Note>
      ) : (
        <TestTable project={project} tests={read.tests} />
      )}
    </div>
  );
}

const cell = "px-2 py-1.5 align-top";
const numeric = `${cell} text-right whitespace-nowrap tabular-nums`;

interface Shown {
  /** Its place in the report. */
  at: number;
  test: CtrfTest;
  word: TestWord;
  kind: string | null;
}

function TestTable({
  project,
  tests,
}: {
  project: Project;
  tests: CtrfTest[];
}) {
  const baseId = useId();
  const [status, setStatus] = useState<TestWord | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const all: Shown[] = tests.map((test, at) => ({
    at,
    test,
    word: testStatus(test).word,
    kind: labelOf(test, "kind"),
  }));
  const ofKind = all.filter((t) => kind === null || t.kind === kind);
  const ofStatus = all.filter((t) => status === null || t.word === status);
  const shown = ofKind.filter((t) => status === null || t.word === status);
  const hidden = all.length - shown.length;
  const kinds = [
    ...new Set(all.flatMap((t) => (t.kind === null ? [] : [t.kind]))),
  ];
  const kindLabel = (k: string) => project.trials?.kinds?.[k]?.label ?? k;
  const has = {
    kind: kinds.length > 0,
    principle: tests.some((t) => labelOf(t, "principle") !== null),
    runner: tests.some((t) => labelOf(t, "runner") !== null),
  };
  const heads = [
    { title: "Status" },
    { title: "Name" },
    ...(has.kind ? [{ title: "Kind" }] : []),
    ...(has.principle ? [{ title: "Principle" }] : []),
    ...(has.runner ? [{ title: "Runner" }] : []),
    { title: "Duration", numeric: true },
  ];
  const suites = [...new Set(shown.map((t) => suiteOf(t.test)))];
  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div
          role="group"
          aria-label="Filter by status"
          className="flex flex-wrap gap-1.5"
        >
          <Chip
            pressed={status === null}
            onClick={() => {
              setStatus(null);
            }}
            label="All"
            count={ofKind.length}
          />
          {TEST_WORDS.map((word) => {
            const count = ofKind.filter((t) => t.word === word).length;
            if (count === 0 && status !== word) return null;
            const tone = testTone(word);
            const Glyph = toneGlyph[tone];
            return (
              <Chip
                key={word}
                pressed={status === word}
                onClick={() => {
                  setStatus(status === word ? null : word);
                }}
                icon={
                  <Glyph
                    className={`size-icon-sm ${toneText[tone]}`}
                    aria-hidden
                  />
                }
                label={word}
                count={count}
              />
            );
          })}
        </div>
        {has.kind && (
          <div
            role="group"
            aria-label="Filter by kind"
            className="flex flex-wrap gap-1.5"
          >
            <Chip
              pressed={kind === null}
              onClick={() => {
                setKind(null);
              }}
              label="All kinds"
              count={ofStatus.length}
            />
            {kinds.map((k) => (
              <Chip
                key={k}
                pressed={kind === k}
                onClick={() => {
                  setKind(kind === k ? null : k);
                }}
                label={kindLabel(k)}
                count={ofStatus.filter((t) => t.kind === k).length}
              />
            ))}
          </div>
        )}
      </div>
      {hidden > 0 && (
        <p className="flex items-center gap-2 text-xs text-fg-3">
          {`${hidden.toString()} of ${all.length.toString()} hidden by ${
            status !== null && kind !== null
              ? "the status and the kind filters"
              : status !== null
                ? "the status filter"
                : "the kind filter"
          }.`}
          <button
            type="button"
            className={control}
            onClick={() => {
              setStatus(null);
              setKind(null);
            }}
          >
            Show all
          </button>
        </p>
      )}
      {shown.length === 0 ? (
        <p className="py-6 text-center text-fg-3">
          No test matches the filters.
        </p>
      ) : (
        <Sideways label="the tests">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line bg-head text-xs text-fg-2">
                {heads.map((h) => (
                  <th
                    key={h.title}
                    scope="col"
                    className={`px-2 py-1 font-medium whitespace-nowrap ${h.numeric === true ? "text-right" : "text-left"}`}
                  >
                    {h.title}
                  </th>
                ))}
              </tr>
            </thead>
            {suites.map((suite, s) => {
              const inSuite = shown.filter((t) => suiteOf(t.test) === suite);
              const n = inSuite.length;
              const k = inSuite.filter((t) => {
                const tone = testTone(t.word);
                return tone === "bad" || tone === "warn";
              }).length;
              const id = `${baseId}-${s.toString()}`;
              return (
                <Group
                  key={suite ?? ""}
                  id={id}
                  title={suite ?? NO_SUITE}
                  summary={
                    k > 0
                      ? `${k.toString()} of ${n.toString()} failed`
                      : n.toString()
                  }
                  width={heads.length}
                >
                  {inSuite.map((t) => (
                    <TestRow
                      key={t.at}
                      id={`${id}-${t.at.toString()}`}
                      test={t.test}
                      kind={t.kind === null ? null : kindLabel(t.kind)}
                      has={has}
                      width={heads.length}
                    />
                  ))}
                </Group>
              );
            })}
          </table>
        </Sideways>
      )}
    </div>
  );
}

/** A test's row, and below it, opened by its button, its message and trace. */
function TestRow({
  id,
  test,
  kind,
  has,
  width,
}: {
  id: string;
  test: CtrfTest;
  kind: string | null;
  has: { kind: boolean; principle: boolean; runner: boolean };
  width: number;
}) {
  const [open, setOpen] = useState(false);
  const { word, tone } = testStatus(test);
  const Glyph = toneGlyph[tone];
  const detail = [
    { part: "message", text: test.message },
    { part: "trace", text: test.trace },
  ].filter((d) => d.text !== undefined && d.text !== "");
  const label = (key: string) => labelOf(test, key) ?? "—";
  return (
    <>
      <tr className="border-b border-line-subtle">
        <td className={`${cell} whitespace-nowrap`}>
          <span
            className={`inline-flex items-center gap-1 text-xs ${toneText[tone]}`}
          >
            <Glyph className="size-3" aria-hidden />
            {word}
          </span>
        </td>
        <td className={cell}>
          <span className="font-mono text-xs break-all">{test.name}</span>
          {detail.length > 0 && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={id}
              onClick={() => {
                setOpen((o) => !o);
              }}
              className={`${control} ml-2`}
            >
              {open ? "Hide message" : "Message"}
            </button>
          )}
        </td>
        {has.kind && <td className={cell}>{kind ?? "—"}</td>}
        {has.principle && (
          <td className={`${cell} font-mono text-xs`}>{label("principle")}</td>
        )}
        {has.runner && (
          <td className={`${cell} font-mono text-xs`}>{label("runner")}</td>
        )}
        <td className={numeric}>{duration(test.duration / 1000, true)}</td>
      </tr>
      {detail.length > 0 && (
        <tr id={id} hidden={!open} className="border-b border-line-subtle">
          <td colSpan={width} className={cell}>
            {detail.map(({ part, text }) => (
              <pre
                key={part}
                className="overflow-x-auto bg-code-bg px-3 py-2 font-mono text-code break-words whitespace-pre-wrap"
              >
                {text}
              </pre>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
