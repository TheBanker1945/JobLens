// The owner's admin page (7.10.1): every vacancy source and what it holds,
// what each nightly run did, the sites that are refusing JobLens, the nightly
// switch (7.8.5) and invites (7.9.1), both moved here from settings. And
// (7.10.2) starting a run now, one source only, or stopping the one going.
//
// The numbers come from the nightly job itself: at the end of every run it
// counts the sources from its files and stores that with the run
// (sources/overview.py), because this server cannot read the job's bucket.
// So they are as of the last run, and the page says when that was.

import { api, ApiError } from "./api.js";
import { byId, fill, h, show } from "./dom.js";
import { ENDONYMS, icon, setUpFrame } from "./frame.js";
import { formatDate, formatNumber, loadLanguage, plural, t, translate } from "./i18n.js";

// Brand names stay as they are in every language; the two that are not brands
// are words in the dictionaries.
const NAMES = {
  recruitee: "Recruitee", greenhouse: "Greenhouse", smartrecruiters: "SmartRecruiters",
  workday: "Workday", jobdataapi: "jobdataapi", eures: "EURES", indeed: "Indeed",
  linkedin: "LinkedIn",
};
const WORDS = { careersite: "admin.careersites", overheid: "admin.overheid" };
// Up here, not beside the code that uses them: the page runs from the top as
// soon as it loads, and a constant further down does not exist yet by then.
const DAY = { day: "numeric", month: "short" };
const WHEN = { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
const TIME = { hour: "2-digit", minute: "2-digit" };
const DATE = { day: "numeric", month: "long" };
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const CHEVRON = "M9 6l6 6-6 6";
const POLL = 30_000; // while a run is going, look again this often
const POLL_STARTING = 10_000; // and while one is starting, more often

let me = null;
let wasRunning = false;
let canStart = false; // this server can start and stop the job (7.10.2)
let busy = false; // a run is going or starting: one at a time
let polling = null;
let shownGoing = null; // the run on screen as going
let stopping = null; // the execution Stop was pressed for

await loadLanguage();
translate();

try {
  me = await api("/api/me");
  if (me.role !== "owner") window.location.replace("/");
  setUpFrame("/admin", me, { onError: (e) => say(errorText(e)) });
  byId("run-now").addEventListener("click", () => startRun({}));
  byId("run-index").addEventListener("click", () => startRun({ fetch: false }));
  byId("run-stop").addEventListener("click", stopRun);
  // The runs first: whether a source gets its "Fetch only" button depends on
  // whether this server can start the job at all.
  await showRuns();
  await Promise.all([showSources(), showNightly(), showInvites()]);
} catch (error) {
  say(errorText(error));
}

// -- the sources ------------------------------------------------------------------

async function showSources() {
  const seen = await api("/api/admin/sources");
  show(byId("sources-none"), !seen);
  show(byId("sources-wrap"), Boolean(seen));
  if (!seen) return;
  const { sources, refusing } = seen.overview;
  byId("sources-as-of").textContent = t("admin.asOf", { date: formatDate(seen.as_of, WHEN) });

  const table = byId("sources");
  for (const old of table.querySelectorAll("tbody, tfoot")) old.remove();
  table.append(...sources.flatMap(sourceGroup), totalsRow(sources));

  const sum = (name) => sources.reduce((n, one) => n + one[name], 0);
  byId("total-joblens").textContent = formatNumber(sum("in_joblens"));
  byId("total-open").textContent = formatNumber(sum("open"));
  byId("total-stored").textContent = formatNumber(sum("stored"));
  show(byId("totals"));

  fill(byId("refusing"), ...(refusing.length
    ? refusing.map((one) => h("li", { class: "refusal" },
        h("strong", {}, one.site),
        h("span", { class: "muted" }, t("admin.refusingUntil", { date: formatDate(one.until, WHEN) })),
        h("span", { class: "muted" }, `${plural("admin.strikes", one.strikes)} · ${one.reason}`),
      ))
    : [h("li", { class: "muted" }, t("admin.refusingNone"))]));
}

function nameOf(source) {
  return WORDS[source] ? t(WORDS[source]) : NAMES[source] || source;
}

// Two row groups per source: its own row, with a button, and its boards or
// searches, shown when the button is pressed.
function sourceGroup(one) {
  const id = `rows-${one.source}`;
  const fetched = one.fetched_at ? formatDate(one.fetched_at, DAY) : t("admin.never");
  const toggle = h("button", {
    type: "button", class: "disclosure", "aria-expanded": "false", "aria-controls": id,
  },
  icon(CHEVRON, { size: 18 }),
  h("span", { class: "disclosure-text" },
    h("strong", {}, nameOf(one.source)),
    h("span", { class: "muted" }, one.per_board
      ? plural("admin.boards", one.rows.filter((row) => row.name !== null).length)
      : plural("admin.searches", one.rows.length),
    h("span", { class: "phone-only" }, ` · ${fetched}`)),
    badges(one),
  ));
  const rows = h("tbody", { id, hidden: true }, ...detailRows(one));
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    show(rows, open);
  });
  const run = one.run;
  const head = h("tbody", {},
    h("tr", { class: "source-row" },
      h("th", { scope: "row" }, toggle),
      h("td", { class: "muted-cell c-wide" }, fetched),
      number(run?.listed, "c-wide"), number(run?.new),
      number(one.stored), number(one.open, "c-wide"), number(one.in_joblens, "strong-num"),
    ));
  return [head, rows];
}

function badges(one) {
  const found = [];
  if (!one.enabled) found.push(h("span", { class: "badge badge-weak" }, t("admin.off")));
  if (one.run?.broken) {
    found.push(h("span", { class: "badge badge-problem" }, plural("admin.broken", one.run.broken)));
  } else if (one.problems.length) {
    found.push(h("span", { class: "badge badge-problem" }, t("admin.needsLook")));
  }
  return found.length ? h("span", { class: "badges" }, ...found) : null;
}

function detailRows(one) {
  const rows = [];
  if (canStart && one.enabled) {
    const button = h("button", { type: "button", class: "button button-outline fetch-one" },
      t("admin.runOne", { source: nameOf(one.source) }));
    button.disabled = busy;
    button.addEventListener("click", () => startRun({ source: one.source }));
    rows.push(h("tr", { class: "board-note" }, h("td", { colspan: 7 }, button)));
  }
  if (one.problems.length) {
    rows.push(h("tr", { class: "board-note" },
      h("td", { colspan: 7 }, h("ul", { class: "problems" },
        ...one.problems.map((line) => h("li", {}, line))))));
  }
  if (!one.per_board && one.rows.length) {
    rows.push(h("tr", { class: "board-note" }, h("td", { colspan: 7 }, t("admin.searchNote"))));
  }
  for (const row of one.rows) {
    rows.push(h("tr", { class: "board-row" },
      h("th", { scope: "row" }, row.name ?? t("admin.noBoard"), statusOf(row.run)),
      h("td", { class: "muted-cell c-wide" }, row.run ? "" : "–"),
      number(row.run?.listed, "c-wide"), number(row.run?.new),
      number(row.stored), number(row.open, "c-wide"), number(row.in_joblens),
    ));
  }
  return rows;
}

// What the last fetch said about one board or search, under its name: nothing
// when it went well, or it was not asked (its "Last fetch" says so), the
// reason when it did not.
function statusOf(run) {
  if (!run || run.status === "ok") return null;
  const key = `admin.status.${run.status}`;
  const word = t(key) === key ? run.status : t(key);
  return h("span", { class: "badges board-status" },
    h("span", { class: run.status === "empty" ? "badge badge-weak" : "badge badge-problem" }, word),
    run.detail && h("span", {}, run.detail),
  );
}

function totalsRow(sources) {
  const sum = (pick) => sources.reduce((n, one) => n + (pick(one) ?? 0), 0);
  return h("tfoot", {}, h("tr", {},
    h("th", { scope: "row" }, t("admin.total")),
    h("td", { class: "c-wide" }),
    number(sum((one) => one.run?.listed), "c-wide"), number(sum((one) => one.run?.new)),
    number(sum((one) => one.stored)), number(sum((one) => one.open), "c-wide"),
    number(sum((one) => one.in_joblens), "strong-num"),
  ));
}

// A number cell. "c-wide" columns are left out on a phone (app.css).
function number(n, extra = "") {
  const cell = h("td", { class: `num ${extra}`.trim() });
  cell.textContent = n === null || n === undefined ? "–" : formatNumber(n);
  return cell;
}

// -- the nightly runs ------------------------------------------------------------

async function showRuns() {
  await drawRuns(await api("/api/admin/runs"));
}

async function drawRuns({ running, runs, can_start: able, starting }) {
  canStart = able;
  busy = running || Boolean(starting);
  const fetched = runs.find((run) => run.new_vacancies !== null);
  byId("total-new").textContent = fetched ? formatNumber(fetched.new_vacancies) : "–";
  const going = running ? runs.find((run) => run.finished_at === null) : null;
  shownGoing = going;
  if (!going) stopping = null;
  // Cloud Run takes a few seconds to end a cancelled run: until it has, say
  // so, rather than offer Stop again.
  const ending = Boolean(going) && going.execution === stopping;
  byId("runs-state").textContent = ending
    ? t("admin.runStopping")
    : going ? t("admin.running", { time: formatDate(going.started_at, TIME) })
      : starting ? t("admin.runStarting")
        : runs.length ? t("admin.runsHint") : t("admin.runsNone");
  fill(byId("runs"), ...runs.map((run) => runItem(run, run === going)));

  show(byId("run-controls"), able);
  show(byId("run-local"), !able);
  byId("run-now").disabled = busy;
  byId("run-index").disabled = busy;
  for (const button of document.querySelectorAll(".fetch-one")) button.disabled = busy;
  const stop = byId("run-stop");
  show(stop, Boolean(going?.execution) && able && !ending);
  stop.disabled = false;

  // While a run is starting or going, look again now and then; when it has
  // just ended, its numbers are new too.
  if (wasRunning && !running) await showSources();
  wasRunning = running;
  clearTimeout(polling);
  if (busy) {
    polling = setTimeout(() => showRuns().catch((e) => say(errorText(e))),
      (starting && !running) || ending ? POLL_STARTING : POLL);
  }
}

// Start the job now (7.10.2): everything, only index and publish, or one
// source. Cloud Run takes about a minute to begin; until the run writes its
// own row, the page says it is starting.
async function startRun(body) {
  say(null);
  busy = true;
  for (const button of document.querySelectorAll("#run-now, #run-index, .fetch-one")) {
    button.disabled = true;
  }
  try {
    await drawRuns(await api("/api/admin/runs", { method: "POST", json: body }));
    byId("runs-title").scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (error) {
    say(errorText(error));
    await showRuns().catch(() => {});
  }
}

async function stopRun() {
  if (!window.confirm(t("admin.runStopSure"))) return;
  say(null);
  byId("run-stop").disabled = true;
  stopping = shownGoing?.execution ?? null;
  try {
    await drawRuns(await api("/api/admin/runs/stop", { method: "POST" }));
  } catch (error) {
    stopping = null;
    say(errorText(error));
    byId("run-stop").disabled = false;
  }
}

function runItem(run, going) {
  const facts = [];
  const steps = Object.entries(run.steps)
    .map(([name, ok]) => `${t(`admin.step.${name}`)} ${ok ? "✓" : "✗"}`);
  if (steps.length) facts.push(steps.join(" · "));
  if (run.finished_at) {
    const minutes = Math.max(1, Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 60_000));
    facts.push(t("admin.took", { minutes: formatNumber(minutes) }));
  }
  if (run.new_vacancies !== null) facts.push(plural("admin.new", run.new_vacancies));
  if (run.in_joblens !== null) facts.push(t("admin.inJobLens", { count: formatNumber(run.in_joblens) }));

  const state = going
    ? h("span", { class: "badge badge-strong" }, t("admin.going"))
    : run.finished_at === null
      ? h("span", { class: "badge badge-problem" }, t("admin.cutOff"))
      : Object.values(run.steps).every(Boolean)
        ? null
        : h("span", { class: "badge badge-problem" }, t("admin.needsLook"));
  return h("li", { class: "run" },
    h("div", { class: "run-head" },
      h("strong", {}, formatDate(run.started_at, WHEN)),
      h("span", { class: "badge badge-weak" }, t(`admin.trigger.${run.trigger}`)),
      !run.fetched && h("span", { class: "badge badge-weak" }, t("admin.noFetch")),
      run.source && h("span", { class: "badge badge-weak" },
        t("admin.onlySource", { source: nameOf(run.source) })),
      state,
    ),
    facts.length ? h("p", { class: "run-facts" }, facts.join(" · ")) : null,
    run.problems.length
      ? h("details", {},
          h("summary", {}, plural("admin.problems", run.problems.length)),
          h("ul", { class: "problems" }, ...run.problems.map((line) => h("li", {}, line))))
      : null,
  );
}

// -- the nightly switch (7.8.5) ----------------------------------------------------

async function showNightly() {
  const box = byId("nightly");
  const draw = (state) => {
    box.checked = state.enabled;
    byId("nightly-state").textContent = state.published_at
      ? t("admin.nightlyLast", {
          date: formatDate(state.published_at, {
            day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
          }),
          count: formatNumber(state.vacancies),
        })
      : t("admin.nightlyNever");
  };
  draw(await api("/api/admin/nightly"));
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      draw(await api("/api/admin/nightly", { method: "PUT", json: { enabled: box.checked } }));
    } catch (error) {
      box.checked = !box.checked;
      say(errorText(error));
    } finally {
      box.disabled = false;
    }
  });
}

// -- inviting people (7.9.1) -------------------------------------------------------
//
// The server answers with the link's path ("/login#..."): this page knows its
// own address, the server behind Cloud Run's proxy is not sure of it. The link
// is shown once -- only its hash is kept -- so it stays on screen until the
// next one is made.

async function showInvites() {
  const offered = (document.querySelector('meta[name="joblens-languages"]')?.content || "en").split(",");
  fill(byId("invite-language"),
    h("option", { value: "" }, t("admin.inviteBrowser")),
    ...offered.map((code) => h("option", { value: code }, ENDONYMS[code] || code)),
  );
  byId("invite").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = byId("invite-email").value.trim();
    if (!EMAIL.test(email)) {
      inviteError(t("admin.inviteBadEmail"));
      byId("invite-email").focus();
      return;
    }
    const sent = await invite({
      email,
      display_name: byId("invite-name").value.trim() || null,
      locale: byId("invite-language").value || null,
    }, byId("invite-make"));
    if (sent) {
      byId("invite-email").value = "";
      byId("invite-name").value = "";
      byId("invite-language").value = "";
    }
  });
  byId("invite-copy").addEventListener("click", copyLink);
  drawPeople(await api("/api/admin/people"));
}

async function invite(body, button) {
  inviteError(null);
  button.disabled = true;
  try {
    const made = await api("/api/admin/invites", { method: "POST", json: body });
    const email = made.person.email;
    byId("invite-said").textContent = made.new_account
      ? t("admin.inviteNew", { email })
      : t("admin.inviteAgain", { email });
    byId("invite-url").value = window.location.origin + made.link;
    byId("invite-copy").textContent = t("admin.copy");
    show(byId("invite-link"));
    drawPeople(await api("/api/admin/people"));
    byId("invite-url").focus();
    byId("invite-url").select();
    return true;
  } catch (error) {
    inviteError(error instanceof ApiError && error.status === 422
      ? t("admin.inviteBadEmail")
      : errorText(error));
    return false;
  } finally {
    button.disabled = false;
  }
}

async function copyLink() {
  const box = byId("invite-url");
  try {
    await navigator.clipboard.writeText(box.value);
    byId("invite-copy").textContent = t("admin.copied");
  } catch {
    box.focus();
    box.select();
    byId("invite-copy").textContent = t("admin.copyManual");
  }
}

function drawPeople(people) {
  fill(byId("people"), ...people.map((person) => {
    const status = person.signed_in_at
      ? h("span", { class: "badge badge-strong" },
        t("admin.peopleIn", { date: formatDate(person.signed_in_at, DATE) }))
      : person.link_until
        ? h("span", { class: "badge badge-weak" },
          t("admin.peopleWaiting", { date: formatDate(person.link_until, DATE) }))
        : h("span", { class: "badge badge-possible" }, t("admin.peopleNeedsLink"));
    const button = h("button", { type: "button", class: "button button-outline" },
      t("admin.peopleNewLink"));
    button.addEventListener("click", () => invite({ email: person.email }, button));
    return h("li", { class: "history-row person" },
      h("span", { class: "person-who" },
        h("strong", {}, person.display_name || person.email),
        person.display_name && h("span", { class: "muted" }, person.email),
        person.id === me.id && h("span", { class: "muted" }, t("admin.peopleYou")),
      ),
      h("span", { class: "person-end" }, status, button),
    );
  }));
}

function inviteError(text) {
  byId("invite-error").textContent = text || "";
  show(byId("invite-error"), Boolean(text));
}

// -- messages -----------------------------------------------------------------------

function say(text) {
  const notice = byId("notice");
  notice.textContent = text || "";
  show(notice, Boolean(text));
  if (text) notice.scrollIntoView({ block: "center" });
}

function errorText(error) {
  if (error instanceof ApiError && error.status === 0) return t("error.offline");
  if (error?.message) return error.message;
  return t("error.generic");
}
