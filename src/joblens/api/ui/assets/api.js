// Talking to the JobLens API from the page.
//
// Two rules the server insists on, kept in one place: every request that
// changes something carries `X-JobLens: 1` (the server refuses it otherwise:
// that is what stops another website from using your session), and the session
// cookie is sent with same-origin requests only.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

// `signedIn: false` is for the login itself, where a 401 means "this link was
// used already" and must be shown, not answered by going back to /login.
export async function api(path, { method = "GET", json, form, signedIn = true } = {}) {
  const headers = {};
  let body;
  if (method !== "GET") headers["X-JobLens"] = "1";
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = form; // the browser writes the multipart boundary itself
  }
  let answer;
  try {
    answer = await fetch(path, { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, null); // no answer at all: offline, or the server is down
  }
  if (answer.status === 401 && signedIn) {
    // The session ran out, or was ended elsewhere: back to the start.
    window.location.assign("/login");
    throw new ApiError(401, null);
  }
  if (answer.status === 204) return null;
  const data = await answer.json().catch(() => ({}));
  if (!answer.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    throw new ApiError(answer.status, detail);
  }
  return data;
}
