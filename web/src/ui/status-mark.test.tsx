import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { project } from "../views/home/rows.test-data";
import { StatusIcon, StatusLabel } from "./status-mark";

describe("a status's icon", () => {
  it.each([
    ["passed", "Passed", "lucide-circle-check", "text-good"],
    ["exhausted", "Exhausted", "lucide-circle-x", "text-bad"],
    ["too_easy", "Too easy", "lucide-circle-alert", "text-warn"],
    [null, "Not ended", "lucide-circle-minus", "text-neutral"],
  ])(
    "draws %s as its tone's circle, in outline, in the tone's colour",
    (word, label, shape, colour) => {
      render(<StatusIcon project={project} word={word} />);
      const icon = screen.getByRole("img", { name: label });
      expect(icon).toHaveClass(colour);
      // Outline only: no fill behind the glyph.
      expect(icon.className).not.toMatch(/\bbg-|rounded-full/);
      expect(icon.querySelector("svg")).toHaveClass(shape);
    },
  );

  it("puts the same shape before a label's word", () => {
    render(<StatusLabel project={project} word="too_easy" />);
    const label = screen.getByText("Too easy");
    expect(label).toHaveClass("bg-warn-label", "text-warn-label-fg");
    expect(label.querySelector("svg")).toHaveClass("lucide-circle-alert");
  });

  it("keeps a label of two words on one line, as its height is one line", () => {
    render(<StatusLabel project={project} word="too_easy" />);
    expect(screen.getByText("Too easy")).toHaveClass(
      "h-5",
      "whitespace-nowrap",
    );
  });
});
