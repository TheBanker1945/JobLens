# Ground truth for the extraction eval

One JSON file per sample in `../vacancies/`:

```json
{
  "details": { ...a valid VacancyDetails... },
  "alternatives": { "company": ["Bakkerij De Vries"] }
}
```

`alternatives` lists other acceptable values for a scalar field (e.g. a company name
with and without "B.V."). List fields have no alternatives.

## Labelling guidelines

Decided by Mahdi on 2026-09-19. These rules define what "correct" means in the eval.

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

**Work mode**
- Any option to work from home, even "af en toe" (now and then) → `hybrid`.
- "Plaats- en tijdonafhankelijk" with an office desk → `hybrid`.
- Obvious from the job itself (warehouse work) → `onsite`, even if not stated.

**Vague terms**
- "Enkele jaren ervaring" → `experience_years_min: null` (no number stated).
- "Hbo- of wo-denkniveau" → `education_level: hbo` (sets the minimum in practice).

## Minor calls by Claude (review these)

- 02: "Je werkt volgens Scrum" → Scrum counts as a skill.
- 03: "met een handscanner" → handscanner counts as a task tool.
- 04: "Computer Vision" in the title and "image segmentation" in the tasks count as
  knowledge areas.
