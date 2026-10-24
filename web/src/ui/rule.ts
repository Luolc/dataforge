// A filter's rule, a field's values: one rule per field, the values of one
// rule alternatives, and the rules of different fields all holding.

/** What a rule asks of a row's value: to be one of its values, or none. */
export type Op = "any" | "none";

export interface Rule {
  field: string;
  op: Op;
  values: string[];
}

/** The rules with the field's rule set to rule, or taken out for null. */
export function withRule(
  rules: readonly Rule[],
  field: string,
  rule: Rule | null,
): Rule[] {
  if (rule === null) return rules.filter((r) => r.field !== field);
  // A rule keeps its place among the others; a new one comes last.
  return rules.some((r) => r.field === field)
    ? rules.map((r) => (r.field === field ? rule : r))
    : [...rules, rule];
}
