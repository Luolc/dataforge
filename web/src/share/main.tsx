import "@fontsource-variable/mona-sans";
import "@fontsource-variable/fira-code";
import "../ui/theme.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { applyTemplate, savedTemplate } from "../ui/template";
import { ShareApp } from "./App";

applyTemplate(savedTemplate());

const root = document.getElementById("root");
if (root === null) throw new Error("share.html has no #root");
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <ShareApp />
    </BrowserRouter>
  </StrictMode>,
);
