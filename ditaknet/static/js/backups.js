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

  let restoreFilename = "";
  let restoreModal = null;

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
        tbody.innerHTML = `<tr><td colspan="8" class="text-muted text-center py-4">${i18n.no_backups || "No backups"}</td></tr>`;
        return;
      }
      tbody.innerHTML = backups
        .map(
          (b) => `<tr>
            <td class="font-monospace small">${b.filename}</td>
            <td>${b.backup_type || "—"}</td>
            <td>${b.app_version || "—"}</td>
            <td class="small">${formatTime(b.created_at)}</td>
            <td>${b.size_display || b.size_bytes}</td>
            <td class="small">${b.includes_summary || "—"}</td>
            <td><span class="badge bg-success">${b.status || "ready"}</span></td>
            <td class="text-end text-nowrap">
              <a class="btn btn-sm btn-outline-primary" href="/api/backups/${encodeURIComponent(b.filename)}/download">${i18n.download || "Download"}</a>
              <button type="button" class="btn btn-sm btn-outline-warning" data-restore="${b.filename}">${i18n.restore || "Restore"}</button>
              <button type="button" class="btn btn-sm btn-outline-danger" data-delete="${b.filename}">${i18n.delete || "Delete"}</button>
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
      tbody.innerHTML = `<tr><td colspan="8" class="text-danger text-center py-4">${err.message || "Error"}</td></tr>`;
    }
  }

  async function openRestore(filename) {
    restoreFilename = filename;
    const box = document.getElementById("restore-validation");
    if (box) {
      box.innerHTML = "…";
      try {
        const v = await api("POST", `/api/backups/${encodeURIComponent(filename)}/validate`);
        box.innerHTML = `<div class="alert alert-info mb-0 py-2"><strong>${i18n.validate_ok || "Valid"}</strong> — ${v.backup_type}, v${v.app_version || "?"}</div>`;
      } catch (err) {
        box.innerHTML = `<div class="alert alert-danger mb-0 py-2">${err.message}</div>`;
      }
    }
    document.getElementById("restore-confirm").checked = false;
    if (!restoreModal && window.bootstrap) {
      restoreModal = new bootstrap.Modal(document.getElementById("restore-modal"));
    }
    restoreModal?.show();
  }

  async function submitRestore() {
    if (!document.getElementById("restore-confirm")?.checked) {
      showAlert("Confirmation required", "warning");
      return;
    }
    const mode = document.getElementById("restore-mode")?.value || "full_restore";
    const body = { mode, confirm: true };
    if (mode === "full_restore_reset_admin") {
      body.new_admin_username = document.getElementById("restore-admin-user")?.value?.trim();
      body.new_admin_password = document.getElementById("restore-admin-pass")?.value || "";
    }
    try {
      await api("POST", `/api/backups/${encodeURIComponent(restoreFilename)}/restore`, body);
      showAlert(i18n.restore_ok || "Restore completed", "success");
      restoreModal?.hide();
      loadBackups();
    } catch (err) {
      showAlert(err.message || "Restore failed", "danger");
    }
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

  document.getElementById("btn-restore-backup")?.addEventListener("click", () => {
    document.getElementById("backup-upload-input")?.click();
  });

  document.getElementById("restore-mode")?.addEventListener("change", (ev) => {
    const show = ev.target.value === "full_restore_reset_admin";
    document.getElementById("restore-admin-fields")?.classList.toggle("d-none", !show);
  });

  document.getElementById("restore-submit-btn")?.addEventListener("click", submitRestore);

  loadBackups();
})();
