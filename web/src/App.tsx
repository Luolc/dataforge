import { useContext } from "react";
import { Navigate, Route, Routes } from "react-router";

import { Frame, Shell } from "./shell/Shell";
import { ProjectsContext } from "./shell/projects";
import { Note } from "./ui/card";
import { AttemptPage } from "./views/attempt/AttemptPage";
import { Monitor } from "./views/monitor/Monitor";
import { HomePage } from "./views/HomePage";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="monitor" element={<Monitor />} />
        <Route path=":project" element={<HomePage />} />
        <Route path=":project/attempt" element={<AttemptPage />} />
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

/** "/" opens the first project. */
function Home() {
  const projects = useContext(ProjectsContext);
  const first = projects.state === "ok" ? projects.data[0] : undefined;
  if (first)
    return <Navigate to={`/${encodeURIComponent(first.id)}`} replace />;
  const note =
    projects.state === "error"
      ? projects.message
      : projects.state === "loading"
        ? "Loading…"
        : "The site has no projects.";
  return (
    <Frame crumbs={[{ label: "Projects" }]}>
      <Note>{note}</Note>
    </Frame>
  );
}
