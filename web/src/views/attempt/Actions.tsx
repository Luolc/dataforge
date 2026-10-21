// A data item's actions, under the heading of one of its attempts: a button
// each, greyed with the reason when the backend says it may not be submitted
// now, whose question holds an input for each number the action takes
// (design-viz.md section 5).
import { ListPlus } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import {
  getJson,
  nextGeneration,
  postJson,
  urls,
  useLoaded,
  useRefresh,
} from "../../api/client";
import {
  type ActionParam,
  type ActionState,
  actionStates,
  type Project,
  written,
} from "../../api/types";
import { Confirm } from "../../ui/confirm";
import { usd } from "../../ui/format";
import { type Said, SaidLine } from "../../ui/said";
import { toneGlyph, toneText } from "../../ui/status";

// A parameter's value as the backend takes it: a plain decimal number of at
// most 6 decimal places and NUMBER_LIMIT characters.
const DECIMAL = /^-?[0-9]+(\.[0-9]{1,6})?$/;
const NUMBER_LIMIT = 15;

/** The number text gives a parameter; null when the backend would refuse it. */
function paramValue(param: ActionParam, text: string): number | null {
  if (text.length > NUMBER_LIMIT || !DECIMAL.test(text)) return null;
  const value = Number(text);
  return value >= param.min && value <= param.max ? value : null;
}

/** A parameter's value as a page shows it, by its format. */
const shown = (param: ActionParam, value: number) =>
  param.format === "usd" ? usd(value) : value.toString();

// The item an attempt ID names: its first item.depth components.
const itemOf = (project: Project, attempt: string) =>
  attempt.split("/").slice(0, project.item.depth).join("/");

export function ItemActions({
  project,
  attempt,
}: {
  project: Project;
  attempt: string;
}) {
  const item = itemOf(project, attempt);
  const url = urls.actions(project.id, item);
  // Read with the page, and again after each write: generations only grow, so
  // the later of the two changes whenever either does.
  const [wrote, setWrote] = useState(0);
  const refresh = Math.max(useRefresh(), wrote);
  const states = useLoaded(
    url,
    (signal) => getJson(url, actionStates, signal),
    refresh,
  );
  const [said, setSaid] = useState<Said | null>(null);
  const submit = async (
    action: ActionState,
    values: Record<string, string>,
  ) => {
    const set = action.params
      .map((p) => `${p.label} ${shown(p, Number(values[p.name]))}`)
      .join(", ");
    const what = set === "" ? action.label : `${action.label} (${set})`;
    try {
      const { job } = await postJson(
        urls.submit(project.id, action.name, item, values),
        written,
      );
      setSaid({
        ok: true,
        text: `${what}: queued as job ${job.id.toString()} in ${job.queue}.`,
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      setSaid({ ok: false, text: `${what} was not queued (${message}).` });
    }
    setWrote(nextGeneration());
  };
  return (
    <section
      aria-label={`Actions of ${project.item.title} ${item}`}
      className="flex flex-col gap-1.5 px-7 pt-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-xs text-fg-3">
          {project.item.title} <span className="font-mono">{item}</span>
        </span>
        {states.state === "error" && (
          <span className={`text-xs ${toneText.bad}`}>
            The actions cannot be read: {states.message}
          </span>
        )}
        {states.state === "ok" &&
          states.data.map((a) => (
            <ActionButton
              key={a.name}
              action={a}
              item={item}
              onYes={(values) => submit(a, values)}
            />
          ))}
      </div>
      <SaidLine
        said={said}
        after={
          <Link to="/queue" className="link hover:underline">
            Open the queue
          </Link>
        }
      />
    </section>
  );
}

/**
 * An action's button and its reason; its question holds an input per
 * parameter, each at its default whenever the question opens, and queues only
 * when every value is one the backend takes.
 */
function ActionButton({
  action,
  item,
  onYes,
}: {
  action: ActionState;
  item: string;
  onYes: (values: Record<string, string>) => Promise<void>;
}) {
  const baseId = useId();
  const defaults = () =>
    Object.fromEntries(
      action.params.map((p) => [p.name, p.default.toString()]),
    );
  const [values, setValues] = useState<Record<string, string>>(defaults);
  const reasonId = `${baseId}-reason`;
  const ready = action.params.every(
    (p) => paramValue(p, values[p.name] ?? "") !== null,
  );
  const fields =
    action.params.length === 0
      ? undefined
      : action.params.map((p) => (
          <ParamField
            key={p.name}
            param={p}
            text={values[p.name] ?? ""}
            hintId={`${baseId}-${p.name}`}
            onChange={(text) => {
              setValues((v) => ({ ...v, [p.name]: text }));
            }}
          />
        ));
  return (
    <div className="inline-flex flex-wrap items-center gap-2">
      <Confirm
        question={`Queue ${action.label} for ${item} in ${action.queue}?`}
        yes="Queue"
        onYes={() => onYes(values)}
        onAsk={() => {
          setValues(defaults());
        }}
        fields={fields}
        ready={ready}
        disabled={action.reason !== null}
        {...(action.reason !== null ? { describedBy: reasonId } : {})}
      >
        <ListPlus className="size-3.5 text-icon" aria-hidden />
        {action.label}
      </Confirm>
      {action.reason !== null && (
        <span id={reasonId} className="text-xs text-fg-3">
          {action.reason}
        </span>
      )}
    </div>
  );
}

/** A parameter's input, labelled by it, with its bounds as its description. */
function ParamField({
  param,
  text,
  hintId,
  onChange,
}: {
  param: ActionParam;
  text: string;
  hintId: string;
  onChange: (text: string) => void;
}) {
  const ok = paramValue(param, text) !== null;
  const range = `${shown(param, param.min)} to ${shown(param, param.max)}`;
  const Bad = toneGlyph.bad;
  return (
    <span className="inline-flex items-center gap-2">
      <label className="inline-flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
        {param.label}
        <input
          type="text"
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          value={text}
          onChange={(e) => {
            onChange(e.target.value);
          }}
          aria-invalid={!ok}
          aria-describedby={hintId}
          className="h-6 w-16 bg-transparent text-right tabular-nums outline-none"
        />
      </label>
      {ok ? (
        <span id={hintId} className="text-fg-3">
          From {range}.
        </span>
      ) : (
        <span
          id={hintId}
          className={`inline-flex items-center gap-1 ${toneText.bad}`}
        >
          <Bad className="size-3.5 flex-none" aria-hidden />
          Not a number from {range}.
        </span>
      )}
    </span>
  );
}
