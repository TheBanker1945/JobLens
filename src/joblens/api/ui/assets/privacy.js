// The privacy page (7.8.3): for everyone, signed in or not. The words are the
// dictionary's; who runs this JobLens and how to reach them come from the
// server's settings (GET /api/privacy) and go in as text, like everything else.

import { api } from "./api.js";
import { byId } from "./dom.js";
import { loadLanguage, t, translate } from "./i18n.js";

await loadLanguage();
translate();

let who = { operator: null, contact: null };
try {
  who = await api("/api/privacy", { signedIn: false });
} catch {
  // Unreachable: the page still says everything but the two names.
}
const unset = t("privacy.unset");
byId("who").textContent = t("privacy.who.text", {
  operator: who.operator || unset,
  contact: who.contact || unset,
});
byId("rights").textContent = t("privacy.rights.text", { contact: who.contact || unset });
