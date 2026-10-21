// The share server's API as the site's source (design-share.md section 4,
// "The API"): a person signs in with a token on the landing page.
import { apiFiles, urls } from "../api/client";
import type { ShareSource } from "./api";
import { Landing } from "./Landing";
import { signOut } from "./token";

export const apiSource: ShareSource = {
  projects: urls.projects(),
  items: (p) => `/api/projects/${encodeURIComponent(p)}/items`,
  record: urls.record,
  files: apiFiles,
  download: null,
  access: { kind: "token", Landing, signOut },
};
