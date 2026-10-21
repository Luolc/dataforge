// The page a person sees before they sign in: one field for the access token
// they were given. The token goes to the server in a POST body and is kept
// nowhere else; the server answers with a cookie.
import { LogIn } from "lucide-react";
import { useState } from "react";

import { toneGlyph, toneText } from "../ui/status";
import { TemplateSwitch } from "../ui/template-switch";
import { signIn } from "./token";

/** What every refused token is told, whatever the server's reason. */
export const REFUSED =
  "This token was not accepted. Check it, or ask for a new one.";

type Shown =
  | { state: "idle" }
  | { state: "busy" }
  | { state: "refused" }
  | { state: "failed"; message: string };

export function Landing({ onSignedIn }: { onSignedIn: () => void }) {
  const [token, setToken] = useState("");
  const [shown, setShown] = useState<Shown>({ state: "idle" });
  const problem =
    shown.state === "refused"
      ? REFUSED
      : shown.state === "failed"
        ? `Signing in failed: ${shown.message}`
        : null;
  const Glyph = toneGlyph.bad;
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-header flex-none items-center px-4">
        <TemplateSwitch />
      </header>
      <main className="flex flex-auto justify-center px-4 pt-24">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setShown({ state: "busy" });
            void signIn(token.trim()).then((result) => {
              if (result.state === "in") {
                setToken("");
                onSignedIn();
              } else setShown(result);
            });
          }}
          aria-labelledby="sign-in"
          className="flex h-fit w-full max-w-sm flex-col gap-4 rounded-panel border border-line bg-panel p-6"
        >
          <h1 id="sign-in" className="text-xl font-semibold tracking-tight">
            Sign in
          </h1>
          <p className="text-fg-2">Enter the access token you were given.</p>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-fg-2">Access token</span>
            <input
              type="password"
              name="token"
              autoComplete="off"
              spellCheck={false}
              required
              value={token}
              onChange={(e) => {
                setToken(e.target.value);
              }}
              className="h-8 target rounded-row border border-chip-line bg-chip px-2 font-mono text-xs text-fg"
            />
          </label>
          {problem !== null && (
            <p
              role="alert"
              className={`flex items-start gap-1.5 text-xs ${toneText.bad}`}
            >
              <Glyph className="mt-px size-3.5 flex-none" aria-hidden />
              {problem}
            </p>
          )}
          <button
            type="submit"
            disabled={shown.state === "busy"}
            className="inline-flex h-8 target items-center justify-center gap-1.5 rounded-row bg-chip-on px-3 font-medium text-chip-on-fg disabled:opacity-idle"
          >
            <LogIn className="size-3.5" aria-hidden />
            {shown.state === "busy" ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}
