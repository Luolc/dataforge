// The Filter button of a list's toolbar, the rules in force as a row of
// chips under it, and the panel that edits one rule: a field, is any of or is
// none of, and the values it names, each with how many rows choosing it
// shows. A rule per field: the values of one rule are alternatives, and the
// rules of different fields all hold.
import { Check, ChevronLeft, ListFilter, Plus, Search, X } from "lucide-react";
import { Popover } from "radix-ui";
import {
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";

import { control } from "./chip";
import { type Op, type Rule, withRule } from "./rule";

/** A value of a field, as the panel lists it. */
interface FilterValue {
  key: string;
  label: string;
  /** How many rows choosing it would show. */
  count: number;
  icon?: ReactNode;
}

/** A field a list filters by, with its values in their order. */
export interface FilterField {
  key: string;
  title: string;
  values: FilterValue[];
}

const OPS: { op: Op; title: string }[] = [
  { op: "any", title: "is any of" },
  { op: "none", title: "is none of" },
];

const opTitle = (op: Op) => OPS.find((o) => o.op === op)?.title ?? op;

const PANEL =
  "z-10 flex max-h-(--radix-popover-content-available-height) w-72 flex-col gap-1 overflow-y-auto rounded-panel border border-line bg-panel p-1.5 text-xs text-fg";

const ROW =
  "flex target w-full items-center gap-2 rounded-row px-2 text-left text-xs hover:bg-hover";

/**
 * One field's rule: its op and its values, each a check box with its count.
 * The op chosen before any value is kept for the values picked after it.
 */
function RulePanel({
  field,
  rule,
  onChange,
  onBack,
}: {
  field: FilterField;
  rule: Rule | undefined;
  onChange: (rule: Rule | null) => void;
  /** Goes back to the list of fields, where the panel came from there. */
  onBack: (() => void) | null;
}) {
  const [draft, setDraft] = useState<Op>(rule?.op ?? "any");
  const op = rule?.op ?? draft;
  const values = rule?.values ?? [];
  // A value no row has is listed only while a rule names it; once taken out
  // of the rule, it stays in its place, at 0, while the panel is open, so the
  // focus stays on its check box.
  const [left, setLeft] = useState<{ value: FilterValue; at: number }[]>([]);
  const listed = [...field.values];
  for (const { value, at } of left)
    if (!listed.some((v) => v.key === value.key))
      listed.splice(Math.min(at, listed.length), 0, { ...value, count: 0 });
  const firstRef = useRef<HTMLButtonElement>(null);
  // The panel takes the place of the control that opened it, so the focus
  // goes on to its first control.
  useEffect(() => {
    firstRef.current?.focus();
  }, []);
  const toggle = (value: FilterValue, at: number) => {
    const { key } = value;
    if (values.includes(key) && value.count === 0)
      setLeft((l) => [...l.filter((x) => x.value.key !== key), { value, at }]);
    const next = values.includes(key)
      ? values.filter((v) => v !== key)
      : [...values, key];
    onChange(next.length === 0 ? null : { field: field.key, op, values: next });
  };
  return (
    <>
      <div className="flex items-center gap-1.5">
        {onBack !== null && (
          <button
            type="button"
            aria-label="Back to the fields"
            onClick={onBack}
            className="inline-flex target items-center justify-center rounded-row hover:bg-hover"
          >
            <ChevronLeft className="size-3.5 text-icon" aria-hidden />
          </button>
        )}
        <span className="pl-1 font-medium">{field.title}</span>
        <div
          role="group"
          aria-label={`How ${field.title} is matched`}
          className="ml-auto flex rounded-row border border-chip-line"
        >
          {OPS.map((o, i) => (
            <button
              key={o.op}
              ref={i === 0 ? firstRef : undefined}
              type="button"
              aria-pressed={op === o.op}
              onClick={() => {
                setDraft(o.op);
                if (rule !== undefined) onChange({ ...rule, op: o.op });
              }}
              className="target px-2 text-2xs font-medium text-fg-2 hover:bg-hover aria-pressed:bg-chip-on aria-pressed:text-chip-on-fg"
            >
              {o.title}
            </button>
          ))}
        </div>
      </div>
      <ul
        role="group"
        aria-label={`Values of ${field.title}`}
        className="flex flex-col"
      >
        {listed.map((v, at) => {
          const on = values.includes(v.key);
          return (
            <li key={v.key}>
              <button
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => {
                  toggle(v, at);
                }}
                className={ROW}
              >
                <span
                  aria-hidden
                  className={`inline-flex size-3.5 flex-none items-center justify-center rounded-row border ${on ? "border-chip-on bg-chip-on text-chip-on-fg" : "border-chip-line bg-chip"}`}
                >
                  {on && <Check className="size-3" />}
                </span>
                {v.icon}
                <span className="flex-1 break-words">{v.label}</span>
                <span className="text-fg-3 tabular-nums">{v.count}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {listed.length === 0 && (
        <p className="px-2 py-1 text-fg-3">No value to filter by.</p>
      )}
    </>
  );
}

/**
 * The panel of the Filter button and of Add filter: the fields, with a search
 * box, and once one is chosen, its rule.
 */
function FieldsPanel({
  fields,
  rules,
  onChange,
}: {
  fields: readonly FilterField[];
  rules: readonly Rule[];
  onChange: (rules: Rule[]) => void;
}) {
  const [find, setFind] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // Back from a field's rule, the focus comes to the search.
  useEffect(() => {
    if (picked === null) searchRef.current?.focus();
  }, [picked]);
  const field = fields.find((f) => f.key === picked);
  if (field !== undefined)
    return (
      <RulePanel
        field={field}
        rule={rules.find((r) => r.field === field.key)}
        onChange={(rule) => {
          onChange(withRule(rules, field.key, rule));
        }}
        onBack={() => {
          setPicked(null);
        }}
      />
    );
  const q = find.trim().toLowerCase();
  const matching = fields.filter((f) => f.title.toLowerCase().includes(q));
  return (
    <>
      <label className="flex target items-center gap-1.5 rounded-row border border-chip-line px-2 focus-within:border-fg-3">
        <Search className="size-3.5 flex-none text-icon" aria-hidden />
        <input
          ref={searchRef}
          type="search"
          aria-label="Filter by"
          placeholder="Filter by…"
          value={find}
          onChange={(e) => {
            setFind(e.target.value);
          }}
          className="h-6 w-full bg-transparent text-xs outline-none placeholder:text-fg-3"
        />
      </label>
      <ul className="flex flex-col">
        {matching.map((f) => (
          <li key={f.key}>
            <button
              type="button"
              onClick={() => {
                setPicked(f.key);
              }}
              className={ROW}
            >
              <ListFilter
                className="size-3.5 flex-none text-icon"
                aria-hidden
              />
              <span className="flex-1 break-words">{f.title}</span>
              {rules.some((r) => r.field === f.key) && (
                <span className="text-2xs text-fg-3">In use</span>
              )}
            </button>
          </li>
        ))}
      </ul>
      {matching.length === 0 && (
        <p className="px-2 py-1 text-fg-3">No field matches.</p>
      )}
    </>
  );
}

/** The Filter button, with how many rules are in force, and its panel. */
export function FilterButton({
  fields,
  rules,
  onChange,
  ref,
}: {
  fields: readonly FilterField[];
  rules: readonly Rule[];
  onChange: (rules: Rule[]) => void;
  ref?: RefObject<HTMLButtonElement | null>;
}) {
  const n = rules.length;
  return (
    <Popover.Root>
      <Popover.Trigger
        ref={ref}
        className={`${control} bg-chip data-[state=open]:border-fg-3`}
      >
        <ListFilter className="size-3.5 text-icon" aria-hidden />
        Filter
        {n > 0 && (
          // The leading space keeps the button's name "Filter · N"; the
          // button's gap spaces it on the page.
          <span className="font-normal text-fg-2 tabular-nums">
            {` · ${n.toString()}`}
          </span>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={4}
          aria-label="Filter"
          className={PANEL}
        >
          <FieldsPanel fields={fields} rules={rules} onChange={onChange} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** A rule's words: "Path is any of First round, Later round". */
function RuleWords({ field, rule }: { field: FilterField; rule: Rule }) {
  const label = (key: string) =>
    field.values.find((v) => v.key === key)?.label ?? key;
  return (
    <span>
      <span className="font-medium">{field.title}</span>{" "}
      <span className="text-fg-3">{opTitle(rule.op)}</span>{" "}
      <span className="font-medium">{rule.values.map(label).join(", ")}</span>
    </span>
  );
}

/**
 * The rules in force as a row of chips, each opening its rule's panel, its ×
 * taking that rule out, then Add filter; nothing while there is no rule.
 * fallback takes the focus when the last rule goes.
 */
export function FilterRules({
  fields,
  rules,
  onChange,
  fallback,
}: {
  fields: readonly FilterField[];
  rules: readonly Rule[];
  onChange: (rules: Rule[]) => void;
  fallback: RefObject<HTMLElement | null>;
}) {
  // The chip whose panel is open, and its place, so that it stays while its
  // panel takes out its last value.
  const [open, setOpen] = useState<{ field: string; at: number } | null>(null);
  const [adding, setAdding] = useState(false);
  const addRef = useRef<HTMLButtonElement>(null);
  const shown = rules.map((r) => r.field);
  if (open !== null && !shown.includes(open.field))
    shown.splice(Math.min(open.at, shown.length), 0, open.field);
  if (shown.length === 0 && !adding) return null;
  // Where the focus goes once a chip leaves.
  const away = (field: string) => {
    if (rules.some((r) => r.field !== field)) addRef.current?.focus();
    else fallback.current?.focus();
  };
  return (
    <div role="group" aria-label="Filters" className="contents">
      {shown.map((key, at) => {
        const field = fields.find((f) => f.key === key);
        if (field === undefined) return null;
        const rule = rules.find((r) => r.field === key);
        return (
          <span
            key={key}
            className="inline-flex items-stretch rounded-row border border-chip-on bg-chip text-xs"
          >
            <Popover.Root
              open={open?.field === key}
              onOpenChange={(o) => {
                setOpen(o ? { field: key, at } : null);
              }}
            >
              <Popover.Trigger className="inline-flex target items-center gap-1 rounded-l-row px-2 text-left break-words hover:bg-hover">
                {rule === undefined ? (
                  <span className="font-medium">{field.title}</span>
                ) : (
                  <RuleWords field={field} rule={rule} />
                )}
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Content
                  align="start"
                  sideOffset={4}
                  aria-label={`Filter by ${field.title}`}
                  className={PANEL}
                  onCloseAutoFocus={(e) => {
                    // The chip of a rule that is gone leaves with its panel.
                    if (!rules.some((r) => r.field === key)) {
                      e.preventDefault();
                      away(key);
                    }
                  }}
                >
                  <RulePanel
                    field={field}
                    rule={rule}
                    onChange={(next) => {
                      onChange(withRule(rules, key, next));
                    }}
                    onBack={null}
                  />
                </Popover.Content>
              </Popover.Portal>
            </Popover.Root>
            <button
              type="button"
              aria-label={`Remove the ${field.title} filter`}
              onClick={() => {
                away(key);
                onChange(withRule(rules, key, null));
              }}
              className="inline-flex target items-center justify-center rounded-r-row border-l border-line-subtle hover:bg-hover"
            >
              <X className="size-3.5 text-icon" aria-hidden />
            </button>
          </span>
        );
      })}
      <Popover.Root open={adding} onOpenChange={setAdding}>
        <Popover.Trigger
          ref={addRef}
          className="inline-flex target items-center gap-1 rounded-row px-1.5 text-xs text-fg-2 hover:bg-hover"
        >
          <Plus className="size-3.5 text-icon" aria-hidden />
          Add filter
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            align="start"
            sideOffset={4}
            aria-label="Add filter"
            className={PANEL}
            onCloseAutoFocus={(e) => {
              if (rules.length === 0) {
                e.preventDefault();
                fallback.current?.focus();
              }
            }}
          >
            <FieldsPanel fields={fields} rules={rules} onChange={onChange} />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
