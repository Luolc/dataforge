import { Navigate, Route, Routes } from "react-router";
import { use } from "react";

import { FileSourceContext } from "../api/client";
import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import {
  type ShareSource,
  ShareSourceContext,
  SignedContext,
  useShareSource,
} from "./api";
import { ItemPage } from "./ItemPage";
import { ItemsPage } from "./ItemsPage";
import { ShareShell } from "./Shell";

/** The site, reading from source: the share server's API or a static site. */
export function ShareApp({ source }: { source: ShareSource }) {
  return (
    <ShareSourceContext value={source}>
      <FileSourceContext value={source.files}>
        <Pages />
      </FileSourceContext>
    </ShareSourceContext>
  );
}

function Pages() {
  return (
    <Routes>
      <Route element={<ShareShell />}>
        <Route index element={<FirstProject />} />
        <Route path=":project" element={<ItemsPage />} />
        <Route path=":project/items/:item" element={<ItemPage />} />
        <Route
          path="*"
          element={
            <Frame crumbs={[{ label: "Not found" }]}>
              <Note>No page here.</Note>
            </Frame>
          }
        />
      </Route>
    </Routes>
  );
}

/** The route /: the first project, which the sidebar lists first. */
function FirstProject() {
  const { projects } = use(SignedContext);
  const { access } = useShareSource();
  if (projects.state === "ok" && projects.data[0] !== undefined)
    return (
      <Navigate to={`/${encodeURIComponent(projects.data[0].id)}`} replace />
    );
  return (
    <Frame crumbs={[{ label: "Projects" }]}>
      <Note>
        {projects.state === "error"
          ? projects.message
          : projects.state === "loading"
            ? "Loading…"
            : access.kind === "token"
              ? "This token names no project."
              : "This site has no project."}
      </Note>
    </Frame>
  );
}
