// The guide for a new account (7.7.2): your CV, what you want, a first match.
//
// It opens on the first step not yet done, lets you go back, and can be
// skipped at any point. Skipping or finishing marks it done on the account
// (PATCH /api/me {onboarded: true}), so the dashboard does not send you back.

import { api, ApiError } from "./api.js";
import { removedKinds, uploadForm } from "./cv-upload.js";
import { byId, h, show } from "./dom.js";
import { formatList, loadLanguage, t, translate } from "./i18n.js";
import { preferencesForm, serverProblems } from "./prefs-form.js";
import { followJob } from "./progress.js";

await loadLanguage();
translate();

let cv = null;
let form = null;

try {
  const [active, prefs, places] = await Promise.all([
    activeCv(),
    api("/api/preferences"),
    api("/api/places"),
  ]);
  cv = active;
  form = preferencesForm(prefs, places);
  byId("prefs-fields").replaceChildren(form.element);
  byId("cv-upload").append(uploadForm({ onDone: uploaded, onError: (e) => say(errorText(e)) }));
  showCv();
  wire();
  goTo(!cv ? 1 : isEmpty(prefs) ? 2 : 3, { focus: false });
} catch (error) {
  say(errorText(error));
}

function wire() {
  byId("skip").addEventListener("click", async (event) => {
    event.preventDefault();
    await done();
  });
  byId("to-2").addEventListener("click", () => goTo(2));
  byId("back-1").addEventListener("click", () => goTo(1));
  byId("back-2").addEventListener("click", () => goTo(2));
  byId("to-3").addEventListener("click", saveWishes);
  byId("start").addEventListener("click", firstMatch);
}

function goTo(step, { focus = true } = {}) {
  say(null);
  for (const n of [1, 2, 3]) {
    show(byId(`step-${n}`), n === step);
    const marker = byId(`stepper-${n}`);
    marker.classList.toggle("is-done", n < step);
    marker.classList.toggle("is-current", n === step);
    if (n === step) marker.setAttribute("aria-current", "step");
    else marker.removeAttribute("aria-current");
  }
  if (focus) byId(`step-${step}-title`).focus();
}

// -- step 1: the CV -----------------------------------------------------------

async function activeCv() {
  try {
    return await api("/api/cvs/active");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

async function uploaded(fresh) {
  cv = fresh;
  showCv();
}

function showCv() {
  show(byId("foot-1"), Boolean(cv));
  if (!cv) return byId("cv-now").replaceChildren();
  const kinds = removedKinds(cv.removed);
  byId("cv-now").replaceChildren(
    h("div", { class: "success" },
      h("strong", {}, cv.filename),
      h("p", {}, kinds.length > 0
        ? t("guide.cv.done", { list: formatList(kinds) })
        : t("guide.cv.doneNothing")),
    ),
  );
}

// -- step 2: what you want ---------------------------------------------------

async function saveWishes() {
  say(null);
  const values = form.read();
  if (!values) return;
  const button = byId("to-3");
  button.disabled = true;
  try {
    await api("/api/preferences", { method: "PUT", json: values });
    goTo(3);
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) form.showErrors(serverProblems(error.details));
    else say(errorText(error));
  } finally {
    button.disabled = false;
  }
}

function isEmpty(prefs) {
  return Object.entries(prefs).every(([key, value]) =>
    key === "version" || value === null || (Array.isArray(value) && value.length === 0),
  );
}

// -- step 3: a first match ----------------------------------------------------

async function firstMatch() {
  say(null);
  const start = byId("start");
  start.disabled = true;
  byId("back-2").disabled = true;
  try {
    const job = await api("/api/matches", { method: "POST", json: { top: 10 } });
    const finished = await followJob(job.id);
    if (finished?.status === "failed") throw new Error(t("progress.failed", { reason: finished.error }));
    byId("progress-text").textContent = t("guide.first.done");
    await done();
  } catch (error) {
    say(errorText(error));
    start.disabled = false;
    byId("back-2").disabled = false;
  }
}

async function done() {
  try {
    await api("/api/me", { method: "PATCH", json: { onboarded: true } });
  } finally {
    window.location.assign("/");
  }
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
