// The CV upload form, shared by the dashboard, the guide and "My CV".
//
// It only uploads: POST /api/cvs with the file and, if given, the name to take
// out. What happens next -- refresh a card, move to the next step -- is the
// page's business, passed in as onDone.

import { api } from "./api.js";
import { h } from "./dom.js";
import { t } from "./i18n.js";

export function uploadForm({ onDone, onError, submitKey = "cv.upload" }) {
  const file = h("input", { type: "file", class: "input", name: "file", accept: ".pdf,.md,.txt" });
  const name = h("input", { type: "text", class: "input", name: "strip_name", autocomplete: "name" });
  const submit = h("button", { type: "submit", class: "button button-primary" }, t(submitKey));
  const form = h("form", { class: "form" },
    h("label", { class: "field" }, h("span", {}, t("cv.file")), file),
    h("label", { class: "field" }, h("span", {}, t("cv.name")), name),
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!file.files[0]) return onError?.(new Error(t("cv.chooseFile")));
    const upload = new FormData();
    upload.append("file", file.files[0]);
    upload.append("strip_name", name.value.trim());
    submit.disabled = true;
    submit.textContent = t("cv.uploading");
    try {
      const cv = await api("/api/cvs", { method: "POST", form: upload });
      form.reset();
      await onDone?.(cv);
    } catch (error) {
      onError?.(error);
    } finally {
      submit.disabled = false;
      submit.textContent = t(submitKey);
    }
  });
  return form;
}

// "links, e-mail and phone": what redaction took out, in words.
export function removedKinds(removed) {
  return Object.keys(removed || {}).map((kind) => t(`removed.${kind}`));
}
