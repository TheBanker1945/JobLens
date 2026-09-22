/* The viewer. Plain DOM, no framework, no build step -- open the page and it is
   the code that is running.

   One rule worth naming: every piece of data from the API is put on the page
   with textContent and never with innerHTML. A vacancy is text somebody else
   wrote, and a page that pastes it in as markup is one advert away from being a
   different page than the one you read. */

const state = { run: null, bucket: "recommended", filter: "", open: null, labels: null };

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};

const ask = async (path, body) => {
  const answer = await fetch(path, body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : undefined);
  const payload = await answer.json();
  if (!answer.ok) throw new Error(payload.error || answer.statusText);
  return payload;
};

const money = (usd) => (usd === null || usd === undefined ? "unknown" : `$${usd.toFixed(3)}`);
const where = (row) => [row.company, row.city].filter(Boolean).join(" · ");

/* ---- the run ---------------------------------------------------------- */

async function loadRuns() {
  const { runs } = await ask("/api/runs");
  const picker = document.getElementById("run-picker");
  picker.replaceChildren(...runs.map((one) =>
    el("option", { value: one.id }, `${one.cv} — ${one.at.slice(0, 16).replace("T", " ")} (${one.judged} judged)`)
  ));
  if (!runs.length) {
    document.getElementById("run-head").replaceChildren(
      el("p", { class: "empty" }, "No runs stored yet. Run scripts/match_cv.py once.")
    );
    return;
  }
  picker.onchange = () => loadRun(picker.value);
  await loadRun(runs[0].id);
}

async function loadRun(id) {
  const run = encodeURIComponent(id);
  [state.run, state.labels] = await Promise.all([
    ask(`/api/runs/${run}`),
    ask(`/api/runs/${run}/labels`),
  ]);
  state.open = null;
  renderRun();
}

const myCall = (key) => (state.labels && state.labels.decisions[key]) || null;

function renderRun() {
  const run = state.run;
  const { stamp, counts, boundary } = run;

  document.getElementById("run-head").replaceChildren(
    el("h2", {}, `${stamp.cv_name} — ${counts.ranked} vacancies ranked, ${stamp.top} judged`),
    el("div", { class: "meta" },
      el("div", {}, run.stamp_line),
      run.funnel_line ? el("div", {}, run.funnel_line) : null,
      el("div", {}, `${money(run.cost_usd)} · ${Math.round(run.seconds)}s of model time · ` +
        `${run.prompt_tokens} tokens in, ${run.output_tokens} out`),
      boundary ? el("div", {},
        `shortlist cut between #${boundary.last_read.rank} (${boundary.last_read.score.toFixed(3)}) ` +
        `and #${boundary.first_unread.rank} (${boundary.first_unread.score.toFixed(3)}) — a gap of ${boundary.gap}`) : null
    )
  );

  const banner = document.getElementById("run-banner");
  banner.replaceChildren(
    run.outcome.fit === "ok" && !run.failures.length ? null :
      el("div", { class: "banner" },
        el("strong", {}, run.outcome.headline),
        run.outcome.advice.map((line) => el("p", {}, line)),
        run.failures.map((line) => el("p", {}, `could not be judged: ${line}`)))
  );

  const buckets = [
    ["recommended", "recommended", counts.recommended, "strong or possible"],
    ["rejected", "judged and rejected", counts.rejected, "a model read these and said no"],
    ["never_shortlisted", "never shortlisted", counts.never_shortlisted, "nobody read these"],
  ];
  document.getElementById("buckets").replaceChildren(...buckets.map(([key, name, count, hint]) =>
    el("button", {
      class: "bucket", "aria-current": String(state.bucket === key),
      onclick: () => { state.bucket = key; state.open = null; renderRows(); },
    }, `${name} — ${count}`, el("small", {}, hint))
  ));

  renderGaps();
  renderRows();
  renderLabelCount();
}

function renderLabelCount() {
  const counts = state.labels ? state.labels.counts : {};
  const node = document.getElementById("label-count");
  const reasons = counts.with_a_reason || 0;
  node.replaceChildren(
    `${reasons} vacanc${reasons === 1 ? "y" : "ies"} marked with a reason` +
    (state.labels && state.labels.judged_by ? ` · judged by ${state.labels.judged_by}` : "")
  );
}

function renderRows() {
  document.querySelectorAll(".bucket").forEach((node, index) => {
    node.setAttribute("aria-current", String(["recommended", "rejected", "never_shortlisted"][index] === state.bucket));
  });
  const wanted = state.filter.trim().toLowerCase();
  const rows = state.run[state.bucket].filter((row) =>
    !wanted || `${row.title} ${row.company || ""} ${row.city || ""}`.toLowerCase().includes(wanted));
  const list = document.getElementById("rows");
  if (!rows.length) {
    list.replaceChildren(el("p", { class: "empty" }, wanted ? "Nothing here matches that." : "Nothing in this bucket."));
    return;
  }
  list.replaceChildren(...rows.map(renderRow));
}

function renderRow(row) {
  const open = state.open === row.key;
  const node = el("div", { class: "row", onclick: (event) => {
    if (event.target.closest("a, .detail")) return;
    state.open = open ? null : row.key;
    renderRows();
  } },
    el("div", { class: "line" },
      el("span", { class: "rank" }, row.rank ? `#${row.rank}` : ""),
      row.judged ? el("span", { class: `badge ${row.verdict}` }, `${row.verdict} ${row.fit}`)
                 : el("span", { class: "badge weak" }, row.score.toFixed(3)),
      el("span", { class: "title" }, row.title),
      el("span", { class: "where" }, where(row)),
      myCall(row.key) ? el("span", { class: `badge mine ${myCall(row.key).call}` },
        `you: ${myCall(row.key).call}`) : null
    ),
    row.judged ? el("div", { class: "where" }, row.summary) : null
  );
  if (open) node.append(renderDetail(row));
  return node;
}

function renderDetail(row) {
  const detail = el("div", { class: "detail" });
  if (row.judged) {
    detail.append(
      el("div", { class: "meta" },
        `retrieval #${row.rank} at ${row.score.toFixed(3)}, matched by "${row.part}" · ` +
        `${row.evidence} claim(s) verified` + (row.dropped ? `, ${row.dropped} dropped: the quote was not in the text` : "")),
    );
    if (row.gaps.length) {
      detail.append(el("h4", {}, "what the vacancy asks for that your CV does not show"));
      for (const gap of row.gaps) {
        detail.append(el("div", { class: "claim" },
          el("div", {}, `${gap.requirement} (${gap.required ? "required" : "a plus"})`),
          el("q", {}, gap.quote)));
      }
    }
  } else {
    detail.append(el("div", { class: "meta" },
      `ranked #${row.rank} of ${state.run.counts.ranked} at ${row.score.toFixed(3)}, ` +
      `matched by "${row.part}" — below the cut, so no model ever read it`));
  }
  if (row.url && row.url.startsWith("http")) {
    detail.append(el("p", {}, el("a", { href: row.url, target: "_blank", rel: "noreferrer" }, row.url)));
  }
  detail.append(renderMarking(row));
  const body = el("div", {}, el("p", { class: "empty" }, "loading the vacancy…"));
  detail.append(body);
  ask(`/api/vacancy?key=${encodeURIComponent(row.key)}&run=${encodeURIComponent(state.run.id)}`)
    .then((found) => body.replaceChildren(renderVacancy(found)))
    .catch((err) => body.replaceChildren(el("p", { class: "empty" }, `could not load it: ${err.message}`)));
  return detail;
}

/* Marking. A call needs a reason, and the reason is stored exactly as typed:
   nothing here summarises it or turns a run of answers into a rule. */
function renderMarking(row) {
  const mine = myCall(row.key);
  const reason = el("input", {
    type: "text", class: "reason", value: mine ? mine.reason : "",
    placeholder: "why? one line — e.g. 'they ask 4 years, I would apply anyway'",
  });
  const status = el("span", { class: "where" },
    mine ? `you said ${mine.call} on ${mine.at.slice(0, 10)}` : "");

  const mark = async (call) => {
    if (!reason.value.trim()) {
      status.textContent = "a reason is required — that is the point of this";
      reason.focus();
      return;
    }
    status.textContent = "saving…";
    try {
      state.labels = await ask(`/api/runs/${encodeURIComponent(state.run.id)}/labels`,
        { key: row.key, call, reason: reason.value });
      renderLabelCount();
      renderRows();  // the row now carries "you: apply" next to the judge's badge
    } catch (err) {
      status.textContent = err.message;
    }
  };

  return el("div", { class: "marking" },
    el("h4", {}, row.judged
      ? `the judge said ${row.verdict} ${row.fit}. what do you say?`
      : "nobody read this one. what do you say?"),
    el("div", { class: "marking-line" },
      reason,
      el("button", { class: "call apply", onclick: () => mark("apply") }, "would apply"),
      el("button", { class: "call maybe", onclick: () => mark("maybe") }, "might"),
      el("button", { class: "call no", onclick: () => mark("no") }, "no")),
    status);
}

function renderVacancy(found) {
  const fragment = document.createDocumentFragment();
  if (found.details) {
    const rows = Object.entries(found.details)
      .filter(([, value]) => value !== null && value !== undefined && !(Array.isArray(value) && !value.length));
    fragment.append(
      el("h4", {}, "extracted fields"),
      el("table", {}, ...rows.map(([name, value]) =>
        el("tr", {}, el("td", {}, name), el("td", {}, Array.isArray(value) ? value.join(", ") : String(value)))))
    );
  } else {
    fragment.append(el("h4", {}, "extracted fields"), el("p", { class: "empty" },
      "none: this vacancy was never extracted, so it was never embedded either."));
  }
  fragment.append(el("h4", {}, "what the board published"), el("pre", { class: "text" }, found.text));
  return fragment;
}

function renderGaps() {
  const { groups, ungrouped, already_on_cv } = state.run.gaps;
  const node = document.getElementById("gaps");
  if (!groups.length) { node.replaceChildren(); return; }
  node.replaceChildren(
    el("h2", {}, "What keeps coming up that you do not have"),
    el("div", { class: "rows" }, ...groups.slice(0, 8).map((group) =>
      el("div", { class: "row" },
        el("div", { class: "line" },
          el("span", { class: "rank" }, group.weight.toFixed(1)),
          el("span", { class: "title" }, group.term),
          el("span", { class: "where" },
            `in ${group.count} vacancies` + (group.required_count ? `, ${group.required_count} as a requirement` : "")))))),
    el("p", { class: "meta" },
      `weight is the sum of each match's fit/100. ${ungrouped} gap(s) named nothing the corpus calls a skill; ` +
      `${already_on_cv} asked for something your CV does list and are left out.`)
  );
}

/* ---- the corpus ------------------------------------------------------- */

let corpusOpen = null;

async function loadCorpus(query = "") {
  const found = await ask(`/api/corpus?q=${encodeURIComponent(query)}&limit=200`);
  document.getElementById("corpus-head").replaceChildren(
    el("h2", {}, `${found.corpus} corpus — ${found.total} vacancies`),
    el("div", { class: "meta" },
      el("div", {}, found.funnel_line),
      el("div", {}, `${found.matched} match this search, showing ${found.shown}`))
  );
  const list = document.getElementById("corpus-rows");
  list.replaceChildren(...found.vacancies.map((vacancy) => {
    const open = corpusOpen === vacancy.key;
    const node = el("div", { class: "row", onclick: (event) => {
      if (event.target.closest("a, .detail")) return;
      corpusOpen = open ? null : vacancy.key;
      loadCorpus(document.getElementById("corpus-search").value);
    } },
      el("div", { class: "line" },
        el("span", { class: "rank" }, vacancy.source),
        el("span", { class: "title" }, vacancy.title),
        el("span", { class: "where" }, where(vacancy)),
        el("span", { class: "badge weak" }, vacancy.extracted ? `${vacancy.chars} chars` : "not extracted")));
    if (open) {
      const detail = el("div", { class: "detail" }, el("p", { class: "empty" }, "loading…"));
      node.append(detail);
      ask(`/api/vacancy?key=${encodeURIComponent(vacancy.key)}`)
        .then((one) => detail.replaceChildren(renderVacancy(one)))
        .catch((err) => detail.replaceChildren(el("p", { class: "empty" }, err.message)));
    }
    return node;
  }));
}

/* ---- wiring ----------------------------------------------------------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((one) =>
      one.setAttribute("aria-current", String(one === tab)));
    document.getElementById("run").hidden = tab.dataset.tab !== "run";
    document.getElementById("corpus").hidden = tab.dataset.tab !== "corpus";
    if (tab.dataset.tab === "corpus" && !document.getElementById("corpus-rows").children.length) {
      loadCorpus();
    }
  };
});

document.getElementById("filter").oninput = (event) => {
  state.filter = event.target.value;
  renderRows();
};

let typing;
document.getElementById("corpus-search").oninput = (event) => {
  clearTimeout(typing);
  const query = event.target.value;
  typing = setTimeout(() => loadCorpus(query), 200);
};

loadRuns().catch((err) => {
  document.getElementById("run-head").replaceChildren(
    el("p", { class: "empty" }, `could not load: ${err.message}`));
});
