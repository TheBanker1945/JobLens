// The matches page (7.7 step 3): everything one match read, why, and what you
// think of it.
//
// One request, GET /api/results, gives a match of the active CV: every judged
// vacancy with the lines of the CV it used (each found in the CV before it was
// stored), what it misses, and what it contradicts of your answers. A mark
// needs a reason; the server keeps it as typed with what the judge said at
// the time, and never overwrites an earlier one (service/marks.py).

import { api, ApiError } from "./api.js";
import { byId, h, show } from "./dom.js";
import { setUpFrame } from "./frame.js";
import {
  formatDate,
  formatList,
  formatMoney,
  formatNumber,
  loadLanguage,
  t,
  translate,
} from "./i18n.js";
import { followJob } from "./progress.js";

const CALLS = ["apply", "maybe", "no"];
const FILTERS = ["all", "strong", "possible", "weak", "unmarked"];
const REASON_LIMIT = 1000; // service/marks.py

let data = null;
let filter = "all";

await loadLanguage();
translate();

try {
  const me = await api("/api/me");
  setUpFrame("/matches", me, { onError: (e) => say(errorText(e)) });
  byId("start").addEventListener("click", startMatch);
  byId("run").addEventListener("change", () => load(byId("run").value));
  await load(new URLSearchParams(window.location.search).get("run"));
  const running = (await api("/api/matches")).find((job) => ["queued", "running"].includes(job.status));
  if (running) await follow(running.id);
} catch (error) {
  say(errorText(error));
}

async function load(runId = null) {
  say(null);
  try {
    data = await api(runId ? `/api/results/${encodeURIComponent(runId)}` : "/api/results");
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) throw error;
    if (runId) return load(null); // an old link to a match that is gone: the newest
    return renderEmpty();
  }
  render();
}

// -- nothing to show yet ----------------------------------------------------------

async function renderEmpty() {
  data = null;
  let hasCv = true;
  try {
    await api("/api/cvs/active");
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) throw error;
    hasCv = false;
  }
  byId("summary").textContent = "";
  for (const id of ["filters", "pushed", "run"]) show(byId(id), false);
  byId("results").replaceChildren();
  const start = byId("start");
  start.textContent = t("action.firstMatch");
  show(start, hasCv);
  byId("empty").replaceChildren(
    h("p", {}, t(hasCv ? "results.noRun" : "results.noCv")),
    !hasCv && h("a", { class: "button button-primary", href: "/cv" }, t("results.toCv")),
  );
  show(byId("empty"));
}

// -- one match --------------------------------------------------------------------

function render() {
  show(byId("empty"), false);
  byId("summary").textContent = t("results.summary", {
    when: formatDate(data.at, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
    count: formatNumber(data.corpus_size),
    read: formatNumber(data.matches.length),
  });
  const picker = byId("run");
  picker.replaceChildren(...data.runs.map((run) => h("option", { value: run.id },
    formatDate(run.at, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }))));
  picker.value = data.id;
  show(picker, data.runs.length > 1);
  const start = byId("start");
  start.textContent = t("action.startMatch");
  show(start);
  // The address names an older match, so a reload or a shared tab shows it again.
  const newest = data.runs[0]?.id === data.id;
  window.history.replaceState(null, "", newest ? "/matches" : `/matches?run=${encodeURIComponent(data.id)}`);
  renderFilters();
  renderList();
  renderPushed();
}

function renderFilters() {
  const count = {
    all: data.matches.length,
    strong: data.strong,
    possible: data.possible,
    weak: data.weak,
    unmarked: data.matches.filter((one) => !one.mark).length,
  };
  byId("filters").replaceChildren(...FILTERS.map((name) => {
    const button = h("button", {
      type: "button", class: "filter", "aria-pressed": String(name === filter),
    }, t(`results.filter.${name}`), h("span", { class: "filter-count" }, formatNumber(count[name])));
    button.addEventListener("click", () => {
      filter = name;
      renderFilters();
      renderList();
    });
    return button;
  }));
  show(byId("filters"), data.matches.length > 0);
}

function renderList() {
  const shown = data.matches.filter((one) =>
    filter === "all" ? true : filter === "unmarked" ? !one.mark : one.verdict === filter);
  byId("results").replaceChildren(...(shown.length
    ? shown.map(matchCard)
    : [h("div", { class: "empty" }, t(data.matches.length ? "results.noneHere" : "results.nothingRead"))]));
}

function renderPushed() {
  byId("pushed-list").replaceChildren(...data.pushed_out.map(pushedCard));
  show(byId("pushed"), data.pushed_out.length > 0);
}

// -- a vacancy ----------------------------------------------------------------------

function matchCard(m) {
  const place = position(m);
  return h("article", { class: "match match-full" },
    h("div", { class: "match-body" },
      h("div", { class: "match-head" },
        h("h3", {}, m.title),
        h("span", { class: `badge badge-${m.verdict}` }, `${t(`verdict.${m.verdict}`)} · ${m.fit}`),
      ),
      h("div", { class: "match-meta" }, meta(m).join(" · ")),
      place && h("div", { class: "match-rank" }, place),
      m.summary && h("p", { class: "match-summary" }, m.summary),
      m.claims.length > 0 && h("div", { class: "match-block" },
        h("h4", {}, t("results.evidence")),
        h("ul", { class: "evidence" }, ...m.claims.map((claim) => h("li", {},
          h("span", { class: "requirement" }, claim.requirement),
          h("q", {}, claim.quote),
        ))),
      ),
      m.gaps.length > 0 && h("div", { class: "match-block" },
        h("h4", {}, t("results.gaps")),
        h("ul", { class: "gaps" }, ...m.gaps.map((gap) => h("li", {},
          h("span", { class: "requirement" }, gap.requirement),
          h("span", { class: `tag tag-${gapKind(gap)}` }, t(`results.gap.${gapKind(gap)}`)),
        ))),
      ),
      m.moved.length > 0 && movedLine(m.moved),
    ),
    markArea(m),
  );
}

function pushedCard(p) {
  return h("article", { class: "match match-full match-pushed" },
    h("div", { class: "match-body" },
      h("div", { class: "match-head" }, h("h3", {}, p.title)),
      h("div", { class: "match-meta" }, [p.company, p.city].filter(Boolean).join(" · ")),
      h("div", { class: "match-rank" }, t("results.pushedRank", { before: p.before, rank: formatNumber(p.rank) })),
      movedLine(p.moved),
    ),
    markArea(p),
  );
}

function meta(m) {
  const hours = m.hours_min && m.hours_max && m.hours_min !== m.hours_max
    ? `${m.hours_min}–${m.hours_max}`
    : m.hours_min || m.hours_max;
  return [
    m.company,
    m.city,
    m.work_mode && t(`workMode.${m.work_mode}`),
    hours && t("card.hours", { hours }),
  ].filter(Boolean);
}

function position(m) {
  if (!m.rank) return null;
  return m.before && m.before !== m.rank
    ? t("results.rankMoved", { rank: m.rank, before: m.before })
    : t("results.rank", { rank: m.rank });
}

function gapKind(gap) {
  return gap.knockout ? "knockout" : gap.required ? "required" : "plus";
}

// What a vacancy contradicts of your answers, in your language: the server
// sends the numbers and names apart (api/views.py Moved), and `text` -- its
// English line -- only for something it could not take apart.
function movedLine(list) {
  return h("div", { class: "match-moved" },
    h("strong", {}, t("results.moved")),
    list.map(movedText).join(" · "),
  );
}

function movedText(m) {
  const values = (prefix) => formatList(m.values.map((value) => named(`${prefix}.${value}`, value)));
  switch (m.field) {
    case "contract": return t("moved.contract", { list: values("contract") });
    case "work_mode": return t("moved.workMode", { list: values("workMode") });
    case "seniority": return t("moved.seniority", { list: values("level") });
    case "language": return t("moved.language", { list: values("languageName") });
    case "employer": return t("moved.employer", { name: m.values[0] });
    case "hours":
      if (m.low !== null) return t("card.hours", { hours: m.low === m.high ? m.low : `${m.low}–${m.high}` });
      break;
    case "salary":
      if (m.low !== null) return t("moved.salary", { amount: formatMoney(m.low, "EUR") });
      break;
    case "distance":
      if (m.low !== null) return t("moved.distance", { km: m.low, place: m.place });
      break;
  }
  return m.text;
}

function named(key, fallback) {
  const words = t(key);
  return words === key ? fallback : words;
}

// -- marking, with a reason ---------------------------------------------------------

function markArea(item) {
  const box = h("div", { class: "mark-area" });
  const link = /^https?:\/\//i.test(item.url || "") ? item.url : null;

  const draw = (editing = false, chosen = null) => {
    const view = link && h("a", {
      class: "button button-outline-blue", href: link, target: "_blank", rel: "noopener noreferrer",
    }, t("card.view"));
    if (item.mark && !editing) {
      const change = h("button", { type: "button", class: "link-button" }, t("mark.change"));
      change.addEventListener("click", () => draw(true, item.mark.call));
      return box.replaceChildren(view || "", h("div", { class: `your-mark your-mark-${item.mark.call}` },
        h("div", {},
          h("strong", {}, t(`call.${item.mark.call}`)),
          h("span", { class: "muted" }, ` · ${formatDate(item.mark.at, { day: "numeric", month: "short" })}`),
        ),
        h("q", {}, item.mark.reason),
        change,
      ));
    }
    const buttons = CALLS.map((call) => {
      const button = h("button", {
        type: "button", class: `call call-${call}`, "aria-pressed": String(call === chosen),
      }, t(`call.${call}`));
      button.addEventListener("click", () => draw(true, call));
      return button;
    });
    const parts = [view || "", h("div", { class: "calls", role: "group", "aria-label": t("mark.label") }, ...buttons)];
    if (chosen) parts.push(reasonForm(item, chosen, () => draw(false)));
    box.replaceChildren(...parts);
    box.querySelector("textarea")?.focus();
  };

  draw();
  return box;
}

function reasonForm(item, call, done) {
  const id = `reason-${Math.random().toString(36).slice(2)}`;
  const reason = h("textarea", {
    id, class: "input reason", rows: 3, maxlength: REASON_LIMIT,
    "aria-describedby": `${id}-hint`,
  }, item.mark?.call === call ? item.mark.reason : "");
  const problem = h("span", { class: "field-error", role: "alert", hidden: true });
  const save = h("button", { type: "submit", class: "button button-primary" }, t("mark.save"));
  const cancel = h("button", { type: "button", class: "button button-outline" }, t("mark.cancel"));
  const form = h("form", { class: "reason-form", novalidate: true },
    h("label", { for: id }, t("mark.why")),
    reason,
    h("span", { class: "field-hint", id: `${id}-hint` }, t("mark.hint")),
    problem,
    h("div", { class: "reason-actions" }, cancel, save),
  );
  cancel.addEventListener("click", done);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = reason.value.trim();
    problem.hidden = Boolean(text);
    problem.textContent = text ? "" : t("mark.needReason");
    if (!text) return reason.focus();
    save.disabled = true;
    try {
      item.mark = await api("/api/marks", {
        method: "POST", json: { run: data.id, key: item.key, call, reason: text },
      });
      renderFilters();
      done();
    } catch (error) {
      save.disabled = false;
      problem.hidden = false;
      problem.textContent = error instanceof ApiError && error.status === 422
        ? t("mark.needReason")
        : errorText(error);
    }
  });
  return form;
}

// -- a new match ----------------------------------------------------------------------

async function startMatch() {
  say(null);
  byId("start").disabled = true;
  try {
    const job = await api("/api/matches", { method: "POST", json: { top: 10 } });
    await follow(job.id);
  } catch (error) {
    byId("start").disabled = false;
    say(errorText(error));
  }
}

async function follow(jobId) {
  byId("start").disabled = true;
  try {
    const job = await followJob(jobId);
    if (job?.status === "failed") say(t("progress.failed", { reason: job.error }));
    else {
      filter = "all";
      await load(null);
    }
  } catch (error) {
    say(errorText(error));
  } finally {
    byId("start").disabled = false;
  }
}

// -- messages -------------------------------------------------------------------------

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
