(function () {
  "use strict";

  const configEl = document.getElementById("updates-config");
  let config = {};
  try {
    config = JSON.parse(configEl ? configEl.textContent || "{}" : "{}");
  } catch (_) {
    config = {};
  }
  const i18n = config.i18n || {};

  function setText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const display = value && String(value).trim() ? String(value) : "—";
    el.textContent = display;
  }

  function showAlert(message, type) {
    const el = document.getElementById("updates-alert");
    if (!el) return;
    el.className = `alert alert-${type || "info"}`;
    el.textContent = message;
    el.classList.remove("d-none");
  }

  function noticeLevelClass(level) {
    const normalized = String(level || "info").toLowerCase();
    if (normalized === "success") return "success";
    if (normalized === "warning") return "warning";
    if (normalized === "error" || normalized === "danger") return "danger";
    return "info";
  }

  function renderNoticeItems(items, heading) {
    if (!items || !items.length) return "";
    const blocks = items.map((item) => {
      const title = item.title || heading;
      const message = item.message || "";
      const url = item.url || "";
      const level = noticeLevelClass(item.level);
      const link = url
        ? ` <a href="${url}" target="_blank" rel="noopener noreferrer" class="alert-link">${i18n.release_notes || "Details"}</a>`
        : "";
      return `<div class="alert alert-${level} mb-2"><div class="fw-semibold">${title}</div><div class="small">${message}${link}</div></div>`;
    });
    return blocks.join("");
  }

  function renderNotices(data) {
    const card = document.getElementById("updates-notices-card");
    const body = document.getElementById("updates-notices-body");
    if (!card || !body) return;

    const announcements = data.announcements || [];
    const promotions = data.promotions || [];
    const releaseNotes = data.release_notes_text || "";
    const manifestMessage = data.manifest_message || "";
    const hasNotices = Boolean(
      announcements.length || promotions.length || releaseNotes || manifestMessage
    );

    if (!hasNotices) {
      card.hidden = true;
      body.innerHTML = "";
      return;
    }

    let html = "";
    if (manifestMessage) {
      html += `<div class="alert alert-primary mb-2">${manifestMessage}</div>`;
    }
    if (releaseNotes) {
      html += `<div class="mb-3"><div class="text-muted small mb-1">${i18n.release_notes || "Release notes"}</div><div class="small">${releaseNotes}</div></div>`;
    }
    if (promotions.length) {
      html += `<div class="mb-2 text-muted small fw-semibold">${i18n.promotions || "Promotions"}</div>`;
      html += renderNoticeItems(promotions, i18n.promotions || "Promotion");
    }
    if (announcements.length) {
      html += `<div class="mb-2 text-muted small fw-semibold">${i18n.announcements || "Announcements"}</div>`;
      html += renderNoticeItems(announcements, i18n.announcements || "Announcement");
    }

    body.innerHTML = html;
    card.hidden = false;
  }

  function renderStatus(data) {
    data = data || {};
    setText("upd-current-version", data.current_version);
    setText("upd-image-tag", data.current_image_tag);
    setText("upd-build-commit", data.build_commit);
    setText("upd-build-date", data.build_date);
    setText("upd-channel", data.update_channel);
    setText("upd-github-repo", data.github_repository || data.github_repo_url);
    setText("upd-ghcr-image", data.ghcr_image || config.ghcr_image);
    const checked = data.last_checked || data.checked_at || "";
    setText("upd-checked-at", checked.replace("T", " ").slice(0, 19));
    setText("upd-latest-version", data.latest_version);

    const badge = document.getElementById("upd-available-badge");
    if (badge) {
      if (!data.source_configured) {
        badge.innerHTML = `<span class="badge bg-secondary">${i18n.update_source_not_configured || "Not configured"}</span>`;
        showAlert(i18n.update_source_not_configured || "Update source is not configured.", "warning");
      } else if (data.error_message || data.source === "error") {
        badge.innerHTML = `<span class="badge bg-warning text-dark">${i18n.check_failed || "Check failed"}</span>`;
        showAlert(i18n.check_failed || "Could not check updates", "warning");
      } else if (data.update_available) {
        badge.innerHTML = `<span class="badge bg-success">${i18n.update_available || "Update available"}</span>`;
      } else if (data.customer_notice_available) {
        badge.innerHTML = `<span class="badge bg-info text-dark">${i18n.notice_available || "Notice available"}</span>`;
      } else {
        badge.innerHTML = `<span class="badge bg-secondary">${i18n.no_update_available || "Up to date"}</span>`;
      }
    }

    const notesEl = document.getElementById("upd-release-notes");
    const url = data.release_notes_url || data.release_url || "";
    if (notesEl) {
      if (url) {
        notesEl.innerHTML = `<a href="${url}" target="_blank" rel="noopener noreferrer">${i18n.release_notes || "Release notes"}</a>`;
      } else if (data.release_notes_text) {
        notesEl.innerHTML = `<span class="small">${data.release_notes_text}</span>`;
      } else {
        notesEl.innerHTML = '<span class="text-muted">—</span>';
      }
    }

    renderNotices(data);

    const pull = document.getElementById("docker-pull-cmd");
    if (pull) {
      const image = data.docker_image || data.ghcr_image || config.ghcr_image;
      if (image) pull.value = `docker pull ${image}`;
    }
    const compose = document.getElementById("docker-compose-cmd");
    if (compose && data.latest_version) {
      const image = data.docker_image || `ghcr.io/ditaknet-sudo/ditaknet:${data.latest_version}`;
      compose.value = [
        `docker pull ${image}`,
        `# set DITAKNET_VERSION=${data.latest_version} (exact tag, not latest)`,
        "docker compose up -d",
        "curl -fsS http://127.0.0.1:5833/health",
      ].join("\n");
    }
    window._updatesReleaseUrl = url;
  }

  async function loadStatus(force) {
    const url = force ? "/api/system/check-updates" : "/api/system/update-status";
    const method = force ? "POST" : "GET";
    try {
      const resp = await (window.ditaknetFetch || fetch)(url, { method, credentials: "same-origin" });
      const data = await resp.json();
      renderStatus(data);
      if (force && window.ditaknetRefreshNotificationBell) {
        window.ditaknetRefreshNotificationBell();
      }
    } catch (err) {
      showAlert(err.message || i18n.check_failed || "Could not load update status", "danger");
    }
  }

  document.getElementById("btn-check-updates")?.addEventListener("click", () => loadStatus(true));
  document.getElementById("upd-check-enabled")?.addEventListener("change", async (ev) => {
    const enabled = Boolean(ev.target.checked);
    try {
      await (window.ditaknetFetch || fetch)("/api/system/update-preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ enabled }),
      });
      await loadStatus(false);
    } catch (err) {
      showAlert(err.message || i18n.check_failed || "Could not save preference", "danger");
      ev.target.checked = !enabled;
    }
  });
  document.getElementById("btn-release-notes")?.addEventListener("click", () => {
    if (window._updatesReleaseUrl) window.open(window._updatesReleaseUrl, "_blank");
  });
  document.getElementById("btn-backup-before-update")?.addEventListener("click", async () => {
    try {
      const resp = await (window.ditaknetFetch || fetch)("/api/backups/create", {
        method: "POST",
        credentials: "same-origin",
      });
      const data = await resp.json();
      showAlert(`Backup created: ${data.filename}`, "success");
    } catch (err) {
      showAlert(err.message || "Backup failed", "danger");
    }
  });

  document.querySelectorAll("[data-copy-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.getAttribute("data-copy-target"));
      if (input && navigator.clipboard) {
        navigator.clipboard.writeText(input.value || "");
      }
    });
  });

  loadStatus(false);
})();
