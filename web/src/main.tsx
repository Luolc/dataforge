import "@fontsource-variable/mona-sans";
import "@fontsource-variable/fira-code";
import "./ui/theme.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { applyTemplate, savedTemplate } from "./ui/template";

applyTemplate(savedTemplate());

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
