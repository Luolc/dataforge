import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { must, records, sampleProject } from "../../../fixtures/sample";
import { RefreshContext } from "../../api/client";
import type { Deliverable } from "../../api/types";
import { type FakeEntry, fakeFiles } from "./fake-api";
import FilesView from "./index";

const root = "artifacts/author/1/task";

const task: Record<string, FakeEntry> = {
  [`${root}/instruction.md`]:
    '# Fix the parser\n\n<img src=x onerror="alert(1)"> stays text',
  [`${root}/src/tinyparse/header.py`]: "import os\n",
  [`${root}/solution/fix.patch`]:
    "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n",
  [`${root}/data.bin`]: new Uint8Array([0xff, 0xfe, 0x00]),
  [`${root}/huge.log`]: {
    type: "too-large",
    reason: "20971520 bytes, over 16777216",
  },
  [`${root}/latest`]: { type: "symlink" },
};

const deliverable = (path: string | null): Deliverable => ({
  ...must(records.passed.deliverables?.find((d) => d.kind === "files")),
  path,
  reason: path === null ? "author was never visited" : null,
});

function show(
  files: Record<string, FakeEntry>,
  file?: string,
  path: string | null = root,
) {
  const fetch = fakeFiles(files);
  const query = new URLSearchParams({ id: "a/b", tab: "files" });
  if (file !== undefined) query.set("file", file);
  const shown = render(
    <MemoryRouter initialEntries={[`/sample/attempt?${query.toString()}`]}>
      <FilesView
        project={sampleProject}
        attempt="a/b"
        record={records.passed}
        deliverable={deliverable(path)}
      />
    </MemoryRouter>,
  );
  return { ...shown, fetch };
}

const preview = () => screen.findByRole("heading", { level: 3 });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the files view", () => {
  it("opens instruction.md and renders it as Markdown, raw HTML as text", async () => {
    const { container } = show(task);

    expect(await preview()).toHaveTextContent("instruction.md");
    expect(
      await screen.findByRole("heading", { name: "Fix the parser" }),
    ).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/stays text/)).toHaveTextContent(
      '<img src=x onerror="alert(1)"> stays text',
    );
  });

  it("lists directories first and opens the first file in that order", async () => {
    show({
      [`${root}/z.txt`]: "z",
      [`${root}/b/inner.txt`]: "inner",
      [`${root}/a/first.txt`]: "first",
    });

    expect(await preview()).toHaveTextContent("a/first.txt");
    const tree = screen.getByRole("navigation", { name: "Files" });
    const names = (await within(tree).findAllByRole("listitem")).map(
      (li) => li.firstElementChild?.textContent,
    );
    expect(names.slice(0, 2)).toEqual(["a", "b"]);
    expect(names.at(-1)).toBe("z.txt1 B");
    expect(within(tree).getByRole("button", { name: "a" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("lists a file listed only by its name and size, and opens another", async () => {
    const { fetch } = show({
      [`${root}/a/source.tar.gz`]: { type: "listed", size: 3 * 1024 * 1024 },
      [`${root}/z.txt`]: "z",
    });

    expect(await preview()).toHaveTextContent("z.txt");
    fireEvent.click(await screen.findByRole("button", { name: "a" }));
    const row = await screen.findByText("source.tar.gz");
    expect(row.closest("a")).toBeNull();
    expect(row).toHaveTextContent("source.tar.gz3.0 MiB");
    expect(
      fetch.mock.calls.some(([url]) => url.includes("source.tar.gz")),
    ).toBe(false);
  });

  it("opens the file the page names, and its folders", async () => {
    show(task, `${root}/src/tinyparse/header.py`);

    expect(await preview()).toHaveTextContent("src/tinyparse/header.py");
    const link = await screen.findByRole("link", { name: "header.py" });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "src" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("opens the first file of the folder the page names, and that folder", async () => {
    show(task, `${root}/src`);

    expect(await preview()).toHaveTextContent("src/tinyparse/header.py");
    expect(
      await screen.findByRole("link", { name: "header.py" }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "src" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.queryByText(/a directory, not a file/)).toBeNull();
  });

  it("opens a file chosen in the tree at once, not asking whether it is a folder", async () => {
    show(task);
    expect(await preview()).toHaveTextContent("instruction.md");

    fireEvent.click(await screen.findByRole("link", { name: /^data\.bin/ }));

    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
      "data.bin",
    );
  });

  it("ignores a named file outside its own path", async () => {
    show(task, "artifacts/review/1/report.md");

    expect(await preview()).toHaveTextContent("instruction.md");
  });

  it("opens and folds a folder, which names what it controls", async () => {
    show(task);

    const folder = await screen.findByRole("button", { name: "solution" });
    expect(folder).toHaveAttribute("aria-expanded", "false");
    const part = must(
      document.getElementById(must(folder.getAttribute("aria-controls"))),
    );
    expect(part).not.toBeVisible();

    fireEvent.click(folder);
    expect(folder).toHaveAttribute("aria-expanded", "true");
    expect(
      await within(part).findByRole("link", { name: "fix.patch" }),
    ).toBeVisible();

    fireEvent.click(folder);
    expect(part).not.toBeVisible();
  });

  it("marks each file with the icon of its kind, and a folder as open or closed", async () => {
    show(task);

    const kindOf = (name: string) =>
      screen
        .getByRole(name === "solution" ? "button" : "link", { name })
        .querySelector("[data-kind]")
        ?.getAttribute("data-kind");
    const folder = await screen.findByRole("button", { name: "solution" });
    expect(kindOf("instruction.md")).toBe("markdown");
    expect(kindOf("solution")).toBe("folder");

    fireEvent.click(folder);
    await screen.findByRole("link", { name: "fix.patch" });
    expect(kindOf("solution")).toBe("folder-open");
    expect(kindOf("fix.patch")).toBe("diff");
  });

  const previews = {
    rendered: ".md h1",
    diff: "[data-line=head]",
    highlighted: "[class^=hljs-]",
  };

  it.each([
    ["Notes.MARKDOWN", "# A heading\n", "markdown", ["rendered"]],
    ["Change.Diff", "--- a/x\n+++ b/x\n", "diff", ["diff"]],
    ["Main.PY", "import os\n", "python", ["highlighted"]],
    ["DOCKERFILE", "FROM scratch\n", "docker", []],
    [".md", "# A heading\n", "other", []],
  ])(
    "shows %s with the icon and the preview of one kind",
    async (name, text, kind, shown) => {
      const { container } = show(
        { [`${root}/${name}`]: text },
        `${root}/${name}`,
      );

      await screen.findByText(/^[12] lines?/);
      expect(
        screen
          .getByRole("link", { name })
          .querySelector("[data-kind]")
          ?.getAttribute("data-kind"),
      ).toBe(kind);
      expect(
        Object.entries(previews)
          .filter(([, selector]) => container.querySelector(selector) !== null)
          .map(([preview]) => preview),
      ).toEqual(shown);
    },
  );

  it("marks a symlink with the icon of any other file", async () => {
    show(task);

    const latest = (await screen.findByText("symlink")).parentElement;
    expect(
      latest?.querySelector("[data-kind]")?.getAttribute("data-kind"),
    ).toBe("other");
  });

  it("highlights source by its extension", async () => {
    const { container } = show(task, `${root}/src/tinyparse/header.py`);

    await screen.findByText("1 line · 10 B");
    expect(container.querySelector(".hljs-keyword")).toHaveTextContent(
      "import",
    );
  });

  it("colours a diff by line", async () => {
    const { container } = show(task, `${root}/solution/fix.patch`);

    await screen.findByText("6 lines · 54 B");
    const kinds = [...container.querySelectorAll("[data-line]")].map((line) =>
      line.getAttribute("data-line"),
    );
    expect(kinds).toEqual(["head", "head", "hunk", "context", "remove", "add"]);
  });

  it("tells a removed -- line or an added ++ line in a hunk from a header", async () => {
    const { container } = show(
      {
        [`${root}/notes.diff`]:
          "--- a/notes.md\n+++ b/notes.md\n@@ -1,3 +1,3 @@\n--- a rule\n+++ a heading\n-x\n+y\n same\n\\ No newline at end of file\n",
      },
      `${root}/notes.diff`,
    );

    await screen.findByText(/^9 lines/);
    const kinds = [...container.querySelectorAll("[data-line]")].map((line) =>
      line.getAttribute("data-line"),
    );
    expect(kinds).toEqual([
      "head",
      "head",
      "hunk",
      "remove",
      "add",
      "remove",
      "add",
      "context",
      "context",
    ]);
  });

  it("shows only the size of a file that is not UTF-8, and a link that downloads it", async () => {
    show(task, `${root}/data.bin`);

    expect(
      await screen.findByText(
        "Not shown: not UTF-8 text, 3 B. Download it with the link above.",
      ),
    ).toBeVisible();
    const link = screen.getByRole("link", { name: "Download" });
    expect(link).toHaveAttribute(
      "href",
      `/api/projects/sample/files?${new URLSearchParams({ attempt: "a/b", path: `${root}/data.bin` }).toString()}`,
    );
    expect(link).toHaveAttribute("download", "data.bin");
  });

  it("gives a text file a link that downloads it too", async () => {
    show(task, `${root}/src/tinyparse/header.py`);

    await screen.findByText("1 line · 10 B");
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "download",
      "header.py",
    );
  });

  it("says why a file over the limit is neither shown nor downloaded, and where it is", async () => {
    show(task, `${root}/huge.log`);

    expect(
      await screen.findByText(
        `Too large to show or download here: 20.0 MiB, over the 16.0 MiB limit. It is at ${root}/huge.log in the attempt's directory.`,
      ),
    ).toBeVisible();
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
  });

  it("shows each file's size in the tree", async () => {
    show(task);

    const tree = await screen.findByRole("navigation", { name: "Files" });
    expect(
      await within(tree).findByRole("link", { name: /^data\.bin/ }),
    ).toHaveTextContent("data.bin3 B");
    expect(
      within(tree).getByRole("link", { name: /^instruction\.md/ }),
    ).toHaveTextContent(/instruction\.md[0-9.]+ B$/);
  });

  it("says which run made it, a link to that run", async () => {
    show(task);

    const from = await screen.findByRole("link", { name: /· run 3$/ });
    expect(from).toHaveAttribute(
      "href",
      "/sample/attempt?id=a%2Fb&tab=runs&run=2",
    );
    expect(from.closest("p")).toHaveTextContent(/^From .* · run 3$/);
  });

  it("lists a symlink without opening it", async () => {
    const { fetch } = show(task);

    await preview();
    expect(await screen.findByText("symlink")).toBeVisible();
    expect(screen.queryByRole("link", { name: /latest/ })).toBeNull();
    const asked = fetch.mock.calls.map(([url]) => url);
    expect(asked.some((url) => url.includes("latest"))).toBe(false);
  });

  it("shows the reason of a deliverable that was not reached", () => {
    const { fetch } = show(task, undefined, null);

    expect(screen.getByText("Not run: author was never visited")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    // No line says which run made it, not even an empty one.
    expect(document.querySelector("header p")).toBeNull();
  });
});

describe("the files view read again while the attempt runs", () => {
  it("lists what is new in its open folders and keeps what was opened", async () => {
    const files = { ...task };
    fakeFiles(files);
    const shown = (refresh: number) => (
      <RefreshContext value={refresh}>
        <MemoryRouter initialEntries={["/sample/attempt?id=a%2Fb&tab=files"]}>
          <FilesView
            project={sampleProject}
            attempt="a/b"
            record={records.passed}
            deliverable={deliverable(root)}
          />
        </MemoryRouter>
      </RefreshContext>
    );
    const { rerender } = render(shown(0));
    expect(await preview()).toHaveTextContent("instruction.md");
    const folder = await screen.findByRole("button", { name: "solution" });
    fireEvent.click(folder);
    await screen.findByRole("link", { name: "fix.patch" });

    files[`${root}/solution/notes.md`] = "notes";
    files[`${root}/zz.txt`] = "zz";
    rerender(shown(1));

    expect(await screen.findByRole("link", { name: "notes.md" })).toBeVisible();
    expect(await screen.findByRole("link", { name: "zz.txt" })).toBeVisible();
    expect(folder).toHaveAttribute("aria-expanded", "true");
    expect(await preview()).toHaveTextContent("instruction.md");
  });
});
