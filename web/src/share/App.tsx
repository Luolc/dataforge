import { Navigate, Route, Routes } from "react-router";
import { use } from "react";

import { Frame } from "../shell/Shell";
import { Note } from "../ui/card";
import { SignedContext } from "./api";
import { ItemPage } from "./ItemPage";
import { ItemsPage } from "./ItemsPage";
import { ShareShell } from "./Shell";

export function ShareApp() {
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

/** The route /: the token's first project, which the sidebar lists first. */
function FirstProject() {
  const { projects } = use(SignedContext);
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
            : "This token names no project."}
      </Note>
    </Frame>
  );
}
