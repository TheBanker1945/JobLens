# Ground truth for the extraction eval

One JSON file per sample in `../vacancies/`:

```json
{
  "split": "dev",
  "details": { ...a valid VacancyDetails... },
  "alternatives": { "company": ["Bakkerij De Vries"] }
}
```

`alternatives` lists other acceptable values for a scalar field (e.g. a company name
with and without "B.V."). List fields have no alternatives.

## Splits

- `dev` (01–05): used to improve the prompt. Look at these mistakes freely.
- `holdout` (06–10): never used for tuning. Only for measuring before/after. If a
  change improves `dev` but not `holdout`, the prompt was fitted to the dev samples.

## Labelling guidelines

Decided by Mahdi on 2026-09-19 (milestones 1.4 and 1.6). These rules define what
"correct" means in the eval.

**Skills**
- Count every concrete skill or tool mentioned: required, "een pré" / "a plus", and
  tools that only appear in the task description.
- One item per skill: "Python (Django of FastAPI)" → Python, Django, FastAPI.
- Knowledge areas count as skills (Omgevingswet, deep learning).
- Soft skills do not count (schrijfvaardigheid, communicatief).
- Written as in the vacancy text (Dutch terms stay Dutch).

**Languages**
- Only languages the candidate MUST speak.
- Either/or ("Nederlands of Engels") → `[]`: neither is required on its own.

**Salary**
- `salary_note` is filled when there is salary information beyond the amounts
  (shift bonus, holiday allowance, "afhankelijk van ervaring"), or when there are no
  amounts at all ("marktconform", "schaal 11"). Only filled/null is scored.
- A pay scale without amounts → `salary_min`/`salary_max` null.
- An internship allowance ("stagevergoeding") is a salary.
- Part-time job with amounts stated for full-time → record the amounts as stated;
  `salary_note` says they are full-time based. No conversion by the model.
- "Up to €85,000" → `salary_min: null`, `salary_max: 85000`. Never invent a floor.

**Work mode**
- Any option to work from home, even "af en toe" (now and then) → `hybrid`.
- "Plaats- en tijdonafhankelijk" with an office desk → `hybrid`.
- Obvious from the job itself (warehouse work, ward nursing) → `onsite`, even if not
  stated. Not obvious (an office job) → `null`.

**Location**
- Fully remote → `city: null`, even if the company has offices.
- Two possible cities ("Rotterdam of Den Haag") → either is correct.

**Hours**
- Contract hours count: "40 hours, with a 4x9 option" → 40/40.

**Education**
- A degree "or equivalent experience" → `null`: no hard requirement.

**Contract**
- ZZP / freelance through an agency → `freelance`: the agency only brokers.
- A first fixed-term contract before a permanent one → `temporary`.

**Vague terms**
- "Enkele jaren ervaring" → `experience_years_min: null` (no number stated).
- "Hbo- of wo-denkniveau" → `education_level: hbo` (sets the minimum in practice).

## Minor calls by Claude (review these)

- 02: "Je werkt volgens Scrum" → Scrum counts as a skill.
- 03: "met een handscanner" → handscanner counts as a task tool.
- 04: "Computer Vision" in the title and "image segmentation" in the tasks count as
  knowledge areas.
- 06: `salary_note: "stagevergoeding"` — the kind of pay is salary information.
- 06: "e-mailmarketing" counts as a knowledge area.
- 07: "infrastructuur als code", CI/CD and the AZ-400 certification count as skills.
- 08: "BIG-registratie" counts as a skill (like the heftruckcertificaat).
- 09: "payments" and "fintech" (required experience areas) count as knowledge areas.
- 10: "CRM-systeem (Salesforce)" → Salesforce and CRM (split rule).
