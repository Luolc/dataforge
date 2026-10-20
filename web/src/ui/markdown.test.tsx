import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./markdown";

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
});
