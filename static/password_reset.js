(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);

  function installStyles() {
    if (document.getElementById("northstarPasswordResetStyles")) return;
    const style = document.createElement("style");
    style.id = "northstarPasswordResetStyles";
    style.textContent = `
      .ns-forgot-wrap{display:flex;justify-content:flex-end;margin:-4px 0 12px}
      .ns-forgot-link{appearance:none;border:0;background:transparent;padding:0;color:#8e3d31;font:600 12px/1.3 inherit;cursor:pointer;text-decoration:underline;text-underline-offset:3px}
      .ns-reset-overlay{position:fixed;inset:0;z-index:99999;display:grid;place-items:center;padding:20px;background:rgba(24,22,19,.58);backdrop-filter:blur(8px)}
      .ns-reset-card{width:min(460px,100%);background:#f5f0e7;color:#181613;border:1px solid rgba(24,22,19,.18);box-shadow:18px 18px 0 rgba(24,22,19,.18);padding:28px}
      .ns-reset-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}
      .ns-reset-head h2{margin:0;font:700 30px/1.05 Georgia,serif;letter-spacing:-.03em}
      .ns-reset-head p{margin:8px 0 0;color:#6e675e;font-size:13px;line-height:1.5}
      .ns-reset-close{appearance:none;border:1px solid rgba(24,22,19,.2);background:transparent;width:34px;height:34px;cursor:pointer;font-size:20px}
      .ns-reset-field{display:grid;gap:7px;margin:14px 0}
      .ns-reset-field label{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#6e675e}
      .ns-reset-field input{width:100%;box-sizing:border-box;border:1px solid #bdb4a7;background:#fffdf8;padding:13px 14px;font:500 15px/1.2 inherit;color:#181613;outline:none}
      .ns-reset-field input:focus{border-color:#8e3d31;box-shadow:0 0 0 3px rgba(142,61,49,.12)}
      .ns-reset-submit{width:100%;border:0;background:#181613;color:#fff;padding:14px 16px;font:700 13px/1 inherit;cursor:pointer;margin-top:8px}
      .ns-reset-submit:disabled{opacity:.55;cursor:wait}
      .ns-reset-message{min-height:20px;margin-top:12px;font-size:13px;line-height:1.45;color:#8e3d31}
      .ns-reset-message.success{color:#35624a}
      .ns-reset-debug{display:block;margin-top:10px;overflow-wrap:anywhere;color:#315d6e}
    `;
    document.head.appendChild(style);
  }

  async function request(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }

  function closeOverlay() {
    document.getElementById("northstarResetOverlay")?.remove();
  }

  function createOverlay({title, description, fields, submitLabel, onSubmit}) {
    closeOverlay();
    const overlay = document.createElement("div");
    overlay.className = "ns-reset-overlay";
    overlay.id = "northstarResetOverlay";
    overlay.innerHTML = `
      <section class="ns-reset-card" role="dialog" aria-modal="true" aria-labelledby="nsResetTitle">
        <div class="ns-reset-head">
          <div><h2 id="nsResetTitle"></h2><p id="nsResetDescription"></p></div>
          <button class="ns-reset-close" type="button" aria-label="Close">×</button>
        </div>
        <form id="nsResetForm"></form>
      </section>`;
    overlay.querySelector("#nsResetTitle").textContent = title;
    overlay.querySelector("#nsResetDescription").textContent = description;
    const form = overlay.querySelector("#nsResetForm");
    for (const field of fields) {
      const wrap = document.createElement("div");
      wrap.className = "ns-reset-field";
      const label = document.createElement("label");
      label.htmlFor = field.id;
      label.textContent = field.label;
      const input = document.createElement("input");
      Object.assign(input, field);
      wrap.append(label, input);
      form.appendChild(wrap);
    }
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "ns-reset-submit";
    submit.textContent = submitLabel;
    const message = document.createElement("div");
    message.className = "ns-reset-message";
    form.append(submit, message);

    overlay.querySelector(".ns-reset-close").addEventListener("click", closeOverlay);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeOverlay();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      message.className = "ns-reset-message";
      message.textContent = "Working…";
      try {
        await onSubmit(Object.fromEntries(new FormData(form)), message);
      } catch (error) {
        message.textContent = error.message || "Something went wrong.";
      } finally {
        submit.disabled = false;
      }
    });
    document.body.appendChild(overlay);
    overlay.querySelector("input")?.focus();
  }

  function openForgot() {
    const existingEmail = $("#loginEmail")?.value || "";
    createOverlay({
      title: "Reset password",
      description: "Enter your Northstar account email. The reset link expires after 30 minutes.",
      fields: [{id: "nsForgotEmail", name: "email", type: "email", autocomplete: "email", required: true, value: existingEmail, label: "Email"}],
      submitLabel: "Create reset link",
      onSubmit: async ({email}, message) => {
        const payload = await request("/api/auth/forgot-password", {email});
        message.className = "ns-reset-message success";
        message.textContent = payload.message;
        if (payload.debug_reset_url) {
          const link = document.createElement("a");
          link.className = "ns-reset-debug";
          link.href = payload.debug_reset_url;
          link.textContent = "Open debug reset link";
          message.appendChild(link);
        }
      },
    });
  }

  function openReset(token) {
    createOverlay({
      title: "Choose a new password",
      description: "Use at least 10 characters. Completing this reset signs out every existing session.",
      fields: [
        {id: "nsNewPassword", name: "password", type: "password", autocomplete: "new-password", required: true, minLength: 10, maxLength: 256, label: "New password"},
        {id: "nsConfirmPassword", name: "confirm", type: "password", autocomplete: "new-password", required: true, minLength: 10, maxLength: 256, label: "Confirm password"},
      ],
      submitLabel: "Update password",
      onSubmit: async ({password, confirm}, message) => {
        if (password !== confirm) throw new Error("The passwords do not match.");
        const payload = await request("/api/auth/reset-password", {token, password});
        message.className = "ns-reset-message success";
        message.textContent = payload.message;
        const url = new URL(window.location.href);
        url.searchParams.delete("reset");
        url.searchParams.delete("reset_token");
        history.replaceState({}, "", url.pathname + url.search + url.hash);
        setTimeout(closeOverlay, 1600);
      },
    });
  }

  function installLink() {
    const form = document.getElementById("loginForm");
    if (!form || document.getElementById("northstarForgotPassword")) return;
    const wrap = document.createElement("div");
    wrap.className = "ns-forgot-wrap";
    const button = document.createElement("button");
    button.type = "button";
    button.id = "northstarForgotPassword";
    button.className = "ns-forgot-link";
    button.textContent = "Forgot password?";
    button.addEventListener("click", openForgot);
    wrap.appendChild(button);
    const error = document.getElementById("loginError");
    form.insertBefore(wrap, error || form.querySelector("button[type=submit]"));
  }

  function start() {
    installStyles();
    installLink();
    const params = new URLSearchParams(window.location.search);
    const token = params.get("reset") || params.get("reset_token");
    if (token) openReset(token);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
