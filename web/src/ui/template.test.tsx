/// <reference types="node" />
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { applyTemplate, KEY, savedTemplate, TEMPLATES } from "./template";
import { TemplateSwitch } from "./template-switch";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset["template"];
});

describe("the colour templates", () => {
  it("default to the high contrast template", () => {
    expect(TEMPLATES[0].id).toBe("contrast");
    expect(savedTemplate()).toBe("contrast");
  });

  it("read the choice kept in localStorage, and ignore one they do not know", () => {
    localStorage.setItem(KEY, "dark");
    expect(savedTemplate()).toBe("dark");
    localStorage.setItem(KEY, "sepia");
    expect(savedTemplate()).toBe("contrast");
  });

  it("draw the page in a template by the root's data-template", () => {
    applyTemplate("neutral");
    expect(document.documentElement.dataset["template"]).toBe("neutral");
  });

  it("switch on the page, and keep the choice for the next visit", () => {
    render(<TemplateSwitch />);
    const select = screen.getByRole("combobox", { name: "Colour template" });
    expect(select).toHaveValue("contrast");
    expect(
      screen.getAllByRole("option").map((o) => o.getAttribute("value")),
    ).toEqual(["contrast", "ac", "neutral", "dark"]);

    fireEvent.change(select, { target: { value: "ac" } });

    expect(select).toHaveValue("ac");
    expect(document.documentElement.dataset["template"]).toBe("ac");
    expect(localStorage.getItem(KEY)).toBe("ac");
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
    localStorage.setItem(KEY, "dark");
    runInNewContext(script, { localStorage, document });
    expect(document.documentElement.dataset["template"]).toBe("dark");
  });
});
