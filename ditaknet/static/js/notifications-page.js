(function () {
  "use strict";

  const configEl = document.getElementById("notifications-page-config");
  let config = {};
  try {
    config = JSON.parse(configEl ? configEl.textContent || "{}" : "{}");
  } catch (_) {
    config = {};
  }
  const i18n = config.i18n || {};

  function levelClass(level) {
    const map = { info: "info", warning: "warning", error: "danger", critical: "danger", success: "success" };
    return map[level] || "secondary";
  }

  async function loadNotifications() {
    const list = document.getElementById("notifications-list");
    if (!list) return;
    try {
      const resp = await (window.ditaknetFetch || fetch)("/api/notifications", { credentials: "same-origin" });
      const data = await resp.json();
      const items = data.notifications || [];
      if (!items.length) {
        list.innerHTML = `<div class="list-group-item text-muted text-center py-5">${i18n.empty || "No notifications"}</div>`;
        return;
      }
      list.innerHTML = items
        .map(
          (n) => `<div class="list-group-item ${n.unread ? "bg-light" : ""}">
            <div class="d-flex justify-content-between align-items-start gap-2">
              <div>
                <span class="badge bg-${levelClass(n.level)} me-1">${n.level}</span>
                <strong>${n.title}</strong>
                <div class="small text-muted mt-1">${(n.created_at || "").replace("T", " ").slice(0, 19)} · ${n.category}</div>
                <div class="small mt-1">${n.message}</div>
                ${n.action_url ? `<a href="${n.action_url}" class="small">${n.action_url}</a>` : ""}
              </div>
              <div class="btn-group btn-group-sm flex-shrink-0">
                ${n.unread ? `<button type="button" class="btn btn-outline-secondary" data-read="${n.id}">${i18n.mark_as_read || "Mark read"}</button>` : ""}
                <button type="button" class="btn btn-outline-secondary" data-dismiss="${n.id}">${i18n.dismiss || "Dismiss"}</button>
              </div>
            </div>
          </div>`
        )
        .join("");
      list.querySelectorAll("[data-read]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await fetch(`/api/notifications/${btn.getAttribute("data-read")}/read`, { method: "POST", credentials: "same-origin" });
          loadNotifications();
          window.ditaknetRefreshNotificationBell?.();
        });
      });
      list.querySelectorAll("[data-dismiss]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          await fetch(`/api/notifications/${btn.getAttribute("data-dismiss")}/dismiss`, { method: "POST", credentials: "same-origin" });
          loadNotifications();
          window.ditaknetRefreshNotificationBell?.();
        });
      });
    } catch (err) {
      list.innerHTML = `<div class="list-group-item text-danger text-center py-4">${err.message}</div>`;
    }
  }

  document.getElementById("btn-mark-all-read")?.addEventListener("click", async () => {
    await fetch("/api/notifications/read-all", { method: "POST", credentials: "same-origin" });
    loadNotifications();
    window.ditaknetRefreshNotificationBell?.();
  });

  loadNotifications();
})();
