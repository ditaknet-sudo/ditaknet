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
  let currentStatus = {};
  const allowedExternalHosts = new Set([
    "github.com",
    "raw.githubusercontent.com",
    "ghcr.io",
  ]);

  function trustedExternalUrl(value) {
    if (!value || typeof value !== "string") return "";
    try {
      const parsed = new URL(value);
      const hostname = parsed.hostname.toLowerCase();
      if (
        parsed.protocol !== "https:" ||
        parsed.username ||
        parsed.password ||
        parsed.port ||
        !allowedExternalHosts.has(hostname)
      ) {
        return "";
      }
      return parsed.href;
    } catch (_) {
      return "";
    }
  }

  async function requireOk(response, fallbackMessage) {
    if (response && response.ok) return response;

    let message = fallbackMessage || "Request failed";
    if (response) {
      try {
        const payload = await response.json();
        if (payload.detail && typeof payload.detail === "object") {
          message = payload.detail.message || payload.detail.code || message;
        } else {
          message = payload.detail || payload.message || message;
        }
      } catch (_) {
        // Keep the local fallback when the server did not return JSON.
      }
    }
    throw new Error(String(message));
  }

  function replaceWithBadge(container, text, extraClasses) {
    const badge = document.createElement("span");
    badge.className = `badge ${extraClasses}`;
    badge.textContent = text;
    container.replaceChildren(badge);
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const display = value && String(value).trim() ? String(value) : "—";
    el.textContent = display;
  }

  function setCopyEnabled(enabled) {
    document.querySelectorAll("[data-copy-target]").forEach((button) => {
      button.disabled = !enabled;
    });
  }

  function resetPreflight() {
    const required = i18n.preflight_required || "Complete update preflight first";
    ["docker-compose-cmd", "docker-pull-cmd", "truenas-notes", "rollback-cmd"].forEach(
      (id) => {
        const field = document.getElementById(id);
        if (field) field.value = required;
      }
    );
    setCopyEnabled(false);
    const result = document.getElementById("update-preflight-result");
    if (result) result.textContent = required;
    const badge = document.getElementById("upd-preflight-badge");
    if (badge) replaceWithBadge(badge, required, "bg-warning text-dark");
  }

  function receiptMatchesStatus(receipt) {
    return Boolean(
      receipt &&
        !receipt.expired &&
        receipt.status === "ready" &&
        receipt.target_version === currentStatus.latest_version &&
        receipt.image_digest === currentStatus.image_digest
    );
  }

  function renderPreflightReceipt(receipt) {
    if (!receiptMatchesStatus(receipt)) return false;
    const commands = receipt.commands || {};
    const values = {
      "docker-compose-cmd": commands.docker_compose || "",
      "docker-pull-cmd": commands.docker_pull || "",
      "truenas-notes": Array.isArray(commands.truenas)
        ? commands.truenas.join("\n")
        : commands.truenas || "",
      "rollback-cmd": commands.rollback || "",
    };
    Object.entries(values).forEach(([id, value]) => {
      const field = document.getElementById(id);
      if (field) field.value = value;
    });
    setCopyEnabled(true);

    const backup = receipt.backup || {};
    const result = document.getElementById("update-preflight-result");
    if (result) {
      result.textContent = `${i18n.preflight_ready || "Update handoff ready"}. ${
        i18n.backup_receipt || "Validated backup"
      }: ${backup.filename || "—"} (${backup.sha256 || "—"})`;
    }
    const badge = document.getElementById("upd-preflight-badge");
    if (badge) {
      replaceWithBadge(
        badge,
        i18n.preflight_ready || "Validated and ready",
        "bg-success"
      );
    }
    return true;
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

  function renderNoticeItems(container, items, heading) {
    items.forEach((rawItem) => {
      const item = rawItem && typeof rawItem === "object" ? rawItem : {};
      const block = document.createElement("div");
      block.className = `alert alert-${noticeLevelClass(item.level)} mb-2`;

      const title = document.createElement("div");
      title.className = "fw-semibold";
      title.textContent = item.title || heading;
      block.appendChild(title);

      const message = document.createElement("div");
      message.className = "small";
      message.appendChild(document.createTextNode(item.message || ""));

      const url = trustedExternalUrl(item.url);
      if (url) {
        message.appendChild(document.createTextNode(" "));
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.className = "alert-link";
        link.textContent = i18n.release_notes || "Details";
        message.appendChild(link);
      }

      block.appendChild(message);
      container.appendChild(block);
    });
  }

  function renderNotices(data) {
    const card = document.getElementById("updates-notices-card");
    const body = document.getElementById("updates-notices-body");
    if (!card || !body) return;

    const announcements = Array.isArray(data.announcements) ? data.announcements : [];
    const promotions = Array.isArray(data.promotions) ? data.promotions : [];
    const releaseNotes = data.release_notes_text || "";
    const manifestMessage = data.manifest_message || "";
    const hasNotices = Boolean(
      announcements.length || promotions.length || releaseNotes || manifestMessage
    );

    if (!hasNotices) {
      card.hidden = true;
      body.replaceChildren();
      return;
    }

    body.replaceChildren();
    if (manifestMessage) {
      const message = document.createElement("div");
      message.className = "alert alert-primary mb-2";
      message.textContent = manifestMessage;
      body.appendChild(message);
    }
    if (releaseNotes) {
      const notes = document.createElement("div");
      notes.className = "mb-3";
      const heading = document.createElement("div");
      heading.className = "text-muted small mb-1";
      heading.textContent = i18n.release_notes || "Release notes";
      const content = document.createElement("div");
      content.className = "small";
      content.textContent = releaseNotes;
      notes.append(heading, content);
      body.appendChild(notes);
    }
    if (promotions.length) {
      const heading = document.createElement("div");
      heading.className = "mb-2 text-muted small fw-semibold";
      heading.textContent = i18n.promotions || "Promotions";
      body.appendChild(heading);
      renderNoticeItems(body, promotions, i18n.promotions || "Promotion");
    }
    if (announcements.length) {
      const heading = document.createElement("div");
      heading.className = "mb-2 text-muted small fw-semibold";
      heading.textContent = i18n.announcements || "Announcements";
      body.appendChild(heading);
      renderNoticeItems(body, announcements, i18n.announcements || "Announcement");
    }

    card.hidden = false;
  }

  function renderStatus(data) {
    data = data || {};
    currentStatus = data;
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
    setText("upd-image-digest", data.image_digest);
    setText(
      "upd-schema-revision",
      data.compatibility && data.compatibility.target_schema_revision
    );

    const trustBadge = document.getElementById("upd-trust-badge");
    if (trustBadge) {
      replaceWithBadge(
        trustBadge,
        data.manifest_trusted
          ? i18n.trusted || "Signed / trusted"
          : i18n.untrusted || "Untrusted",
        data.manifest_trusted ? "bg-success" : "bg-danger"
      );
    }

    resetPreflight();
    const confirmation = document.getElementById("update-confirmation");
    const preflightButton = document.getElementById("btn-update-preflight");
    const expectedConfirmation = data.latest_version
      ? `UPDATE ${data.latest_version}`
      : "UPDATE x.y.z";
    if (confirmation) {
      confirmation.value = "";
      confirmation.placeholder = expectedConfirmation;
      confirmation.dataset.expected = expectedConfirmation;
      confirmation.disabled = !data.update_handoff_available;
    }
    if (preflightButton) preflightButton.disabled = true;

    const badge = document.getElementById("upd-available-badge");
    if (badge) {
      if (!data.source_configured) {
        replaceWithBadge(
          badge,
          i18n.update_source_not_configured || "Not configured",
          "bg-secondary"
        );
        showAlert(i18n.update_source_not_configured || "Update source is not configured.", "warning");
      } else if (data.error_message || data.source === "error") {
        replaceWithBadge(badge, i18n.check_failed || "Check failed", "bg-warning text-dark");
        showAlert(i18n.check_failed || "Could not check updates", "warning");
      } else if (data.update_available) {
        replaceWithBadge(badge, i18n.update_available || "Update available", "bg-success");
      } else if (data.customer_notice_available) {
        replaceWithBadge(
          badge,
          i18n.notice_available || "Notice available",
          "bg-info text-dark"
        );
      } else {
        replaceWithBadge(badge, i18n.no_update_available || "Up to date", "bg-secondary");
      }
    }

    const notesEl = document.getElementById("upd-release-notes");
    const url = trustedExternalUrl(data.release_notes_url || data.release_url || "");
    if (notesEl) {
      notesEl.replaceChildren();
      if (url) {
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = i18n.release_notes || "Release notes";
        notesEl.appendChild(link);
      } else if (data.release_notes_text) {
        const text = document.createElement("span");
        text.className = "small";
        text.textContent = data.release_notes_text;
        notesEl.appendChild(text);
      } else {
        const empty = document.createElement("span");
        empty.className = "text-muted";
        empty.textContent = "—";
        notesEl.appendChild(empty);
      }
    }

    renderNotices(data);

    window._updatesReleaseUrl = url;
  }

  async function loadLastReceipt() {
    if (!currentStatus.update_handoff_available) return;
    try {
      const response = await (window.ditaknetFetch || fetch)(
        "/api/system/update-preflight",
        { method: "GET", credentials: "same-origin" }
      );
      const checked = await requireOk(response, i18n.preflight_failed || "Preflight failed");
      const data = await checked.json();
      renderPreflightReceipt(data.receipt);
    } catch (_) {
      // A missing/expired/admin-only receipt simply keeps commands locked.
    }
  }

  async function loadStatus(force) {
    const url = force ? "/api/system/check-updates" : "/api/system/update-status";
    const method = force ? "POST" : "GET";
    try {
      const resp = await (window.ditaknetFetch || fetch)(url, { method, credentials: "same-origin" });
      const checkedResponse = await requireOk(
        resp,
        i18n.check_failed || "Could not load update status"
      );
      const data = await checkedResponse.json();
      renderStatus(data);
      await loadLastReceipt();
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
      const response = await (window.ditaknetFetch || fetch)("/api/system/update-preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ enabled }),
      });
      await requireOk(response, i18n.check_failed || "Could not save preference");
      await loadStatus(false);
    } catch (err) {
      showAlert(err.message || i18n.check_failed || "Could not save preference", "danger");
      ev.target.checked = !enabled;
    }
  });
  document.getElementById("btn-release-notes")?.addEventListener("click", () => {
    if (window._updatesReleaseUrl) {
      const opened = window.open(window._updatesReleaseUrl, "_blank", "noopener,noreferrer");
      if (opened) opened.opener = null;
    }
  });
  document.getElementById("update-confirmation")?.addEventListener("input", (event) => {
    const button = document.getElementById("btn-update-preflight");
    if (!button) return;
    const expected = event.target.dataset.expected || "";
    button.disabled =
      !currentStatus.update_handoff_available || event.target.value !== expected;
  });
  document.getElementById("btn-update-preflight")?.addEventListener("click", async () => {
    const confirmation = document.getElementById("update-confirmation");
    const button = document.getElementById("btn-update-preflight");
    if (!confirmation || !currentStatus.latest_version) return;
    if (button) button.disabled = true;
    try {
      const response = await (window.ditaknetFetch || fetch)(
        "/api/system/update-preflight",
        {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            target_version: currentStatus.latest_version,
            confirmation: confirmation.value,
          }),
        }
      );
      const checked = await requireOk(
        response,
        i18n.preflight_failed || "Update preflight failed"
      );
      const receipt = await checked.json();
      if (!renderPreflightReceipt(receipt)) {
        throw new Error(i18n.preflight_failed || "Update preflight result is stale");
      }
      showAlert(i18n.preflight_ready || "Validated update handoff is ready", "success");
    } catch (err) {
      resetPreflight();
      showAlert(err.message || i18n.preflight_failed || "Update preflight failed", "danger");
    } finally {
      if (button) {
        button.disabled = confirmation.value !== confirmation.dataset.expected;
      }
    }
  });
  document.getElementById("btn-backup-before-update")?.addEventListener("click", async () => {
    try {
      const resp = await (window.ditaknetFetch || fetch)("/api/backups/create", {
        method: "POST",
        credentials: "same-origin",
      });
      const checkedResponse = await requireOk(resp, "Backup failed");
      const data = await checkedResponse.json();
      showAlert(`Backup created: ${data.filename}`, "success");
    } catch (err) {
      showAlert(err.message || "Backup failed", "danger");
    }
  });

  document.querySelectorAll("[data-copy-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      const input = document.getElementById(btn.getAttribute("data-copy-target"));
      if (input && navigator.clipboard) {
        navigator.clipboard.writeText(input.value || "");
      }
    });
  });

  loadStatus(false);
})();
