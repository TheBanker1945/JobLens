// The guide (7.9.3): a new account's way in, one question at a time.
//
// The CV first, then what someone wants, each question on its own with a list
// to pick from, a bar that fills as they go, and a way to skip any of them.
// Every answer is saved the moment Continue is pressed (PUT /api/preferences,
// the whole document, as the one-page form saves it), so leaving halfway
// loses nothing. The end is a summary with a first match.
//
// "/guide?open" asks only the questions still open: the dashboard's way back
// in. Skipping never erases an answer given before; clearing one is the
// one-page form's job. Finishing, or "Finish later", marks the guide done on
// the account (PATCH /api/me {onboarded: true}), so the dashboard stops
// sending a new account here.

import { api, ApiError } from "./api.js";
import { removedKinds, uploadForm } from "./cv-upload.js";
import { byId, fill, h, show } from "./dom.js";
import { formatList, formatMoney, loadLanguage, t, translate } from "./i18n.js";
import { serverProblems } from "./prefs-form.js";
import { followJob } from "./progress.js";
import { PHASES, QUESTIONS, isAnswered } from "./questions.js";

const ONLY_OPEN = new URLSearchParams(window.location.search).has("open");
// A single-choice question answered with a click moves on by itself, after
// a moment to see the choice land. Not with the keyboard: arrow keys move
// through the choices, and each would jump to the next question.
const MOVE_ON_MS = 300;

let prefs = null;
let cv = null;
let places = [];
let asked = QUESTIONS; // this visit's questions; asked.length is the summary
let at = 0;
let backToSummary = false;
let byPointer = false;
let words = []; // the typed words on screen (employers, own sectors)
const skipped = new Set();

await loadLanguage();
translate();

try {
  [prefs, cv, places] = await Promise.all([
    api("/api/preferences"),
    activeCv(),
    api("/api/places"),
  ]);
  if (ONLY_OPEN) asked = QUESTIONS.filter((q) => !isAnswered(q, prefs, cv));
  wire();
  const first = asked.findIndex((q) => !isAnswered(q, prefs, cv) && !blocked(q));
  go(first === -1 ? asked.length : first, { focus: false });
} catch (error) {
  say(errorText(error));
}

function wire() {
  byId("later").addEventListener("click", (event) => {
    event.preventDefault();
    finish();
  });
  byId("back").addEventListener("click", () => {
    backToSummary = false;
    go(at - 1, { step: -1 });
  });
  byId("skip").addEventListener("click", skip);
  byId("next").addEventListener("click", next);
  const area = byId("question");
  area.addEventListener("pointerdown", () => { byPointer = true; });
  area.addEventListener("keydown", () => { byPointer = false; });
}

// -- moving through ------------------------------------------------------------

function go(index, { step = 1, focus = true } = {}) {
  let i = index;
  // A distance needs a home to measure from; without one it is passed by.
  while (i >= 0 && i < asked.length && blocked(asked[i])) {
    skipped.add(asked[i].id);
    i += step;
  }
  at = Math.max(0, Math.min(asked.length, i));
  say(null);
  draw();
  if (focus) byId("q-title")?.focus();
  window.scrollTo({ top: 0 });
}

function blocked(q) {
  return Boolean(q.needs) && !isAnswered({ id: q.needs }, prefs);
}

async function next() {
  const q = asked[at];
  if (!q) return;
  if (q.kind !== "cv") {
    const patch = collect(q);
    if (patch === null) return;
    if (Object.keys(patch).length > 0 && !(await save(patch))) return;
  }
  skipped.delete(q.id);
  if (backToSummary) {
    backToSummary = false;
    return go(asked.length);
  }
  go(at + 1);
}

function skip() {
  const q = asked[at];
  if (!q) return;
  if (!isAnswered(q, prefs, cv)) skipped.add(q.id);
  if (backToSummary) {
    backToSummary = false;
    return go(asked.length);
  }
  go(at + 1);
}

function edit(q) {
  if (!asked.includes(q)) asked = QUESTIONS;
  backToSummary = true;
  go(asked.indexOf(q));
}

async function save(patch) {
  const button = byId("next");
  button.disabled = true;
  try {
    prefs = await api("/api/preferences", { method: "PUT", json: { ...prefs, ...patch } });
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      const [key] = Object.values(serverProblems(error.details));
      say(t(key));
    } else {
      say(errorText(error));
    }
    return false;
  } finally {
    button.disabled = false;
  }
}

// -- drawing a question -----------------------------------------------------------

function draw() {
  drawMeter();
  const q = asked[at];
  show(byId("actions"), Boolean(q));
  document.body.classList.toggle("has-actions", Boolean(q));
  if (!q) return drawSummary();
  words = [];
  const title = h("h1", { id: "q-title", tabindex: "-1" }, t(q.title));
  const hint = q.hint && h("p", { class: "hint" }, t(q.hint));
  fill(byId("question"), title, hint, body(q), h("p", { class: "changes" },
    h("strong", {}, t("onb.whatChanges")), " ", t(q.changes)));
  byId("back").disabled = at === 0;
  byId("skip").hidden = q.kind === "cv" && Boolean(cv);
  byId("next").textContent = backToSummary ? t("onb.save") : t("onb.continue");
  refreshNext();
}

function drawMeter() {
  const q = asked[at];
  const total = asked.length;
  const phase = q ? PHASES.find(([id]) => id === q.phase)[1] : "onb.summary.phase";
  byId("meter-phase").textContent = t(phase);
  const open = [...skipped].filter((id) => {
    const one = QUESTIONS.find((x) => x.id === id);
    return !isAnswered(one, prefs, cv) && !blocked(one);
  }).length;
  const count = q ? t("onb.count", { n: at + 1, total }) : t("onb.countDone", { total });
  byId("meter-count").textContent = open ? `${count} · ${t("onb.skipped", { n: open })}` : count;
  const share = total ? Math.round((at / total) * 100) : 100;
  byId("meter-fill").style.width = `${share}%`;
  byId("meter-bar").setAttribute("aria-valuenow", String(share));
}

function body(q) {
  if (q.kind === "cv") return cvBody();
  if (q.kind === "place") return placeBody(q);
  if (q.kind === "words") return wordsBody(q, prefs[q.id] || []);
  return choicesBody(q);
}

function choicesBody(q) {
  const single = q.kind === "single";
  const options = optionsOf(q);
  const now = q.get ? q.get(prefs) : prefs[q.id];
  const chosen = (value) => (single ? now === value : (now || []).includes(value));
  const list = h("div", { class: "options", role: single ? "radiogroup" : "group", "aria-labelledby": "q-title" },
    ...options.map(([value, label, sub], index) => {
      const input = h("input", {
        type: single ? "radio" : "checkbox", name: q.id, value: String(index), checked: chosen(value),
      });
      input.addEventListener("change", () => {
        refreshNext();
        if (single && byPointer) setTimeout(() => { if (input.checked && asked[at] === q) next(); }, MOVE_ON_MS);
      });
      return h("label", { class: "opt" },
        input,
        h("span", { class: `mark ${single ? "mark-radio" : "mark-check"}`, "aria-hidden": "true" }),
        h("span", { class: "opt-text" }, h("strong", {}, label), sub && h("span", {}, sub)),
      );
    }),
  );
  // Hours set on the form that no choice here matches: say what they are.
  const custom = q.id === "hours" && isAnswered(q, prefs) && q.get(prefs).length === 0 &&
    h("p", { class: "hint" }, t("onb.hours.now", { min: prefs.hours_min ?? 0, max: prefs.hours_max ?? 60 }));
  if (!q.own) return h("div", { class: "stack" }, list, custom);
  const labels = options.map(([value]) => value);
  return h("div", { class: "stack" }, list,
    wordsBody(q, (prefs[q.id] || []).filter((word) => !labels.includes(word)), "onb.sectors.own"));
}

// [stored value, label, sub-label] for each choice; a value set on the form
// that is not among them (a salary of 3,200) is offered too, so it shows.
function optionsOf(q) {
  const options = q.options.map(([value, spec, sub]) => {
    if (q.own) return [t(spec), t(spec), null]; // kept as the words chosen
    return [value, labelOf(spec), sub ? t(sub) : null];
  });
  const now = prefs[q.id];
  if (q.kind === "single" && isAnswered(q, prefs) && !options.some(([value]) => value === now)) {
    const spec = { salary_min: { money: now }, max_distance_km: { km: now } }[q.id];
    if (spec) options.push([now, labelOf(spec), null]);
  }
  return options;
}

function labelOf(spec) {
  if (typeof spec === "string") return t(spec);
  if ("money" in spec) return formatMoney(spec.money, "EUR");
  return t("onb.distance.km", { km: spec.km });
}

function placeBody(q) {
  const box = h("input", {
    class: "input input-large", id: "place", list: "places", autocomplete: "off",
    value: prefs[q.id] || "", "aria-labelledby": "q-title",
  });
  box.addEventListener("input", refreshNext);
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      next();
    }
  });
  return h("div", { class: "field" }, box,
    h("datalist", { id: "places" }, ...places.map((name) => h("option", { value: name }))));
}

function wordsBody(q, initial, placeholderKey = "onb.employers.placeholder") {
  words = [...initial];
  const box = h("input", { class: "input input-large", id: "word", autocomplete: "off", placeholder: t(placeholderKey) });
  const chips = h("div", { class: "chips" });
  const drawChips = () => {
    fill(chips, ...words.map((word) => {
      const remove = h("button", { type: "button", class: "chip-remove", "aria-label": t("onb.remove", { word }) }, "×");
      remove.addEventListener("click", () => {
        words = words.filter((one) => one !== word);
        drawChips();
        refreshNext();
      });
      return h("span", { class: "chip chip-word" }, word, remove);
    }));
  };
  const add = () => {
    const word = box.value.trim();
    if (word && !words.includes(word)) words.push(word);
    box.value = "";
    drawChips();
    refreshNext();
    box.focus();
  };
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  });
  const button = h("button", { type: "button", class: "button button-outline" }, t("onb.add"));
  button.addEventListener("click", add);
  drawChips();
  return h("div", { class: "field" }, h("div", { class: "word-row" }, box, button), chips);
}

function cvBody() {
  const upload = uploadForm({
    onDone: (fresh) => {
      cv = fresh;
      draw();
    },
    onError: (e) => say(errorText(e)),
  });
  if (!cv) return upload;
  const kinds = removedKinds(cv.removed);
  const another = h("button", { type: "button", class: "linkish" }, t("onb.cv.another"));
  const slot = h("div", { class: "stack" },
    h("div", { class: "success" },
      h("strong", {}, cv.filename),
      h("p", {}, kinds.length > 0 ? t("guide.cv.done", { list: formatList(kinds) }) : t("guide.cv.doneNothing")),
    ),
    another,
  );
  another.addEventListener("click", () => fill(slot, upload));
  return slot;
}

// -- reading the answer on screen -----------------------------------------------------

// What Continue saves: the fields to change, {} for nothing to change, or
// null when nothing is answered yet (Continue stays off).
function collect(q) {
  if (q.kind === "place") {
    const home = byId("place").value.trim();
    return home ? { home } : null;
  }
  if (q.kind === "words") return words.length ? { [q.id]: [...words] } : null;
  const options = optionsOf(q);
  const picked = [...byId("question").querySelectorAll(`input[name="${q.id}"]:checked`)]
    .map((input) => options[Number(input.value)][0]);
  if (q.kind === "single") return picked.length ? { [q.id]: picked[0] } : null;
  if (q.own) {
    const all = [...picked, ...words];
    return all.length ? { [q.id]: all } : null;
  }
  if (picked.length === 0) return isAnswered(q, prefs) ? {} : null;
  return q.set ? q.set(picked) : { [q.id]: picked };
}

function refreshNext() {
  const q = asked[at];
  byId("next").disabled = q.kind === "cv" ? !cv : collect(q) === null;
}

// -- the end: what was answered, and a first match ------------------------------------

function drawSummary() {
  const rows = QUESTIONS.map((q) => {
    const done = isAnswered(q, prefs, cv);
    const change = h("button", { type: "button", class: "linkish" },
      t(done ? "onb.summary.change" : "onb.summary.answer"));
    change.addEventListener("click", () => edit(q));
    return h("div", { class: "answer" },
      h("span", { class: "answer-q" }, t(q.short)),
      h("span", { class: "answer-a" }, done
        ? describe(q)
        : h("span", { class: "badge badge-possible" },
          t(skipped.has(q.id) ? "onb.summary.skipped" : "onb.summary.notAnswered"))),
      change,
    );
  });
  const open = QUESTIONS.filter((q) => !isAnswered(q, prefs, cv) && !blocked(q)).length;
  const toDashboard = h("button", { type: "button", class: "button button-outline" }, t("onb.toDashboard"));
  toDashboard.addEventListener("click", finish);
  let start = null;
  if (cv) {
    start = h("button", { type: "button", class: "button button-primary", id: "start" }, t("guide.first.start"));
    start.addEventListener("click", () => firstMatch(start, toDashboard));
  } else {
    start = h("button", { type: "button", class: "button button-primary" }, t("onb.summary.uploadCv"));
    start.addEventListener("click", () => edit(QUESTIONS[0]));
  }
  fill(byId("question"),
    h("h1", { id: "q-title", tabindex: "-1" }, t("onb.summary.title")),
    h("p", { class: "hint" }, open ? t("onb.summary.open", { n: open }) : t("onb.summary.all")),
    h("div", { class: "answers" }, ...rows),
    h("div", { class: "start" },
      h("div", {},
        h("strong", {}, t("guide.first.title")),
        h("p", {}, cv ? t("guide.first.intro") : t("onb.summary.noCv")),
        cv && h("p", { class: "muted" }, t("guide.first.cost")),
      ),
      h("div", { class: "start-buttons" }, toDashboard, start),
    ),
  );
}

function describe(q) {
  if (q.kind === "cv") return cv.filename;
  if (q.kind === "place") return prefs.home;
  if (q.kind === "words" || q.own) return formatList(prefs[q.id]);
  if (q.id === "hours") return t("wishes.hours", { min: prefs.hours_min ?? 0, max: prefs.hours_max ?? "…" });
  const options = optionsOf(q);
  const label = (value) => options.find(([one]) => one === value)?.[1] ?? String(value);
  return formatList((q.kind === "single" ? [prefs[q.id]] : prefs[q.id]).map(label));
}

async function firstMatch(start, toDashboard) {
  say(null);
  start.disabled = true;
  toDashboard.disabled = true;
  try {
    const job = await api("/api/matches", { method: "POST", json: { top: 10 } });
    const finished = await followJob(job.id);
    if (finished?.status === "failed") throw new Error(t("progress.failed", { reason: finished.error }));
    byId("progress-text").textContent = t("guide.first.done");
    await finish();
  } catch (error) {
    say(errorText(error));
    start.disabled = false;
    toDashboard.disabled = false;
  }
}

async function finish() {
  try {
    await api("/api/me", { method: "PATCH", json: { onboarded: true } });
  } finally {
    window.location.assign("/");
  }
}

// -- the rest -------------------------------------------------------------------------

async function activeCv() {
  try {
    return await api("/api/cvs/active");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
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
