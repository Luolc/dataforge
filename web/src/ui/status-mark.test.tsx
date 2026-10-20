import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Project } from "../api/types";
import { StatusIcon, StatusLabel } from "./status-mark";

const project = {
  statuses: {
    passed: { label: "Passed", tone: "good" },
    exhausted: { label: "Exhausted", tone: "bad" },
    too_easy: { label: "Too easy", tone: "warn" },
  },
} as unknown as Project;

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
});
