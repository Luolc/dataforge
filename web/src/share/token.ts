// Signing in to the share server with a token, and out (design-share.md
// section 4, "Login, cookie and headers"): the server sets and clears the
// cookie that carries the token.

const urls = { login: "/api/login", logout: "/api/logout" };

/** What a sign-in came to: in, refused, or failed for another reason. */
export type SignIn =
  { state: "in" } | { state: "refused" } | { state: "failed"; message: string };

/**
 * Sign in with a token: the server sets the cookie that carries it. The
 * token goes in the body alone, never in a URL.
 */
export async function signIn(token: string): Promise<SignIn> {
  let response: Response;
  try {
    response = await fetch(urls.login, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch (error) {
    return { state: "failed", message: messageOf(error) };
  }
  if (response.ok) return { state: "in" };
  // Every refusal, whatever its reason, is the same 401.
  if (response.status === 401) return { state: "refused" };
  return { state: "failed", message: response.status.toString() };
}

/**
 * Sign out: the server clears the cookie. Null once it has, else why it
 * failed, and the cookie is still set.
 */
export async function signOut(): Promise<string | null> {
  try {
    const response = await fetch(urls.logout, { method: "POST" });
    return response.ok ? null : response.status.toString();
  } catch (error) {
    return messageOf(error);
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
