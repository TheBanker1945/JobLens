// "What I'm looking for": the one form, filled with the saved answers.
// Saving replaces all answers (PUT /api/preferences); the next match uses them.

import { api, ApiError } from "./api.js";
import { byId, show } from "./dom.js";
import { setUpFrame } from "./frame.js";
import { loadLanguage, t, translate } from "./i18n.js";
import { preferencesForm, serverProblems } from "./prefs-form.js";

await loadLanguage();
translate();

try {
  const [me, prefs, places] = await Promise.all([
    api("/api/me"),
    api("/api/preferences"),
    api("/api/places"),
  ]);
  setUpFrame("/preferences", me, { onError: (e) => say(errorText(e)) });
  const form = preferencesForm(prefs, places);
  byId("prefs-fields").replaceChildren(form.element);
  byId("prefs").addEventListener("submit", async (event) => {
    event.preventDefault();
    say(null);
    byId("saved").textContent = "";
    const values = form.read();
    if (!values) return;
    const button = byId("save");
    button.disabled = true;
    try {
      await api("/api/preferences", { method: "PUT", json: values });
      byId("saved").textContent = t("prefs.saved");
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        form.showErrors(serverProblems(error.details));
      } else {
        say(errorText(error));
      }
    } finally {
      button.disabled = false;
    }
  });
} catch (error) {
  say(errorText(error));
}

function say(text) {
  const notice = byId("notice");
  notice.textContent = text || "";
  show(notice, Boolean(text));
}

function errorText(error) {
  if (error instanceof ApiError && error.status === 0) return t("error.offline");
  if (error?.message) return error.message;
  return t("error.generic");
}
