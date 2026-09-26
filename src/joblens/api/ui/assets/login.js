// The page a login link opens: one button that signs you in (7.5, restyled 7.7).
//
// A login link is /login#<code>. What follows "#" never reaches the server, so
// the code cannot end up in a log; this script reads it, wipes it from the
// address bar, and sends it once, when the button is pressed. Opening the link
// signs nobody in -- a chat app showing a preview of the link would otherwise
// use it up.

import { api, ApiError } from "./api.js";
import { byId, show } from "./dom.js";
import { loadLanguage, t, translate } from "./i18n.js";

const code = window.location.hash.slice(1);
history.replaceState(null, "", window.location.pathname);

await loadLanguage();
translate();

const button = byId("go");
const note = byId("note");

if (!code) {
  // Somebody without a link, or signed in already: in that case, go home.
  byId("message").textContent = t("login.noCode");
  show(button, false);
  show(note, false);
  fetch("/api/me", { credentials: "same-origin" })
    .then((answer) => answer.ok && window.location.replace("/"))
    .catch(() => {});
}

button.addEventListener("click", async () => {
  button.disabled = true;
  note.classList.remove("is-error");
  note.textContent = t("login.signingIn");
  try {
    await api("/api/login", { method: "POST", json: { token: code }, signedIn: false });
    window.location.replace("/");
  } catch (error) {
    note.classList.add("is-error");
    note.textContent =
      error instanceof ApiError && error.status === 0 ? t("error.offline") : t("login.failed");
  }
});
