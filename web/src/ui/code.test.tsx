import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeView, languageOf } from "./code";

describe("CodeView", () => {
  it("highlights by the file's extension, with line numbers", () => {
    const { container } = render(
      <CodeView name="a.py" text={"import os\nx = 1\n"} />,
    );

    expect(container.querySelector(".hljs-keyword")).toHaveTextContent(
      "import",
    );
    expect(container.querySelector("pre")).toHaveTextContent("1 2");
  });

  it("shows an unknown kind of file as plain text", () => {
    const { container } = render(
      <CodeView name="notes.xyz" text="<b>import</b>" />,
    );

    expect(languageOf("notes.xyz")).toBeNull();
    expect(container.querySelector("b, span")).toBeNull();
    expect(container).toHaveTextContent("<b>import</b>");
  });
});
