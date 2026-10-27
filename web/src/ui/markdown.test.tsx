import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Markdown } from "./markdown";

// jsdom draws nothing, so it has no scrollIntoView: a test puts a spy there.
function spyOnScroll() {
  const moved = vi.fn();
  Element.prototype.scrollIntoView = moved;
  return moved;
}

afterEach(() => {
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
});

describe("Markdown", () => {
  it("raw HTML shows as text", () => {
    const { container } = render(
      <Markdown
        text={
          '<img src=x onerror="alert(1)"> and <b>bold</b>\n\n<script>alert(2)</script>'
        }
      />,
    );

    expect(container.querySelector("img, b, script")).toBeNull();
    expect(container).toHaveTextContent(
      '<img src=x onerror="alert(1)"> and <b>bold</b>',
    );
    expect(container).toHaveTextContent("<script>alert(2)</script>");
  });

  it("an image is a link, not loaded", () => {
    const { container } = render(
      <Markdown text="![a chart](https://example.com/chart.png)" />,
    );

    const link = screen.getByRole("link", { name: "image: a chart" });
    expect(container.querySelector("img")).toBeNull();
    expect(link).toHaveAttribute("href", "https://example.com/chart.png");
  });

  it("a link opens in a new tab with no opener or referrer", () => {
    render(<Markdown text="[notes](https://example.com/notes)" />);

    const link = screen.getByRole("link", { name: "notes" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("a link to a heading of the text moves there and focuses it", () => {
    const moved = spyOnScroll();
    render(
      <Markdown
        text={
          "- [Scope](#scope)\n- [Scope, again](#scope-1)\n- [Why `x` fails?](#why-x-fails)\n\n## Scope\n\n## Scope\n\n## Why `x` fails?\n"
        }
      />,
    );

    const link = screen.getByRole("link", { name: "Scope, again" });
    expect(link).not.toHaveAttribute("target");
    fireEvent.click(link);
    const [, second] = screen.getAllByRole("heading", { name: "Scope" });
    expect(moved.mock.contexts).toEqual([second]);
    expect(second).toHaveFocus();
    fireEvent.click(screen.getByRole("link", { name: "Why x fails?" }));
    expect(moved.mock.contexts.at(-1)).toBe(
      screen.getByRole("heading", { name: "Why x fails?" }),
    );
  });

  it("a link to a name the text does not hold moves nothing", () => {
    const moved = spyOnScroll();
    render(<Markdown text={"[gone](#gone)\n\n## Here\n"} />);

    fireEvent.click(screen.getByRole("link", { name: "gone" }));
    expect(moved).not.toHaveBeenCalled();
  });

  it("a javascript: link loses its URL", () => {
    render(<Markdown text="[run](javascript:alert(1))" />);

    expect(screen.getByText("run")).not.toHaveAttribute(
      "href",
      expect.stringContaining("javascript"),
    );
  });

  it("renders tables and highlights a fenced block", () => {
    const { container } = render(
      <Markdown
        text={
          "| a | b |\n| - | - |\n| 1 | 2 |\n\n```python\ndef f(): pass\n```"
        }
      />,
    );

    expect(screen.getByRole("table")).toHaveTextContent("12");
    expect(container.querySelector(".hljs-keyword")).toHaveTextContent("def");
  });

  it("draws a cell whose whole text is plain, and raw HTML stays text", () => {
    const { container } = render(
      <Markdown
        text={
          "| a | b |\n| - | - |\n| pass | the pass |\n| <b>pass</b> | `pass` |"
        }
        cell={(text) => (text === "pass" ? <mark>{text}</mark> : null)}
      />,
    );

    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]?.closest("td")).toHaveTextContent(/^pass$/);
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByRole("table")).toHaveTextContent("<b>pass</b>");
    expect(screen.getByText("the pass").tagName).toBe("TD");
  });

  it("keeps a code span with a variable whole, so a line breaks around it", () => {
    render(
      <Markdown
        text={"Check out `${commit}` and `plain` code."}
        variables={{
          pattern: /\$\{\s*\w+\s*\}/g,
          draw: (name) => <span>{`[${name}]`}</span>,
        }}
      />,
    );

    expect(screen.getByText("[commit]").closest("code")).toHaveClass(
      "inline-block",
      "max-w-full",
    );
    expect(screen.getByText("plain")).not.toHaveClass("inline-block");
  });
});
