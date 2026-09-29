// Following a running match: ask for the job every 1.5 s, show where it is,
// stop when it is done or failed. Shared by the dashboard and the guide.

import { api } from "./api.js";
import { byId, show } from "./dom.js";
import { t } from "./i18n.js";

const POLL_MS = 1500;
let following = null;

// Resolves with the finished job ("done" or "failed"); rejects if the job can
// no longer be read (offline, signed out).
export async function followJob(jobId) {
  following = jobId;
  show(byId("progress"));
  try {
    while (following === jobId) {
      const job = await api(`/api/matches/${encodeURIComponent(jobId)}`);
      if (job.status === "done" || job.status === "failed") return job;
      draw(job);
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
    return null;
  } finally {
    if (following === jobId) following = null;
    show(byId("progress"), false);
  }
}

export function isFollowing(jobId) {
  return following === jobId;
}

function draw(job) {
  let text = t("progress.queued");
  let share = 4;
  if (job.stage === "reading") [text, share] = [t("progress.reading"), 10];
  if (job.stage === "ranking") [text, share] = [t("progress.ranking"), 22];
  if (job.stage === "judging" && job.total) {
    text = t("progress.judging", { done: Math.min(job.done + 1, job.total), total: job.total });
    share = 30 + (70 * job.done) / job.total;
  }
  byId("progress-text").textContent = text;
  byId("progress-fill").style.width = `${share}%`;
}
