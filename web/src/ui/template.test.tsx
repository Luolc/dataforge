/// <reference types="node" />
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { applyTemplate, KEY, savedTemplate, TEMPLATES } from "./template";
import { TemplateSwitch } from "./template-switch";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset["template"];
});

const open = (el: Element) => {
  fireEvent.pointerDown(el, { button: 0, ctrlKey: false });
};
const press = (el: Element | null, key: string) => {
  if (el) fireEvent.keyDown(el, { key });
};

describe("the colour templates", () => {
  it("default to Light, in the order Light, Dark, High contrast, Neutral light", () => {
    expect(TEMPLATES.map((t) => [t.id, t.label])).toEqual([
      ["ac", "Light"],
      ["dark", "Dark"],
      ["contrast", "High contrast"],
      ["neutral", "Neutral light"],
    ]);
    expect(savedTemplate()).toBe("ac");
  });

  it("read the choice kept in localStorage, and ignore one they do not know", () => {
    localStorage.setItem(KEY, "dark");
    expect(savedTemplate()).toBe("dark");
    localStorage.setItem(KEY, "sepia");
    expect(savedTemplate()).toBe("ac");
  });

  it("draw the page in a template by the root's data-template", () => {
    applyTemplate("neutral");
    expect(document.documentElement.dataset["template"]).toBe("neutral");
  });

  it("open a menu of the templates, and keep the choice for the next visit", () => {
    render(<TemplateSwitch />);
    const button = screen.getByRole("button", { name: /Colour template/ });
    expect(button).toHaveTextContent("Light");
    expect(button).toHaveAttribute("aria-expanded", "false");

    open(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAttribute("aria-controls");
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => i.textContent)).toEqual([
      "Light",
      "Dark",
      "High contrast",
      "Neutral light",
    ]);
    expect(items[0]).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("menuitemradio", { name: "Dark" }));

    expect(button).toHaveTextContent("Dark");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.documentElement.dataset["template"]).toBe("dark");
    expect(localStorage.getItem(KEY)).toBe("dark");
  });

  it("work from the keyboard: arrows move, Enter chooses, Escape closes", async () => {
    render(<TemplateSwitch />);
    const button = screen.getByRole("button", { name: /Colour template/ });
    button.focus();

    press(button, "Enter");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    press(document.activeElement, "ArrowDown");
    // The menu moves focus on the next tick.
    await waitFor(() => {
      expect(document.activeElement).toHaveTextContent("Dark");
    });
    press(document.activeElement, "ArrowDown");
    await waitFor(() => {
      expect(document.activeElement).toHaveTextContent("High contrast");
    });
    press(document.activeElement, "Enter");
    expect(localStorage.getItem(KEY)).toBe("contrast");
    expect(button).toHaveTextContent("High contrast");

    press(button, "Enter");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    press(document.activeElement, "Escape");
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => {
      expect(button).toHaveFocus();
    });
  });

  // Tests run from web/.
  it("are applied by a script in the head before the bundle paints", () => {
    const html = readFileSync("index.html", "utf8");
    const early = html.indexOf('<script src="/template.js"></script>');
    expect(early).toBeGreaterThan(-1);
    expect(early).toBeLessThan(html.indexOf("</head>"));
    expect(early).toBeLessThan(html.indexOf('type="module"'));

    const script = readFileSync("public/template.js", "utf8");
    expect(script).toContain(`"${KEY}"`);
    // The script and template.ts agree on every value: none, one unknown, each id.
    for (const stored of [null, "sepia", ...TEMPLATES.map((t) => t.id)]) {
      localStorage.clear();
      if (stored !== null) localStorage.setItem(KEY, stored);
      delete document.documentElement.dataset["template"];
      runInNewContext(script, { localStorage, document });
      expect(document.documentElement.dataset["template"]).toBe(
        savedTemplate(),
      );
    }
    expect(document.documentElement.dataset["template"]).toBe("neutral");
  });
});
