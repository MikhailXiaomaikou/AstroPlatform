export function consumeInvitationFromUrl(): string {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
  const invitation = fragment.get("invite") || "";
  const unsafeQueryWasPresent = url.searchParams.has("invite");
  if (unsafeQueryWasPresent) {
    // Query parameters are sent in the initial HTTP request and may already be
    // present in CDN/access logs. Never accept them as invitation credentials.
    url.searchParams.delete("invite");
  }
  if (invitation) {
    fragment.delete("invite");
    url.hash = fragment.toString();
  }
  if (invitation || unsafeQueryWasPresent) {
    const query = url.searchParams.toString();
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${query ? `?${query}` : ""}${url.hash ? `#${url.hash.replace(/^#/, "")}` : ""}`,
    );
  }
  return invitation;
}
