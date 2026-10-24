import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router";

import { Frame, Shell } from "./shell/Shell";
import { Note } from "./ui/card";
import { AttemptPage } from "./views/attempt/AttemptPage";
import { DefinitionPage } from "./views/definition/DefinitionPage";
import { HomePage } from "./views/HomePage";
import { ProjectsPage } from "./views/ProjectsPage";
import { QueuePage } from "./views/queue/QueuePage";
import { SnapshotPage } from "./views/snapshot/SnapshotPage";

// uPlot loads with the Monitor page only, the one page that draws charts.
const Monitor = lazy(() => import("./views/monitor/Monitor"));

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<ProjectsPage />} />
        <Route
          path="monitor"
          element={
            <Suspense
              fallback={
                <Frame crumbs={[{ label: "Monitor" }]}>
                  <Note>Loading…</Note>
                </Frame>
              }
            >
              <Monitor />
            </Suspense>
          }
        />
        <Route path="queue" element={<QueuePage />} />
        <Route path=":project" element={<HomePage />} />
        <Route path=":project/attempt" element={<AttemptPage />} />
        <Route path=":project/definition" element={<DefinitionPage />} />
        <Route path=":project/:page" element={<SnapshotPage />} />
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
