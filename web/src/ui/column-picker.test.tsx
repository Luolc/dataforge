import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { ColumnPicker, type PickedColumn } from "./column-picker";

const COLUMNS: PickedColumn[] = [
  { key: "id", title: "Task", locked: true },
  { key: "status", title: "Status" },
  { key: "rounds", title: "Rounds" },
  { key: "col.Cost", title: "Cost" },
];

const NONE: string[] = [];

function Picker({ hidden: first = NONE }: { hidden?: string[] }) {
  const [hidden, setHidden] = useState(first);
  return (
    <>
      <ColumnPicker columns={COLUMNS} hidden={hidden} onChange={setHidden} />
      <output aria-label="hidden">{hidden.join(",")}</output>
    </>
  );
}

const button = () => screen.getByRole("button", { name: /^Columns/ });

function open() {
  fireEvent.click(button());
  return screen.getByRole("dialog", { name: "Columns" });
}

/** The titles of a group's columns, in order. */
function group(name: string): string[] {
  const g = screen.queryByRole("group", { name });
  return g === null
    ? []
    : within(g)
        .getAllByRole("switch")
        .map((s) => s.getAttribute("aria-label") ?? "");
}

const toggle = (title: string) => {
  fireEvent.click(screen.getByRole("switch", { name: title }));
};

describe("the column picker", () => {
  it("opens a panel that names the columns shown and hidden", () => {
    render(<Picker hidden={["rounds"]} />);
    expect(button()).toHaveTextContent(/^Columns · 1 hidden$/);
    expect(button()).toHaveAttribute("aria-expanded", "false");

    const panel = open();

    expect(button()).toHaveAttribute("aria-expanded", "true");
    expect(button()).toHaveAttribute("aria-controls", panel.id);
    expect(within(panel).getByRole("searchbox")).toHaveAccessibleName(
      "Find a column",
    );
    expect(group("Shown in the table")).toEqual(["Task", "Status", "Cost"]);
    expect(group("Hidden")).toEqual(["Rounds"]);
  });

  it("hides a column, and shows it again", () => {
    render(<Picker />);
    expect(button()).toHaveTextContent(/^Columns$/);
    open();
    expect(screen.queryByRole("group", { name: "Hidden" })).toBeNull();

    toggle("Rounds");
    toggle("Cost");

    expect(screen.getByRole("switch", { name: "Rounds" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(group("Shown in the table")).toEqual(["Task", "Status"]);
    expect(group("Hidden")).toEqual(["Rounds", "Cost"]);
    expect(button()).toHaveTextContent(/^Columns · 2 hidden$/);

    toggle("Rounds");

    expect(group("Hidden")).toEqual(["Cost"]);
    expect(screen.getByLabelText("hidden")).toHaveTextContent(/^col\.Cost$/);
  });

  it("keeps a locked column shown: Always shown, and Hide all leaves it", () => {
    render(<Picker />);
    open();
    const locked = screen.getByRole("switch", { name: "Task" });
    expect(locked).toBeDisabled();
    expect(locked).toHaveTextContent("Always shown");

    fireEvent.click(screen.getByRole("button", { name: "Hide all" }));

    expect(group("Shown in the table")).toEqual(["Task"]);
    expect(group("Hidden")).toEqual(["Status", "Rounds", "Cost"]);
    expect(button()).toHaveTextContent("Columns · 3 hidden");
    expect(screen.queryByRole("button", { name: "Hide all" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));

    expect(group("Shown in the table")).toEqual([
      "Task",
      "Status",
      "Rounds",
      "Cost",
    ]);
    expect(button()).toHaveTextContent(/^Columns$/);
  });

  it("finds a column by its title, and moves only those it finds", () => {
    render(<Picker />);
    open();
    const find = screen.getByRole("searchbox", { name: "Find a column" });

    fireEvent.change(find, { target: { value: "st" } });

    expect(group("Shown in the table")).toEqual(["Status", "Cost"]);
    fireEvent.click(screen.getByRole("button", { name: "Hide all" }));
    expect(screen.getByLabelText("hidden")).toHaveTextContent(
      /^status,col\.Cost$/,
    );

    fireEvent.change(find, { target: { value: "none of them" } });

    expect(screen.getByText("No column matches.")).toBeVisible();
  });

  it("resets to the default, every column shown", () => {
    render(<Picker hidden={["status", "rounds"]} />);
    open();
    const reset = screen.getByRole("button", { name: "Reset to default" });

    fireEvent.click(reset);

    expect(screen.getByLabelText("hidden")).toBeEmptyDOMElement();
    expect(group("Hidden")).toEqual([]);
    expect(button()).toHaveTextContent(/^Columns$/);
    expect(reset).toBeDisabled();
  });

  it("opens on its search, and closes on Escape back to its button", async () => {
    render(<Picker />);
    button().focus();

    // A button's Enter and Space are its click.
    fireEvent.click(button());

    await waitFor(() => {
      expect(
        screen.getByRole("searchbox", { name: "Find a column" }),
      ).toHaveFocus();
    });
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: "Escape",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(button()).toHaveFocus();
    });
  });
});
