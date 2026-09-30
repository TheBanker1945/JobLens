// Settings (7.7 step 4): your name and language, whose AI reads your CV and
// what it has cost, and your data -- to take with you, or to delete.
//
// Nothing here is new on the server: PATCH /api/me, the routes for an own key
// (7.6: /api/ai, /api/ai/providers, /api/usage), and export and delete (7.5).
// An own key goes in once and never comes back out: the page only ever sees
// its last four characters. The owner's nightly switch and invites moved to
// the admin page (7.10.1).

import { api, ApiError } from "./api.js";
import { byId, fill, h, show } from "./dom.js";
import { ENDONYMS, setUpFrame } from "./frame.js";
import { formatDate, formatMoney, loadLanguage, t, translate } from "./i18n.js";

// What JobLens has measured (llm/presets.py MEASURED), in the page's language.
const MEASURED = {
  "gemini/gemini-3.8-flash": "settings.measured.default",
  "ollama/qwen3:8b": "settings.measured.local",
};

let me = null;
let providers = [];

await loadLanguage();
translate();

try {
  me = await api("/api/me");
  setUpFrame("/settings", me, { onError: (e) => say(errorText(e)) });
  fillAccount();
  byId("account").addEventListener("submit", saveAccount);
  byId("delete-sure").addEventListener("change", () => {
    byId("delete").disabled = !byId("delete-sure").checked;
  });
  byId("delete").addEventListener("click", deleteEverything);
  providers = await api("/api/ai/providers");
  fillProviders();
  byId("provider").addEventListener("change", () => providerChanged());
  byId("bring").addEventListener("submit", bringKey);
  await showAi();
} catch (error) {
  say(errorText(error));
}

// -- your account ---------------------------------------------------------------

function fillAccount() {
  byId("display-name").value = me.display_name || "";
  byId("email").textContent = me.email || "–";
  byId("role").textContent = t(`account.${me.role}`);
  const offered = (document.querySelector('meta[name="joblens-languages"]')?.content || "en").split(",");
  const picker = byId("language-choice");
  fill(picker, ...offered.map((code) => h("option", { value: code }, ENDONYMS[code] || code)));
  picker.value = document.documentElement.lang;
}

async function saveAccount(event) {
  event.preventDefault();
  say(null);
  byId("account-saved").textContent = "";
  const name = byId("display-name").value.trim();
  const locale = byId("language-choice").value;
  try {
    me = await api("/api/me", {
      method: "PATCH", json: name ? { locale, display_name: name } : { locale },
    });
    // A new language means every word on the page: load it again.
    if (locale !== document.documentElement.lang) return window.location.reload();
    byId("account-saved").textContent = t("settings.saved");
  } catch (error) {
    say(errorText(error));
  }
}

// -- your AI --------------------------------------------------------------------

async function showAi() {
  const [ai, usage] = await Promise.all([api("/api/ai"), api("/api/usage")]);
  const own = ai.using === "own";
  const measured = MEASURED[`${ai.provider}/${ai.model}`];
  let forget = null;
  if (own) {
    forget = h("button", { type: "button", class: "button button-outline" }, t("settings.forget"));
    forget.addEventListener("click", forgetKey);
  }
  fill(byId("ai-now"),
    h("p", { class: "ai-line" }, own
      ? t("settings.using.own", { provider: labelOf(ai.provider), model: ai.model, hint: ai.key_hint })
      : t("settings.using.joblens", { model: ai.model })),
    h("p", { class: "muted" }, measured ? t(measured) : t("settings.notMeasured")),
    own && ai.verified_at && h("p", { class: "muted" }, t("settings.verified", {
      date: formatDate(ai.verified_at, { day: "numeric", month: "long", year: "numeric" }),
    })),
    forget && h("div", {}, forget),
  );

  const parts = [];
  if (usage.allowance_usd !== null) {
    const fill = h("div", { class: "bar-fill" });
    fill.style.width = `${Math.min(100, (100 * usage.joblens_usd) / usage.allowance_usd)}%`;
    parts.push(
      h("div", { class: "bar" }, fill),
      h("p", { class: "muted" }, t("ai.used", {
        spent: formatMoney(usage.joblens_usd, "USD"),
        allowance: formatMoney(usage.allowance_usd, "USD"),
      })),
      h("p", { class: "muted" }, t("settings.allowanceNote")),
    );
  } else if (own) {
    parts.push(h("p", { class: "muted" }, t("ai.ownSpent", { spent: formatMoney(usage.own_usd, "USD") })));
  } else {
    parts.push(h("p", { class: "muted" }, t("ai.unlimited")));
  }
  fill(byId("ai-usage"), ...parts);

  if (own && providers.some((one) => one.provider === ai.provider)) {
    byId("provider").value = ai.provider;
    providerChanged(ai.model);
    byId("thinking").checked = ai.thinking;
  }
  show(byId("bring"), providers.length > 0);
}

function fillProviders() {
  fill(byId("provider"), ...providers.map((one) => h("option", { value: one.provider }, one.label)));
  providerChanged();
}

function chosen() {
  return providers.find((one) => one.provider === byId("provider").value) || providers[0];
}

function providerChanged(model = null) {
  const preset = chosen();
  if (!preset) return;
  byId("model").value = model ?? preset.suggested ?? "";
  byId("model").placeholder = preset.suggested || "";
  // The list of exact ids lives at the provider; the address is our own
  // (llm/presets.py), still only followed when it is an ordinary web link.
  const list = /^https:\/\//i.test(preset.models_url || "")
    ? h("a", { href: preset.models_url, target: "_blank", rel: "noopener noreferrer" },
        t("settings.modelList", { provider: preset.label }))
    : null;
  fill(byId("model-hint"), t("settings.modelHint"), " ", list || "");
  byId("api-key").placeholder = preset.local ? t("settings.keyLocal") : "";
  byId("where").textContent = preset.local
    ? t("settings.whereLocal")
    : t("settings.where", { provider: preset.label });
}

function labelOf(provider) {
  return providers.find((one) => one.provider === provider)?.label || provider;
}

async function bringKey(event) {
  event.preventDefault();
  const preset = chosen();
  const model = byId("model").value.trim();
  const key = byId("api-key").value.trim();
  const problem = byId("bring-error");
  const fail = (text) => {
    problem.textContent = text || "";
    show(problem, Boolean(text));
  };
  fail(null);
  if (!model) return fail(t("settings.needModel"));
  if (!key && !preset.local) return fail(t("settings.needKey"));
  const button = byId("test-save");
  button.disabled = true;
  button.textContent = t("settings.testing");
  try {
    await api("/api/ai", {
      method: "PUT",
      json: { provider: preset.provider, model, api_key: key || "local", thinking: byId("thinking").checked },
    });
    byId("api-key").value = "";
    await showAi();
  } catch (error) {
    // 400 carries the provider's own sentence (a wrong key, an unknown model):
    // shown as it is, because it is the provider's words, not ours.
    if (error instanceof ApiError && error.status === 503) fail(t("settings.keysOff"));
    else fail(errorText(error));
  } finally {
    button.disabled = false;
    button.textContent = t("settings.testSave");
  }
}

async function forgetKey() {
  say(null);
  try {
    await api("/api/ai", { method: "DELETE" });
    byId("api-key").value = "";
    fillProviders();
    byId("thinking").checked = false;
    await showAi();
  } catch (error) {
    say(errorText(error));
  }
}

// -- your data ------------------------------------------------------------------

async function deleteEverything() {
  if (!byId("delete-sure").checked) return;
  byId("delete").disabled = true;
  try {
    await api("/api/me/delete", { method: "POST", json: { confirm: "delete everything" } });
    window.location.assign("/login");
  } catch (error) {
    byId("delete").disabled = false;
    say(errorText(error));
  }
}

// -- messages -------------------------------------------------------------------

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
