import { Route, Routes } from "react-router";

import { Frame, Shell } from "./shell/Shell";
import { Note } from "./ui/card";
import { AttemptPage } from "./views/attempt/AttemptPage";
import { Monitor } from "./views/monitor/Monitor";
import { HomePage } from "./views/HomePage";
import { ProjectsPage } from "./views/ProjectsPage";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<ProjectsPage />} />
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
