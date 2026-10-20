/** The session view of a trace; until it is built, the trace's path. */
export function SessionView({
  path,
  format,
}: {
  path: string;
  format: string | null;
}) {
  return (
    <p className="rounded-row border border-dashed border-line p-4 text-xs text-fg-3">
      The session view is not built yet: <code>{path}</code>
      {format !== null && ` (${format})`}
    </p>
  );
}
