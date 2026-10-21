import "@fontsource-variable/mona-sans";
import "@fontsource-variable/fira-code";
import "../ui/theme.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { applyTemplate, savedTemplate } from "../ui/template";
import type { ShareSource } from "./api";
import { ShareApp } from "./App";

/** Draw the share site into the page's #root, reading from source. */
export function mount(source: ShareSource) {
  applyTemplate(savedTemplate());
  const root = document.getElementById("root");
  if (root === null) throw new Error("the page has no #root");
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <ShareApp source={source} />
      </BrowserRouter>
    </StrictMode>,
  );
}
