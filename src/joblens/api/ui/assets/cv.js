// "My CV": the active CV as it is sent (redacted), what was taken out, what a
// model read from it, the versions before it, and a new upload.

import { api, ApiError } from "./api.js";
import { removedKinds, uploadForm } from "./cv-upload.js";
import { byId, h, show } from "./dom.js";
import { FILE_ICON, icon, setUpFrame } from "./frame.js";
import { formatDate, formatList, formatNumber, loadLanguage, t, translate } from "./i18n.js";

await loadLanguage();
translate();

try {
  const me = await api("/api/me");
  setUpFrame("/cv", me, { onError: (e) => say(errorText(e)) });
  byId("upload").append(uploadForm({ onDone: load, onError: (e) => say(errorText(e)) }));
  await load();
} catch (error) {
  say(errorText(error));
}

async function load() {
  say(null);
  const [active, all] = await Promise.all([activeCv(), api("/api/cvs")]);
  renderActive(active);
  renderHistory(all.filter((one) => !one.active));
}

async function activeCv() {
  try {
    return await api("/api/cvs/active");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

function renderActive(cv) {
  const box = byId("active");
  if (!cv) return box.replaceChildren(h("p", { class: "muted" }, t("cv.none")));
  const kinds = removedKinds(cv.removed);
  box.replaceChildren(
    h("div", { class: "file-row" },
      h("span", { class: "file-icon" }, icon(FILE_ICON, { size: 20, color: "#1d4ed8" })),
      h("div", {},
        h("div", { class: "file-name" }, cv.filename),
        h("div", { class: "muted" }, [
          t("cv.uploaded", { date: formatDate(cv.uploaded_at, { day: "numeric", month: "long", year: "numeric" }) }),
          t("cvpage.characters", { n: formatNumber(cv.characters) }),
        ].join(" · ")),
      ),
    ),
    kinds.length > 0 && h("p", { class: "card-note" }, t("cv.removed", { list: formatList(kinds) })),
    h("h3", { class: "subhead" }, t("cvpage.sent")),
    h("p", { class: "muted" }, t("cvpage.sentHint")),
    h("pre", { class: "sent", tabindex: "0" }, cv.text),
    cv.profile && profile(cv.profile),
    h("p", { class: "muted" }, t("cvpage.fileKept")),
  );
}

function profile(p) {
  const period = (job) => [job.start, job.current ? t("cvpage.now") : job.end].filter(Boolean).join(" – ");
  return h("div", { class: "profile" },
    h("h3", { class: "subhead" }, t("cvpage.read")),
    p.headline && h("p", { class: "profile-headline" }, p.headline),
    p.experience?.length > 0 && h("div", {},
      h("h4", {}, t("cvpage.experience")),
      h("ul", { class: "plain-list" }, ...p.experience.map((job) => h("li", {},
        h("strong", {}, job.title),
        job.company && ` · ${job.company}`,
        period(job) && h("span", { class: "muted" }, ` · ${period(job)}`),
      ))),
    ),
    p.education?.length > 0 && h("div", {},
      h("h4", {}, t("cvpage.education")),
      h("ul", { class: "plain-list" }, ...p.education.map((one) => h("li", {},
        h("strong", {}, one.programme),
        one.institution && ` · ${one.institution}`,
        one.end_year && h("span", { class: "muted" }, ` · ${one.end_year}`),
      ))),
    ),
    p.skills?.length > 0 && h("div", {},
      h("h4", {}, t("cvpage.skills")),
      h("div", { class: "chips" }, ...p.skills.map((skill) => h("span", { class: "chip" }, skill))),
    ),
  );
}

function renderHistory(older) {
  const box = byId("history");
  if (!older.length) return box.replaceChildren(h("p", { class: "muted" }, t("cvpage.noHistory")));
  box.replaceChildren(h("ul", { class: "plain-list history" }, ...older.map((cv) => {
    const button = h("button", { type: "button", class: "button button-outline" }, t("cvpage.makeActive"));
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/api/cvs/${encodeURIComponent(cv.id)}/activate`, { method: "POST" });
        await load();
      } catch (error) {
        button.disabled = false;
        say(errorText(error));
      }
    });
    return h("li", { class: "history-row" },
      h("div", {},
        h("div", { class: "file-name" }, cv.filename),
        h("div", { class: "muted" }, formatDate(cv.uploaded_at, { day: "numeric", month: "long", year: "numeric" })),
      ),
      button,
    );
  })));
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
