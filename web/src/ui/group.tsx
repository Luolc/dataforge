// A group of a table's rows that folds under its heading row.
import { ChevronDown, ChevronRight } from "lucide-react";
import { type ReactNode, useState } from "react";

/** A group of rows under a heading row that folds it. */
export function Group({
  id,
  title,
  summary,
  width,
  children,
}: {
  id: string;
  title: string;
  summary: string;
  width: number;
  children: ReactNode;
}) {
  const [folded, setFolded] = useState(false);
  return (
    <>
      <tbody>
        <tr>
          <th
            scope="rowgroup"
            colSpan={width}
            className="border-y border-line-subtle bg-group px-1 py-0.5 text-left"
          >
            <button
              type="button"
              aria-expanded={!folded}
              aria-controls={id}
              onClick={() => {
                setFolded((f) => !f);
              }}
              className="inline-flex target items-center gap-1.5 rounded-row px-1.5 text-xs font-medium hover:bg-hover"
            >
              {folded ? (
                <ChevronRight className="size-3.5 text-icon" aria-hidden />
              ) : (
                <ChevronDown className="size-3.5 text-icon" aria-hidden />
              )}
              {title}
              <span className="font-normal text-fg-2">{summary}</span>
            </button>
          </th>
        </tr>
      </tbody>
      <tbody id={id} hidden={folded}>
        {children}
      </tbody>
    </>
  );
}
