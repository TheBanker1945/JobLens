// The one-page preferences form, shared by the guide (step 2) and "What I'm
// looking for". Every field may stay empty, and empty means no preference.
//
// It checks what it can before sending (whole numbers, fewest hours not above
// most, a home for a distance) so the person reads the problem in their own
// language next to the field; the server checks everything again
// (preferences/schema.py) and is the one that decides.

import { h } from "./dom.js";
import { t } from "./i18n.js";

const CONTRACTS = ["permanent", "temporary", "temp_agency", "freelance", "internship"];
const WORK_MODES = ["onsite", "hybrid", "remote"];
const LEVELS = ["junior", "medior", "senior", "lead"];
const LANGUAGES = ["Dutch", "English", "German", "French", "Spanish"];
const YEARS = [["", "prefs.years.none"], ["0", "prefs.years.0"], ["1", "prefs.years.1"],
  ["2", "prefs.years.2"], ["3", "prefs.years.3"], ["10", "prefs.years.any"]];
const DEGREE = [["", "prefs.degree.none"], ["yes", "prefs.degree.yes"], ["no", "prefs.degree.no"]];
// The server's limits (preferences/schema.py), and what to say when one is crossed.
const LIMITS = {
  max_distance_km: [1, 300, "prefs.error.distanceRange"],
  hours_min: [0, 60, "prefs.error.hoursRange"],
  hours_max: [0, 60, "prefs.error.hoursRange"],
  salary_min: [0, Infinity, "prefs.error.number"],
};

export function preferencesForm(prefs, places = []) {
  const fields = {};
  const errors = {};

  const choices = (name, legendKey, values, labelOf, chosen) => {
    const group = h("div", { class: "choices" },
      ...values.map((value) => h("label", { class: "choice" },
        h("input", { type: "checkbox", name, value, checked: chosen.includes(value) }),
        labelOf(value),
      )),
    );
    fields[name] = group;
    return fieldset(name, legendKey, group);
  };
  const radios = (name, legendKey, options, chosen) => {
    const group = h("div", { class: "choices" },
      ...options.map(([value, key]) => h("label", { class: "choice" },
        h("input", { type: "radio", name, value, checked: value === chosen }),
        t(key),
      )),
    );
    fields[name] = group;
    return fieldset(name, legendKey, group);
  };
  const input = (name, labelKey, value, extra = {}, hintKey = null) => {
    const hintId = hintKey ? `pref-${name}-hint` : null;
    const box = h("input", {
      class: "input", name, id: `pref-${name}`, value: value ?? "",
      "aria-describedby": hintId, ...extra,
    });
    fields[name] = box;
    return h("div", { class: "field" },
      h("label", { for: `pref-${name}` }, t(labelKey)),
      box,
      hintKey && h("span", { class: "field-hint", id: hintId }, t(hintKey)),
      errorSlot(name),
    );
  };
  const number = (name, labelKey, value, hintKey = null) => {
    const [min, max] = LIMITS[name];
    const range = Number.isFinite(max) ? { min, max } : { min };
    const extra = { type: "number", step: 1, inputmode: "numeric", ...range };
    return input(name, labelKey, value, extra, hintKey);
  };
  const fieldset = (name, legendKey, group) =>
    h("fieldset", { class: "fieldset" }, h("legend", {}, t(legendKey)), group, errorSlot(name));
  const errorSlot = (name) => {
    errors[name] = h("span", { class: "field-error", role: "alert", hidden: true });
    return errors[name];
  };

  const datalist = h("datalist", { id: "places" }, ...places.map((name) => h("option", { value: name })));
  const years = prefs.stretch_years === null || prefs.stretch_years === undefined
    ? "" : String(prefs.stretch_years >= 10 ? 10 : prefs.stretch_years);
  const degree = prefs.stretch_degree === true ? "yes" : prefs.stretch_degree === false ? "no" : "";

  const element = h("div", { class: "prefs-grid" },
    choices("contract_types", "prefs.contract", CONTRACTS, (v) => t(`contract.${v}`), prefs.contract_types || []),
    choices("work_modes", "prefs.workMode", WORK_MODES, (v) => t(`workMode.${v}`), prefs.work_modes || []),
    h("div", { class: "field-row" },
      input("home", "prefs.home", prefs.home, { list: "places", autocomplete: "off" },
        "prefs.homeHint"),
      number("max_distance_km", "prefs.distance", prefs.max_distance_km),
    ),
    h("div", { class: "field-row" },
      number("hours_min", "prefs.hoursMin", prefs.hours_min, "prefs.perWeek"),
      number("hours_max", "prefs.hoursMax", prefs.hours_max, "prefs.perWeek"),
      number("salary_min", "prefs.salary", prefs.salary_min, "prefs.salaryHint"),
    ),
    choices("seniority", "prefs.level", LEVELS, (v) => t(`level.${v}`), prefs.seniority || []),
    choices("languages", "prefs.languages", LANGUAGES, (v) => t(`languageName.${v}`), prefs.languages || []),
    input("avoid_employers", "prefs.avoidEmployers", (prefs.avoid_employers || []).join(", "),
      {}, "prefs.commas"),
    input("avoid_sectors", "prefs.avoidSectors", (prefs.avoid_sectors || []).join(", "),
      {}, "prefs.commas"),
    radios("stretch_years", "prefs.years", YEARS, years),
    radios("stretch_degree", "prefs.degree", DEGREE, degree),
    datalist,
  );

  function read() {
    clearErrors();
    const problems = {};
    const checked = (name) =>
      [...fields[name].querySelectorAll("input:checked")].map((box) => box.value);
    const whole = (name) => {
      const box = fields[name];
      const raw = box.value.trim();
      // A number box reports "" for text it cannot read ("-", "1e"), so ask
      // the browser whether something was typed, or it would vanish unsaid.
      if (raw === "" && !box.validity?.badInput) return null;
      if (!/^\d+$/.test(raw)) {
        problems[name] = "prefs.error.number";
        return null;
      }
      const [min, max, tooFar] = LIMITS[name];
      const value = Number(raw);
      if (value < min || value > max) problems[name] = tooFar;
      return value;
    };
    const list = (name) =>
      fields[name].value.split(",").map((part) => part.trim()).filter(Boolean);
    const values = {
      contract_types: checked("contract_types"),
      work_modes: checked("work_modes"),
      home: fields.home.value.trim() || null,
      max_distance_km: whole("max_distance_km"),
      hours_min: whole("hours_min"),
      hours_max: whole("hours_max"),
      salary_min: whole("salary_min"),
      seniority: checked("seniority"),
      languages: checked("languages"),
      avoid_employers: list("avoid_employers"),
      avoid_sectors: list("avoid_sectors"),
      stretch_years: checked("stretch_years")[0] ? Number(checked("stretch_years")[0]) : null,
      stretch_degree: { yes: true, no: false }[checked("stretch_degree")[0]] ?? null,
    };
    if (values.hours_min !== null && values.hours_max !== null && values.hours_min > values.hours_max) {
      problems.hours_max = "prefs.error.hours";
    }
    if (values.max_distance_km !== null && !values.home) problems.home = "prefs.error.distanceNeedsHome";
    if (Object.keys(problems).length) {
      showErrors(problems);
      return null;
    }
    return values;
  }

  function showErrors(problems) {
    clearErrors();
    let first = null;
    for (const [name, key] of Object.entries(problems)) {
      const slot = errors[name] || errors.contract_types;
      slot.textContent = t(key);
      slot.hidden = false;
      first ||= fields[name];
    }
    first?.scrollIntoView({ block: "center" });
    first?.focus?.();
  }

  function clearErrors() {
    for (const slot of Object.values(errors)) {
      slot.textContent = "";
      slot.hidden = true;
    }
  }

  return { element, read, showErrors };
}

// The server's 422 answer, as field -> text key. Field errors name their
// field; the checks across fields (hours, distance, home) are recognised by
// what they say, and anything else becomes a general message.
export function serverProblems(details) {
  const problems = {};
  for (const one of details || []) {
    const field = one.loc?.[1];
    const said = String(one.msg || "");
    if (said.includes("hours_min is more")) problems.hours_max = "prefs.error.hours";
    else if (said.includes("needs a home")) problems.home = "prefs.error.distanceNeedsHome";
    else if (said.includes("not a Dutch place")) problems.home = "prefs.error.home";
    else if (field) problems[field] = "prefs.error.value";
  }
  return Object.keys(problems).length ? problems : { contract_types: "error.generic" };
}
