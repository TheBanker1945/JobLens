// The dashboard (7.7 step 1): everything on it comes from one request,
// GET /api/dashboard, and every piece of text goes in through h() -- as text,
// never as HTML (dom.js says why).

import { api, ApiError } from "./api.js";
import { byId, h, show } from "./dom.js";
import {
  formatDate,
  formatList,
  formatMoney,
  formatNumber,
  loadLanguage,
  plural,
  t,
  translate,
} from "./i18n.js";

// Each language's own name, for the picker: someone who cannot read the
// current language can still find theirs.
const ENDONYMS = { en: "English", nl: "Nederlands", de: "Deutsch", fr: "Français", es: "Español" };
const POLL_MS = 1500;

let data = null;
let following = null;

await loadLanguage();
translate();
setUpLanguagePicker();
setUpAccountMenu();
setUpCvForm();
byId("start-match").addEventListener("click", startMatch);
await refresh();

async function refresh() {
  try {
    data = await api("/api/dashboard");
  } catch (error) {
    return say(errorText(error));
  }
  renderAccount();
  renderHero();
  renderStats();
  renderMatches();
  renderCv();
  renderWishes();
  renderAi();
  if (data.open_match) follow(data.open_match.id);
}

// -- the welcome band -------------------------------------------------------

function renderHero() {
  const { user, cv, latest } = data;
  const name = user.display_name || (user.email || "").split("@")[0];
  const hour = new Date().getHours();
  const part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  byId("greeting").textContent = t(`greeting.${part}`, { name });
  const today = formatDate(Date.now(), { weekday: "long", day: "numeric", month: "long" });
  byId("today").textContent = today.charAt(0).toUpperCase() + today.slice(1);

  const button = byId("start-match");
  if (!cv) {
    byId("hero-message").textContent = t("hero.noCv");
    show(button, false);
  } else if (!latest) {
    byId("hero-message").textContent = t("hero.noRun");
    button.textContent = t("action.firstMatch");
    show(button);
  } else {
    byId("hero-message").textContent = `${t("hero.compared", {
      count: formatNumber(latest.corpus_size),
    })} ${plural("hero.strong", latest.strong)}`;
    button.textContent = t("action.startMatch");
    show(button);
  }
  button.disabled = Boolean(data.open_match);
}

function renderStats() {
  const { latest } = data;
  show(byId("stats"), Boolean(latest));
  if (!latest) return;
  byId("stat-strong").textContent = formatNumber(latest.strong);
  byId("stat-possible").textContent = formatNumber(latest.possible);
  byId("stat-weak").textContent = formatNumber(latest.weak);
  byId("stat-moved").textContent = formatNumber(latest.moved);
}

// -- the matches ------------------------------------------------------------

function renderMatches() {
  const { latest } = data;
  const list = byId("matches");
  byId("matches-subtitle").textContent = latest
    ? t("matches.subtitle", {
        when: formatDate(latest.at, {
          weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
        }),
      })
    : "";
  if (!latest) return list.replaceChildren(h("div", { class: "empty" }, t("matches.none")));
  if (!latest.recommended.length) {
    return list.replaceChildren(h("div", { class: "empty" }, t("matches.nothingClear")));
  }
  list.replaceChildren(...latest.recommended.map(matchCard));
}

function matchCard(card) {
  const hours = card.hours_min && card.hours_max && card.hours_min !== card.hours_max
    ? `${card.hours_min}–${card.hours_max}`
    : card.hours_min || card.hours_max;
  const meta = [
    card.company,
    card.city,
    card.work_mode && t(`workMode.${card.work_mode}`),
    hours && t("card.hours", { hours }),
  ].filter(Boolean);
  // Only ordinary web links: a scraped advert's "link" could otherwise be
  // javascript:..., which a click would run.
  const link = /^https?:\/\//i.test(card.url || "") ? card.url : null;
  return h("article", { class: "match" },
    h("div", { class: "match-body" },
      h("div", { class: "match-head" },
        h("h3", {}, card.title),
        h("span", { class: `badge badge-${card.verdict}` }, `${t(`verdict.${card.verdict}`)} · ${card.fit}`),
      ),
      h("div", { class: "match-meta" }, meta.join(" · ")),
      card.quote && h("div", { class: "match-quote" }, h("strong", {}, t("card.why")), `“${card.quote}”`),
      card.gap && h("div", { class: "match-gap" },
        h("strong", {}, t("card.missing")),
        `${card.gap} (${t(card.gap_required ? "card.required" : "card.plus")})`,
      ),
    ),
    link && h("div", { class: "match-actions" },
      h("a", {
        class: card.verdict === "strong" ? "button button-primary" : "button button-outline-blue",
        href: link, target: "_blank", rel: "noopener noreferrer",
      }, t("card.view")),
    ),
  );
}

// -- starting a match, and following it --------------------------------------

async function startMatch() {
  say(null);
  byId("start-match").disabled = true;
  try {
    const job = await api("/api/matches", { method: "POST", json: { top: 10 } });
    follow(job.id);
  } catch (error) {
    byId("start-match").disabled = false;
    say(errorText(error));
  }
}

async function follow(jobId) {
  if (following === jobId) return;
  following = jobId;
  byId("start-match").disabled = true;
  show(byId("progress"));
  while (following === jobId) {
    let job;
    try {
      job = await api(`/api/matches/${encodeURIComponent(jobId)}`);
    } catch (error) {
      following = null;
      show(byId("progress"), false);
      return say(errorText(error));
    }
    if (job.status === "done" || job.status === "failed") {
      following = null;
      show(byId("progress"), false);
      if (job.status === "failed") say(t("progress.failed", { reason: job.error }));
      return refresh();
    }
    showProgress(job);
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

function showProgress(job) {
  let text = t("progress.queued");
  let share = 4;
  if (job.stage === "reading") [text, share] = [t("progress.reading"), 10];
  if (job.stage === "ranking") [text, share] = [t("progress.ranking"), 22];
  if (job.stage === "judging" && job.total) {
    text = t("progress.judging", { done: Math.min(job.done + 1, job.total), total: job.total });
    share = 30 + (70 * job.done) / job.total;
  }
  byId("progress-text").textContent = text;
  byId("progress-fill").style.width = `${share}%`;
}

// -- the side: CV, wishes, AI ---------------------------------------------------

function renderCv() {
  const { cv } = data;
  const current = byId("cv-current");
  if (!cv) {
    current.replaceChildren(h("p", { class: "muted" }, t("cv.none")));
    show(byId("cv-removed"), false);
    show(byId("cv-replace"), false);
    show(byId("cv-form"));
    byId("cv-submit").textContent = t("cv.upload");
    return;
  }
  current.replaceChildren(
    h("div", { class: "file-row" },
      h("span", { class: "file-icon", "aria-hidden": "true" }, fileIcon()),
      h("div", {},
        h("div", { class: "file-name" }, cv.filename),
        h("div", { class: "muted" }, t("cv.uploaded", {
          date: formatDate(cv.uploaded_at, { day: "numeric", month: "long" }),
        })),
      ),
    ),
  );
  const kinds = Object.keys(cv.removed || {}).map((kind) => t(`removed.${kind}`));
  byId("cv-removed").textContent = kinds.length ? t("cv.removed", { list: formatList(kinds) }) : "";
  show(byId("cv-removed"), kinds.length > 0);
  show(byId("cv-replace"), byId("cv-form").hidden);
  byId("cv-submit").textContent = t("cv.upload");
}

function setUpCvForm() {
  const form = byId("cv-form");
  byId("cv-replace").addEventListener("click", () => {
    show(form);
    show(byId("cv-replace"), false);
    byId("cv-file").focus();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    say(null);
    const file = byId("cv-file").files[0];
    if (!file) return say(t("cv.chooseFile"));
    const upload = new FormData();
    upload.append("file", file);
    upload.append("strip_name", byId("cv-name").value.trim());
    const submit = byId("cv-submit");
    submit.disabled = true;
    submit.textContent = t("cv.uploading");
    try {
      await api("/api/cvs", { method: "POST", form: upload });
      form.reset();
      show(form, false);
      await refresh();
    } catch (error) {
      say(errorText(error));
    } finally {
      submit.disabled = false;
      submit.textContent = t("cv.upload");
    }
  });
}

function renderWishes() {
  const p = data.preferences;
  const chips = [
    ...p.contract_types.map((kind) => t(`contract.${kind}`)),
    ...p.work_modes.map((mode) => t(`workMode.${mode}`)),
    (p.hours_min !== null || p.hours_max !== null) &&
      t("wishes.hours", { min: p.hours_min ?? 0, max: p.hours_max ?? "…" }),
    p.home && p.max_distance_km && t("wishes.distance", { km: p.max_distance_km, home: p.home }),
    p.salary_min !== null && t("wishes.salary", { amount: formatMoney(p.salary_min, "EUR") }),
    ...p.seniority.map((level) => t(`level.${level}`)),
    p.languages.length && formatList(p.languages.map(languageName)),
    p.stretch_years !== null && p.stretch_years > 0 &&
      (p.stretch_years >= 10 ? t("wishes.anyYears") : t("wishes.moreYears", { n: p.stretch_years })),
    p.stretch_degree === true && t("wishes.higherDegree"),
    (p.avoid_employers.length || p.avoid_sectors.length) &&
      t("wishes.avoid", { list: formatList([...p.avoid_employers, ...p.avoid_sectors]) }),
  ].filter(Boolean);
  byId("wishes").replaceChildren(
    ...(chips.length
      ? chips.map((text) => h("span", { class: "chip" }, text))
      : [h("span", { class: "muted" }, t("wishes.none"))]),
  );
}

function languageName(name) {
  const key = `languageName.${name}`;
  const translated = t(key);
  return translated === key ? name : translated;
}

function renderAi() {
  const { ai, usage } = data;
  byId("ai-model").textContent = ai.using === "own"
    ? t("ai.own", { provider: ai.provider, model: ai.model })
    : t("ai.joblens", { model: ai.model });
  const limited = usage.allowance_usd !== null;
  show(byId("ai-bar"), limited);
  if (limited) {
    const share = Math.min(100, (100 * usage.joblens_usd) / usage.allowance_usd);
    byId("ai-fill").style.width = `${share}%`;
    byId("ai-used").textContent = t("ai.used", {
      spent: formatMoney(usage.joblens_usd, "USD"),
      allowance: formatMoney(usage.allowance_usd, "USD"),
    });
  } else {
    byId("ai-used").textContent = ai.using === "own"
      ? t("ai.ownSpent", { spent: formatMoney(usage.own_usd, "USD") })
      : t("ai.unlimited");
  }
}

// -- header: language and account ---------------------------------------------

function setUpLanguagePicker() {
  const picker = byId("language");
  const offered = (document.querySelector('meta[name="joblens-languages"]')?.content || "en")
    .split(",");
  picker.replaceChildren(
    ...offered.map((code) => h("option", { value: code }, ENDONYMS[code] || code)),
  );
  picker.value = document.documentElement.lang;
  picker.addEventListener("change", async () => {
    try {
      await api("/api/me", { method: "PATCH", json: { locale: picker.value } });
      window.location.reload();
    } catch (error) {
      say(errorText(error));
    }
  });
}

function renderAccount() {
  const { user } = data;
  const name = user.display_name || user.email || "";
  byId("account-button").textContent = (name.trim()[0] || "?").toUpperCase();
  byId("account-name").textContent = name;
  byId("account-email").textContent = [user.email, t(`account.${user.role}`)]
    .filter(Boolean)
    .join(" · ");
}

function setUpAccountMenu() {
  const button = byId("account-button");
  const menu = byId("account-menu");
  const toggle = (open) => {
    show(menu, open);
    button.setAttribute("aria-expanded", String(open));
  };
  button.addEventListener("click", () => toggle(menu.hidden));
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !event.target.closest(".account")) toggle(false);
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      toggle(false);
      button.focus();
    }
  });
  byId("sign-out").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST" });
    } finally {
      window.location.assign("/login");
    }
  });
}

// -- messages ---------------------------------------------------------------

function say(text) {
  const notice = byId("notice");
  notice.textContent = text || "";
  show(notice, Boolean(text));
}

function errorText(error) {
  if (error instanceof ApiError && error.status === 0) return t("error.offline");
  if (error instanceof ApiError && error.message) return error.message;
  return t("error.generic");
}

function fileIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "#1d4ed8");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const d of ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5"]) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}
