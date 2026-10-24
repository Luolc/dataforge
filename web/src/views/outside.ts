// Whether the page is for people outside the machine, as the share site's
// are: then a trial gives no time, cost or log, and its trace no cost
// (design-share.md, invariant 18).
import { createContext } from "react";

export const OutsideContext = createContext(false);
