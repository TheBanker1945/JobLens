"""The page a login link opens: one button that signs you in (7.5).

**Why a page and not a link that signs in on its own.** Chat apps and mail
clients open links to show a preview; a link that signed in when opened would
be used up by the preview, and the person clicking it would find it dead. So
opening the link only shows this page, and the sign-in is a POST the button
sends -- which a preview never does.

**Why the token is after a `#`.** A login link is
`http://127.0.0.1:8001/login#<token>`. Browsers never send what follows `#` to
the server, so the token cannot land in an access log or a proxy's; the page's
own script reads it, posts it once, and wipes it from the address bar.

No external script, style or font: the page carries its own, and its
Content-Security-Policy allows nothing else -- the script by its hash.
"""

import base64
import hashlib
import json

SCRIPT = """
const token = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const button = document.getElementById("go");
const note = document.getElementById("note");
if (!token) {
  button.disabled = true;
  note.textContent = "This link has no code in it. Ask for a new one.";
}
button.addEventListener("click", async () => {
  button.disabled = true;
  note.textContent = "Signing in...";
  const answer = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-JobLens": "1" },
    body: JSON.stringify({ token }),
  });
  if (answer.ok) { location.replace(NEXT); return; }
  const said = await answer.json().catch(() => ({}));
  note.textContent = said.detail || "That did not work. Ask for a new link.";
});
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Sign in to JobLens</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 26rem;
         margin: 18vh auto; padding: 0 1rem; }
  button { font: inherit; padding: .6rem 1.4rem; cursor: pointer; }
  #note { color: #666; min-height: 1.5em; }
</style>
</head>
<body>
<h1>JobLens</h1>
<p>You were invited to try JobLens. This link signs you in on this device
for 30 days, and works once.</p>
<button id="go">Sign in</button>
<p id="note"></p>
<script>{script}</script>
</body>
</html>
"""


def login_page(next_url: str) -> tuple[str, str]:
    """The page, and the Content-Security-Policy that allows only its script."""
    script = SCRIPT.replace("NEXT", json.dumps(next_url))
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = (
        "default-src 'none'; "
        f"script-src 'sha256-{digest}'; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return PAGE.replace("{script}", script), policy
