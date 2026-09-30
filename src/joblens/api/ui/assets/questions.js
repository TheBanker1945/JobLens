// The onboarding's questions (7.9.3), one table: the guide asks them one at a
// time, and the dashboard counts the ones still open.
//
// Every answer lands in the preferences document (preferences/schema.py), the
// same one the one-page form edits, so both always show the same answers. Each
// question names its texts in full ("onb.hours.title", not a key built from
// its id) so tests/test_ui.py can check they exist in all five languages.
//
// Option values are the values stored: numbers for money and distance, true or
// false for the degree. The form's limits (prefs-form.js, schema.py) hold here
// too: hours stay within 0-60, a distance within 1-300 km.

export const PHASES = [
  ["cv", "onb.phase.cv"],
  ["now", "onb.phase.now"],
  ["job", "onb.phase.job"],
  ["place", "onb.phase.place"],
  ["stretch", "onb.phase.stretch"],
];

// "Full-time" is hours, not a contract: each choice is a range, and the ones
// picked become one range (full-time and four days: 32 to 40 hours).
const HOURS = { full: [36, 40], four: [32, 35], part: [null, 28] };

export const QUESTIONS = [
  {
    id: "cv", phase: "cv", kind: "cv", short: "onb.cv.short",
    title: "guide.cv.title", hint: "guide.cv.intro", changes: "onb.cv.changes",
  },
  {
    id: "work_status", phase: "now", kind: "single", short: "onb.status.short",
    title: "onb.status.title", changes: "onb.savedOnly",
    options: [
      ["employed", "workStatus.employed"],
      ["unemployed", "workStatus.unemployed"],
      ["self_employed", "workStatus.self_employed"],
      ["student", "workStatus.student"],
    ],
  },
  {
    id: "goals", phase: "now", kind: "multi", short: "onb.goals.short",
    title: "onb.goals.title", hint: "onb.pickAll", changes: "onb.savedOnly",
    options: [
      ["income_soon", "goal.income_soon"],
      ["first_job", "goal.first_job"],
      ["extra_income", "goal.extra_income"],
      ["balance", "goal.balance"],
      ["secure", "goal.secure"],
      ["step_up", "goal.step_up"],
      ["switch", "goal.switch"],
    ],
  },
  {
    id: "contract_types", phase: "job", kind: "multi", short: "prefs.contract",
    title: "onb.contract.title", hint: "onb.pickAll", changes: "onb.contract.changes",
    options: [
      ["permanent", "contract.permanent"],
      ["temporary", "contract.temporary"],
      ["temp_agency", "contract.temp_agency"],
      ["freelance", "contract.freelance"],
      ["internship", "contract.internship"],
    ],
  },
  {
    id: "hours", phase: "job", kind: "multi", short: "onb.hours.short",
    title: "onb.hours.title", hint: "onb.pickAll", changes: "onb.hours.changes",
    options: [
      ["full", "onb.hours.full", "onb.hours.fullSub"],
      ["four", "onb.hours.four", "onb.hours.fourSub"],
      ["part", "onb.hours.part", "onb.hours.partSub"],
    ],
    get: hoursChosen,
    set: hoursOf,
    answered: (p) => p.hours_min !== null || p.hours_max !== null,
  },
  {
    id: "work_modes", phase: "job", kind: "multi", short: "prefs.workMode",
    title: "onb.workMode.title", hint: "onb.pickAll", changes: "onb.workMode.changes",
    options: [
      ["onsite", "workMode.onsite"],
      ["hybrid", "workMode.hybrid"],
      ["remote", "workMode.remote"],
    ],
  },
  {
    id: "seniority", phase: "job", kind: "multi", short: "prefs.level",
    title: "onb.level.title", hint: "onb.pickAll", changes: "onb.level.changes",
    options: [
      ["junior", "level.junior"],
      ["medior", "level.medior"],
      ["senior", "level.senior"],
      ["lead", "level.lead"],
    ],
  },
  {
    id: "salary_min", phase: "job", kind: "single", short: "onb.salary.short",
    title: "onb.salary.title", hint: "onb.salary.hint", changes: "onb.salary.changes",
    options: [2500, 3000, 3500, 4000, 4500, 5000].map((euro) => [euro, { money: euro }]),
  },
  {
    id: "home", phase: "place", kind: "place", short: "prefs.home",
    title: "onb.home.title", hint: "onb.home.hint", changes: "onb.home.changes",
  },
  {
    id: "max_distance_km", phase: "place", kind: "single", needs: "home", short: "onb.distance.short",
    title: "onb.distance.title", hint: "prefs.distanceHint", changes: "onb.distance.changes",
    options: [10, 25, 50, 75].map((km) => [km, { km }]),
  },
  {
    id: "languages", phase: "place", kind: "multi", short: "prefs.languages",
    title: "onb.languages.title", hint: "onb.pickAll", changes: "onb.languages.changes",
    options: [
      ["Dutch", "languageName.Dutch"],
      ["English", "languageName.English"],
      ["German", "languageName.German"],
      ["French", "languageName.French"],
      ["Spanish", "languageName.Spanish"],
    ],
  },
  {
    id: "stretch_years", phase: "stretch", kind: "single", short: "onb.years.short",
    title: "prefs.years", changes: "onb.judge.changes",
    options: [
      [0, "prefs.years.0"],
      [1, "prefs.years.1"],
      [2, "prefs.years.2"],
      [3, "prefs.years.3"],
      [10, "prefs.years.any"],
    ],
  },
  {
    id: "stretch_degree", phase: "stretch", kind: "single", short: "onb.degree.short",
    title: "prefs.degree", changes: "onb.judge.changes",
    options: [
      [true, "prefs.degree.yes"],
      [false, "prefs.degree.no"],
    ],
  },
  {
    id: "avoid_employers", phase: "stretch", kind: "words", short: "prefs.avoidEmployers",
    title: "onb.employers.title", hint: "onb.employers.hint", changes: "onb.employers.changes",
  },
  {
    // Kept as the words the person chose, in their language: the judge reads
    // them as written (preferences/prompt.py), and the form shows them so.
    id: "avoid_sectors", phase: "stretch", kind: "multi", own: true, short: "prefs.avoidSectors",
    title: "onb.sectors.title", hint: "onb.sectors.hint", changes: "onb.judge.changes",
    options: [
      ["defence", "onb.sector.defence"],
      ["gambling", "onb.sector.gambling"],
      ["tobacco", "onb.sector.tobacco"],
      ["oil", "onb.sector.oil"],
      ["crypto", "onb.sector.crypto"],
      ["advertising", "onb.sector.advertising"],
      ["recruitment", "onb.sector.recruitment"],
    ],
  },
];

// The questions a preferences document can leave open: all but the CV.
export const ASKED = QUESTIONS.filter((q) => q.kind !== "cv");

export function isAnswered(q, prefs, cv = null) {
  if (q.kind === "cv") return Boolean(cv);
  if (q.answered) return q.answered(prefs);
  const value = prefs[q.id];
  return Array.isArray(value) ? value.length > 0 : value !== null && value !== undefined && value !== "";
}

// The questions still open, in order. A distance without a home to measure
// from cannot be answered, so it is open only once there is a home.
export function openQuestions(prefs, cv = null, questions = QUESTIONS) {
  return questions.filter((q) =>
    !isAnswered(q, prefs, cv) && !(q.needs && !isAnswered({ id: q.needs }, prefs)));
}

function hoursChosen(prefs) {
  if (prefs.hours_min === null && prefs.hours_max === null) return [];
  const low = prefs.hours_min ?? 0;
  const high = prefs.hours_max ?? 60;
  return Object.entries(HOURS)
    .filter(([, [from, to]]) => (from ?? 0) >= low && to <= high)
    .map(([key]) => key);
}

function hoursOf(chosen) {
  const ranges = chosen.map((key) => HOURS[key]);
  const froms = ranges.map(([from]) => from);
  return {
    hours_min: froms.includes(null) ? null : Math.min(...froms),
    hours_max: Math.max(...ranges.map(([, to]) => to)),
  };
}
