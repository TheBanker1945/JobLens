// The frame every signed-in page shares: the top bar (brand, menu, language,
// account) and, on a phone, the bottom menu. Built here once, so four pages
// do not each carry a copy that drifts.

import { api } from "./api.js";
import { byId, h, show } from "./dom.js";
import { t } from "./i18n.js";

// Each language's own name, so someone who cannot read the current language
// can still find theirs.
const ENDONYMS = { en: "English", nl: "Nederlands", de: "Deutsch", fr: "Français", es: "Español" };

const PAGES = [
  { href: "/", key: "nav.dashboard", icon: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { href: "/cv", key: "nav.cv", icon: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5" },
  { href: "/preferences", key: "nav.preferences", icon: "M4 6h16 M4 12h10 M4 18h6" },
];

export function setUpFrame(current, user, { onError } = {}) {
  const top = byId("topbar");
  top.replaceChildren(
    h("a", { class: "brand", href: "/" }, logo(), "JobLens"),
    h("nav", { class: "mainnav", "aria-label": t("nav.label") },
      ...PAGES.map((one) => h("a", {
        href: one.href, "aria-current": one.href === current ? "page" : null,
      }, t(one.key))),
    ),
    h("div", { class: "topbar-end" },
      h("label", { class: "visually-hidden", for: "language" }, t("nav.language")),
      languagePicker(onError),
      accountMenu(user),
    ),
  );
  const bottom = byId("bottomnav");
  if (bottom) {
    bottom.setAttribute("aria-label", t("nav.label"));
    bottom.replaceChildren(
      ...PAGES.map((one) => h("a", {
        href: one.href, "aria-current": one.href === current ? "page" : null,
      }, icon(one.icon), t(one.key))),
    );
  }
}

function languagePicker(onError) {
  const offered = (document.querySelector('meta[name="joblens-languages"]')?.content || "en")
    .split(",");
  const picker = h("select", { id: "language", class: "language" },
    ...offered.map((code) => h("option", { value: code }, ENDONYMS[code] || code)),
  );
  picker.value = document.documentElement.lang;
  picker.addEventListener("change", async () => {
    try {
      await api("/api/me", { method: "PATCH", json: { locale: picker.value } });
      window.location.reload();
    } catch (error) {
      onError?.(error);
    }
  });
  return picker;
}

function accountMenu(user) {
  const name = user.display_name || user.email || "";
  const button = h("button", {
    type: "button", class: "avatar", "aria-haspopup": "true", "aria-expanded": "false",
    "aria-controls": "account-menu", "aria-label": t("account.menu"),
  }, (name.trim()[0] || "?").toUpperCase());
  const signOut = h("button", { type: "button" }, t("account.signOut"));
  const menu = h("div", { id: "account-menu", class: "menu", hidden: true },
    h("div", { class: "menu-who" },
      h("strong", {}, name),
      h("span", {}, [user.email, t(`account.${user.role}`)].filter(Boolean).join(" · ")),
    ),
    h("a", { href: "/api/me/export", download: "joblens-my-data.json" }, t("account.download")),
    signOut,
  );
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
  signOut.addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST" });
    } finally {
      window.location.assign("/login");
    }
  });
  return h("div", { class: "account" }, button, menu);
}

// Inline SVG, built as elements (the page's policy allows no inline markup
// from strings, and none is needed).
export function icon(paths, { size = 22, color = "currentColor" } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [name, value] of Object.entries({
    width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: color,
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  })) svg.setAttribute(name, value);
  for (const d of paths.split(" M").map((part, i) => (i ? `M${part}` : part))) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function logo() {
  const mark = icon("M15 15l5 5", { size: 18, color: "#ffffff" });
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  for (const [name, value] of Object.entries({ cx: 10.5, cy: 10.5, r: 6 })) {
    circle.setAttribute(name, value);
  }
  mark.setAttribute("stroke-width", "2.4");
  mark.prepend(circle);
  return h("span", { class: "logo" }, mark);
}

export const FILE_ICON = PAGES[1].icon;
