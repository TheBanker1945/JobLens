// The page's words, in the language the server chose (api/language.py).
//
// The server writes the language into <html lang="..."> before it sends the
// page, so the choice is made once, in one place, and tested there. This file
// loads that language's dictionary (assets/i18n/<code>.json), and English
// underneath it: a key missing from a translation shows the English words,
// never the key.

let words = {};
let fallback = {};
export let language = "en";

export async function loadLanguage() {
  language = document.documentElement.lang || "en";
  const english = await fetchDictionary("en");
  fallback = english;
  words = language === "en" ? english : await fetchDictionary(language);
}

async function fetchDictionary(code) {
  const answer = await fetch(`/assets/i18n/${code}.json`, { credentials: "same-origin" });
  return answer.ok ? answer.json() : {};
}

// t("hero.compared", { count: "1.166" }) -> the sentence with {count} filled in.
export function t(key, values = {}) {
  const text = words[key] ?? fallback[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    name in values ? String(values[name]) : whole,
  );
}

// One word for "none", "one" or "several": the keys end in .zero, .one, .other.
export function plural(key, n, values = {}) {
  const form = n === 0 ? "zero" : n === 1 ? "one" : "other";
  return t(`${key}.${form}`, { n, ...values });
}

// Fills every element marked data-i18n="key" with its words, and every
// data-i18n-attr="aria-label:key" attribute likewise.
export function translate(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of root.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of node.dataset.i18nAttr.split(";")) {
      const [attribute, key] = pair.split(":");
      node.setAttribute(attribute.trim(), t(key.trim()));
    }
  }
}

export function formatNumber(n) {
  return new Intl.NumberFormat(language).format(n);
}

export function formatMoney(amount, currency) {
  return new Intl.NumberFormat(language, {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "EUR" ? 0 : 2,
  }).format(amount);
}

export function formatDate(value, options) {
  return new Intl.DateTimeFormat(language, options).format(new Date(value));
}

export function formatList(items) {
  return new Intl.ListFormat(language, { style: "long", type: "conjunction" }).format(items);
}
