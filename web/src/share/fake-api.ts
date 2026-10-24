// For tests: a fetch that answers the share site's API from the synthetic
// export in fixtures/share, as dataforge.share would, with one token that
// signs in.
import { vi } from "vitest";
import { z } from "zod";

const ROOT = "../../fixtures/share/";

const files: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>("../../fixtures/share/**/*", {
      query: "?raw",
      import: "default",
      eager: true,
    }),
  ).map(([key, text]) => [key.slice(ROOT.length), text]),
);

/** The one token the fake server accepts. */
export const GOOD_TOKEN = "dfs_0000beef_fixture";

const exportJson = z.looseObject({
  project: z.looseObject({ id: z.string() }),
  session: z.unknown(),
  statuses: z.unknown(),
  columns: z.array(z.unknown()).default([]),
  trials: z.unknown().default(null),
  deliverables: z.array(z.unknown()).default([]),
  kinds: z.unknown().default({}),
  items: z.array(z.unknown()),
});

/** The fixture's export.json. */
const exported = exportJson.parse(JSON.parse(files["export.json"] ?? "{}"));

interface Fake {
  /** Whether the cookie is set: false until a sign-in with GOOD_TOKEN. */
  signedIn: boolean;
  /** The reason a refusal gives, which the page must not repeat. */
  reason: string;
  /** Whether a sign-out gets no answer. */
  logoutFails: boolean;
}

/** Stub the global fetch with the fake server; returns its state and spy. */
export function fakeShare(signedIn = false) {
  const state: Fake = {
    signedIn,
    reason: "a valid token is needed",
    logoutFails: false,
  };
  const fetch = vi.fn(async (input: string, init?: RequestInit) =>
    Promise.resolve(answer(state, input, init)),
  );
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

const refused = (state: Fake) =>
  Response.json(
    { error: "unauthorized", reason: state.reason },
    { status: 401 },
  );

function answer(state: Fake, input: string, init?: RequestInit): Response {
  const url = new URL(input, "http://share");
  const method = init?.method ?? "GET";
  if (url.pathname === "/api/login" && method === "POST") {
    const body = z
      .looseObject({ token: z.unknown() })
      .safeParse(
        JSON.parse(typeof init?.body === "string" ? init.body : "null"),
      );
    if (!body.success || body.data.token !== GOOD_TOKEN) return refused(state);
    state.signedIn = true;
    return new Response(null, { status: 204 });
  }
  if (url.pathname === "/api/logout" && method === "POST") {
    if (state.logoutFails) throw new TypeError("Failed to fetch");
    state.signedIn = false;
    return new Response(null, { status: 204 });
  }
  if (!state.signedIn) return refused(state);
  const { project, session, statuses, columns, trials, deliverables, kinds } =
    exported;
  const base = `/api/projects/${project.id}`;
  if (url.pathname === "/api/projects")
    return Response.json([
      { ...project, session, statuses, columns, trials, deliverables, kinds },
    ]);
  if (url.pathname === `${base}/items`) return Response.json(exported.items);
  const item = url.searchParams.get("attempt") ?? "";
  if (url.pathname === `${base}/record`)
    return file(`items/${item}/record.json`, "application/json");
  if (url.pathname === `${base}/files`) {
    const path = url.searchParams.get("path") ?? "";
    return file(`items/${item}/${path}`, "application/octet-stream");
  }
  return Response.json(
    { error: "not found", reason: "no such route" },
    { status: 404 },
  );
}

function file(path: string, type: string): Response {
  const text = files[path];
  if (text !== undefined)
    return new Response(text, { headers: { "Content-Type": type } });
  const names = new Map<string, string>();
  for (const key of Object.keys(files)) {
    if (!key.startsWith(`${path}/`)) continue;
    const [name = "", ...rest] = key.slice(path.length + 1).split("/");
    names.set(name, rest.length > 0 ? "dir" : "file");
  }
  if (names.size === 0)
    return Response.json(
      { error: "not found", reason: "no such file" },
      { status: 404 },
    );
  const entries = [...names]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([name, kind]) => ({
      name,
      type: kind,
      size: kind === "file" ? 1 : null,
    }));
  return Response.json({ entries });
}
