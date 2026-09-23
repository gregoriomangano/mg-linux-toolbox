// Single source of truth for every author/contact/support detail the
// startup nudge and the "Chi sono" page show. Nothing here is duplicated
// anywhere else, and every external address is opened through the Tauri 2
// opener plugin (never a shell, never an arbitrary command line).
export const AUTHOR = {
  name: "Gregorio Mangano",
  avatar: "assets/mg-linux-toolbox.svg",
  youtube: "https://www.youtube.com/@GregorioMangano",
  website: "https://www.manganogregorio.it/",
  github: "https://github.com/gregoriomangano",
  support: "https://www.manganogregorio.it/donazioni.html",
};

// The only external addresses this app is allowed to open. Anything else is
// refused before it can ever reach Tauri, so no frontend value can turn
// into an arbitrary navigation.
const EXTERNAL_ALLOWLIST = new Set([
  AUTHOR.youtube,
  AUTHOR.website,
  AUTHOR.github,
  AUTHOR.support,
]);

/// Opens one allow-listed https address in the user's default browser
/// through the official Tauri opener plugin command. Returns `false`
/// (without opening anything) for anything that is not an exact,
/// allow-listed https URL; never navigates the webview, never closes the
/// window, never uses window.open/location.href and never builds a shell.
export async function openExternalUrl(url) {
  let parsed;
  try {
    parsed = new URL(String(url));
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  if (!EXTERNAL_ALLOWLIST.has(parsed.href)) return false;
  await window.__TAURI__.core.invoke("plugin:opener|open_url", {
    url: parsed.href,
  });
  return true;
}

/// Builds an external link: a real anchor (so it stays keyboard reachable
/// and inspectable) whose click is always intercepted and routed through
/// [`openExternalUrl`]. `target="_blank"` is deliberately not used: the
/// plugin's own link handler is not the mechanism here.
export function externalLink(url, className, text) {
  const link = document.createElement("a");
  link.href = url;
  link.className = className;
  link.textContent = text;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    openExternalUrl(url).catch((error) =>
      console.error("External link could not be opened", error),
    );
  });
  return link;
}
