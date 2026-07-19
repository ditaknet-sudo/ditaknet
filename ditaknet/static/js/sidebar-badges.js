(function () {
  "use strict";

  const REASON_I18N_PREFIX = "nav_status_";

  function loadI18n() {
    const el = document.getElementById("nav-status-i18n");
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (_) {
      return {};
    }
  }

  function reasonLabel(reason, i18n) {
    const key = REASON_I18N_PREFIX + reason;
    return i18n[key] || reason.replace(/_/g, " ");
  }

  function applyBadge(link, block, i18n) {
    const badge = link.querySelector("[data-sidebar-badge]");
    if (!badge || !block) return;

    const level = block.level || "healthy";
    const count = Number(block.count || 0);
    const reasons = block.reasons || [];

    badge.classList.remove("d-none", "is-dot", "is-count", "is-critical", "is-warning", "is-info");
    badge.textContent = "";
    badge.removeAttribute("title");

    if (level === "healthy" || count <= 0) {
      badge.classList.add("d-none");
      badge.setAttribute("aria-hidden", "true");
      return;
    }

    const labels = reasons.map((r) => reasonLabel(r, i18n));
    const title = labels.join(" | ");
    badge.setAttribute("title", title);
    badge.setAttribute("aria-label", title);
    badge.removeAttribute("aria-hidden");

    if (link.getAttribute("data-nav-status") === "notifications" && count > 0) {
      badge.classList.add("is-count", "is-info");
      badge.textContent = count > 99 ? "99+" : String(count);
      return;
    }

    badge.classList.add("is-dot");
    if (level === "critical") {
      badge.classList.add("is-critical");
    } else if (level === "warning") {
      badge.classList.add("is-warning");
    } else {
      badge.classList.add("is-info");
    }
  }

  async function refreshSidebarBadges() {
    const i18n = loadI18n();
    const links = document.querySelectorAll("[data-nav-status]");
    if (!links.length) return;

    try {
      const resp = await (window.ditaknetFetch || fetch)("/api/navigation/status", {
        credentials: "same-origin",
      });
      if (!resp.ok) return;
      const data = await resp.json();
      links.forEach((link) => {
        const key = link.getAttribute("data-nav-status");
        if (key && data[key]) {
          applyBadge(link, data[key], i18n);
        }
      });
    } catch (_) {
      /* silent — badges are optional enhancement */
    }
  }

  window.ditaknetRefreshSidebarBadges = refreshSidebarBadges;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshSidebarBadges);
  } else {
    refreshSidebarBadges();
  }

  setInterval(refreshSidebarBadges, 60000);
})();
