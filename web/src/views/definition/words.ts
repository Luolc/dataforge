// The definition views' own words (L37): the mark of a snapshot taken from a
// tree with changes, an attempt's lack of one, a variable's value that is not
// one text, and the sentences for an attempt with no snapshot.
import type { Tone } from "../../api/types";

export const DIRTY: { label: string; tone: Tone } = {
  label: "Dirty",
  tone: "warn",
};

export const NONE_OF_ALL =
  "None of the project's attempts recorded its definition.";

export const noneOfNewest = (n: number) =>
  `None of the project's newest ${n.toString()} attempts recorded its definition.`;

export const NO_DEFINITION = "This project names no definition.";

export const NOT_RECORDED = "This attempt recorded no definition.";

export const NOT_RECORDED_WORD = "Not recorded";

/** What a per-round variable's block shows in place of a value. */
export const PER_ROUND = "differs each round";

/** What a variable's block shows for a value that is empty. */
export const EMPTY = "empty";
