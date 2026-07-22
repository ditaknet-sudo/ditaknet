(function () {
  "use strict";

  const configEl = document.getElementById("backups-config");
  let config = {};
  try {
    config = JSON.parse(configEl ? configEl.textContent || "{}" : "{}");
  } catch (_) {
    config = {};
  }
  const i18n = config.i18n || {};

  let restoreModal = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character]));
  }

  function showAlert(message, type) {
    const el = document.getElementById("backups-alert");
    if (!el) return;
    el.className = `alert alert-${type || "info"}`;
    el.textContent = message;
    el.classList.remove("d-none");
  }

  function formatTime(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 19);
  }

  async function api(method, url, body) {
    const opts = { method, credentials: "same-origin", headers: {} };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await (window.ditaknetFetch || fetch)(url, opts);
    if (method === "GET" || resp.headers.get("content-type")?.includes("json")) {
      return resp.json();
    }
    return resp;
  }

  async function loadBackups() {
    const tbody = document.getElementById("backups-table-body");
    if (!tbody) return;
    try {
      const data = await api("GET", "/api/backups");
      const backups = data.backups || [];
      if (!backups.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-muted text-center py-4">${escapeHtml(i18n.no_backups || "No backups")}</td></tr>`;
        return;
      }
      tbody.innerHTML = backups
        .map(
          (b) => `<tr>
            <td class="font-monospace small">${escapeHtml(b.filename)}</td>
            <td>${escapeHtml(b.backup_type || "—")}</td>
            <td>${escapeHtml(b.app_version || "—")}</td>
            <td class="small">${escapeHtml(formatTime(b.created_at))}</td>
            <td>${escapeHtml(b.size_display || b.size_bytes)}</td>
            <td class="small">${escapeHtml(b.includes_summary || "—")}</td>
            <td><span class="badge bg-success">${escapeHtml(b.status || "ready")}</span></td>
            <td class="text-end text-nowrap">
              <a class="btn btn-sm btn-outline-primary" href="/api/backups/${encodeURIComponent(b.filename)}/download">${escapeHtml(i18n.download || "Download")}</a>
              <button type="button" class="btn btn-sm btn-outline-warning" data-restore="${escapeHtml(b.filename)}">${escapeHtml(i18n.restore || "Restore")}</button>
              <button type="button" class="btn btn-sm btn-outline-danger" data-delete="${escapeHtml(b.filename)}">${escapeHtml(i18n.delete || "Delete")}</button>
            </td>
          </tr>`
        )
        .join("");
      tbody.querySelectorAll("[data-restore]").forEach((btn) => {
        btn.addEventListener("click", () => openRestore(btn.getAttribute("data-restore")));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => deleteBackup(btn.getAttribute("data-delete")));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-danger text-center py-4">${escapeHtml(err.message || "Error")}</td></tr>`;
    }
  }

  async function openRestore(filename) {
    const box = document.getElementById("restore-validation");
    const command = document.getElementById("restore-command");
    if (box) {
      box.textContent = "…";
      if (command) command.textContent = "";
      try {
        const v = await api("POST", `/api/backups/${encodeURIComponent(filename)}/validate`);
        box.textContent = `${i18n.validate_ok || "Valid"} — ${v.backup_type || "backup"}, v${v.app_version || "?"}`;
        box.className = "mb-3 small alert alert-info py-2";
        if (command) command.textContent = v.offline_restore_command || "";
      } catch (err) {
        box.textContent = err.message || "Validation failed";
        box.className = "mb-3 small alert alert-danger py-2";
      }
    }
    if (!restoreModal && window.bootstrap) {
      restoreModal = new bootstrap.Modal(document.getElementById("restore-modal"));
    }
    restoreModal?.show();
  }

  async function deleteBackup(filename) {
    if (!window.confirm(i18n.delete_confirm || "Delete this backup?")) return;
    try {
      await api("DELETE", `/api/backups/${encodeURIComponent(filename)}`);
      loadBackups();
    } catch (err) {
      showAlert(err.message, "danger");
    }
  }

  document.getElementById("btn-create-backup")?.addEventListener("click", async () => {
    try {
      const data = await api("POST", "/api/backups/create");
      showAlert(`Backup created: ${data.filename}`, "success");
      loadBackups();
    } catch (err) {
      showAlert(err.message || "Backup failed", "danger");
    }
  });

  document.getElementById("btn-upload-backup")?.addEventListener("click", () => {
    document.getElementById("backup-upload-input")?.click();
  });

  document.getElementById("backup-upload-input")?.addEventListener("change", async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const resp = await fetch("/api/backups/upload", { method: "POST", body: form, credentials: "same-origin" });
      if (!resp.ok) throw new Error("Upload failed");
      const data = await resp.json();
      showAlert(`Uploaded: ${data.filename}`, "success");
      loadBackups();
    } catch (err) {
      showAlert(err.message || "Upload failed", "danger");
    }
    ev.target.value = "";
  });

  loadBackups();
})();
