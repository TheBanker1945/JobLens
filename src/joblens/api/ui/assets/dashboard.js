// The dashboard: everything on it comes from one request, GET /api/dashboard,
// and every piece of text goes in through h() -- as text, never as HTML
// (dom.js says why).

import { api, ApiError } from "./api.js";
import { removedKinds, uploadForm } from "./cv-upload.js";
import { byId, h, show } from "./dom.js";
import { FILE_ICON, icon, setUpFrame } from "./frame.js";
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
import { followJob, isFollowing } from "./progress.js";

let data = null;
let framed = false;

await loadLanguage();
translate();
byId("start-match").addEventListener("click", startMatch);
byId("cv-upload").append(uploadForm({ onDone: afterUpload, onError: (e) => say(errorText(e)) }));
byId("cv-replace").addEventListener("click", () => {
  show(byId("cv-upload"));
  show(byId("cv-replace"), false);
});
await refresh();

async function refresh() {
  try {
    data = await api("/api/dashboard");
  } catch (error) {
    return say(errorText(error));
  }
  if (!framed) {
    setUpFrame("/", data.user, { onError: (e) => say(errorText(e)) });
    framed = true;
  }
  renderHero();
  renderStats();
  renderMatches();
  renderCv();
  renderWishes();
  renderAi();
  if (data.open_match && !isFollowing(data.open_match.id)) follow(data.open_match.id);
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
    await follow(job.id);
  } catch (error) {
    byId("start-match").disabled = false;
    say(errorText(error));
  }
}

async function follow(jobId) {
  byId("start-match").disabled = true;
  let job;
  try {
    job = await followJob(jobId);
  } catch (error) {
    return say(errorText(error));
  }
  if (job?.status === "failed") say(t("progress.failed", { reason: job.error }));
  await refresh();
}

// -- the side: CV, wishes, AI ---------------------------------------------------

function renderCv() {
  const { cv } = data;
  const current = byId("cv-current");
  if (!cv) {
    current.replaceChildren(h("p", { class: "muted" }, t("cv.none")));
    show(byId("cv-removed"), false);
    show(byId("cv-replace"), false);
    show(byId("cv-upload"));
    return;
  }
  current.replaceChildren(
    h("div", { class: "file-row" },
      h("span", { class: "file-icon" }, icon(FILE_ICON, { size: 20, color: "#1d4ed8" })),
      h("div", {},
        h("div", { class: "file-name" }, cv.filename),
        h("div", { class: "muted" }, t("cv.uploaded", {
          date: formatDate(cv.uploaded_at, { day: "numeric", month: "long" }),
        })),
      ),
    ),
  );
  const kinds = removedKinds(cv.removed);
  byId("cv-removed").textContent = kinds.length ? t("cv.removed", { list: formatList(kinds) }) : "";
  show(byId("cv-removed"), kinds.length > 0);
  show(byId("cv-replace"), byId("cv-upload").hidden);
}

async function afterUpload() {
  show(byId("cv-upload"), false);
  await refresh();
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

// -- messages ---------------------------------------------------------------

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
