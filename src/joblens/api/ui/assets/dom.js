// Building page elements, and the one rule that keeps the page safe.
//
// Vacancy titles, companies and quotes come from scraped adverts: text written
// by strangers. Put into the page as HTML, a title like
// `<img src=x onerror=...>` would run as code in the reader's session. So
// everything the page shows goes in as text (textContent), never parsed as
// HTML -- here or in any script that uses this (tests/test_ui.py holds every
// script to that). The page's Content-Security-Policy is the second lock:
// even an injected script tag would not be allowed to run.

export function h(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function byId(id) {
  return document.getElementById(id);
}

export function show(node, visible = true) {
  node.hidden = !visible;
}
