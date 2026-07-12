(function () {
  "use strict";

  var STORAGE_KEY = "ditaknet-sidebar-collapsed";

  function showGlobalError(message, requestId) {
    var host = document.getElementById("global-error-alert");
    if (!host) return;
    host.classList.remove("d-none");
    var msgEl = host.querySelector("[data-error-message]");
    if (msgEl) msgEl.textContent = message || "Something went wrong.";
    var ridEl = host.querySelector("[data-error-request-id]");
    if (ridEl) ridEl.textContent = requestId || "—";
  }

  window.ditaknetFetch = async function (url, options) {
    options = options || {};
    var resp = await fetch(url, Object.assign({ credentials: "same-origin" }, options));
    if (!resp.ok) {
      var payload = null;
      try {
        payload = await resp.json();
      } catch (err) {
        payload = null;
      }
      var requestId = resp.headers.get("X-Request-ID") || (payload && payload.request_id) || null;
      var message =
        (payload && (payload.message || payload.detail)) ||
        ("Request failed (" + resp.status + ")");
      showGlobalError(message, requestId);
      var error = new Error(message);
      error.status = resp.status;
      error.requestId = requestId;
      error.payload = payload;
      throw error;
    }
    return resp;
  };

  document.querySelectorAll("[data-copy-request-id]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var code = document.querySelector("[data-error-request-id]");
      if (code && navigator.clipboard) {
        navigator.clipboard.writeText(code.textContent || "");
      }
    });
  });

  document.querySelectorAll("[data-confirm]").forEach(function (el) {
    el.addEventListener("click", function (event) {
      var message = el.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll(".alert[data-auto-dismiss]").forEach(function (alert) {
    window.setTimeout(function () {
      if (typeof bootstrap !== "undefined" && bootstrap.Alert) {
        bootstrap.Alert.getOrCreateInstance(alert).close();
      } else {
        alert.remove();
      }
    }, 5000);
  });

  var shell = document.getElementById("app-shell");
  var sidebar = document.getElementById("app-sidebar");
  var collapseBtn = document.getElementById("sidebar-collapse-btn");
  var mobileBtn = document.getElementById("sidebar-mobile-btn");
  var backdrop = document.getElementById("sidebar-backdrop");

  if (!shell || !sidebar) {
    return;
  }

  function isMobile() {
    return window.matchMedia("(max-width: 992px)").matches;
  }

  function collapseLabels() {
    return {
      collapse: collapseBtn?.getAttribute("data-label-collapse") || "Collapse sidebar",
      expand: collapseBtn?.getAttribute("data-label-expand") || "Expand sidebar",
    };
  }

  function updateCollapseAria(collapsed) {
    if (!collapseBtn) {
      return;
    }
    var labels = collapseLabels();
    collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    collapseBtn.setAttribute("aria-label", collapsed ? labels.expand : labels.collapse);
    collapseBtn.title = collapsed ? labels.expand : labels.collapse;
  }

  function setCollapsed(collapsed) {
    shell.classList.toggle("sidebar-collapsed", collapsed && !isMobile());
    updateCollapseAria(collapsed && !isMobile());
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch (err) {
      /* ignore */
    }
  }

  function isCollapsed() {
    return shell.classList.contains("sidebar-collapsed");
  }

  function openMobile() {
    shell.classList.add("sidebar-mobile-open");
    if (backdrop) {
      backdrop.hidden = false;
      backdrop.classList.add("is-visible");
    }
    document.body.style.overflow = "hidden";
  }

  function closeMobile() {
    shell.classList.remove("sidebar-mobile-open");
    if (backdrop) {
      backdrop.classList.remove("is-visible");
      window.setTimeout(function () {
        if (!shell.classList.contains("sidebar-mobile-open")) {
          backdrop.hidden = true;
        }
      }, 250);
    }
    document.body.style.overflow = "";
  }

  function restoreCollapsedState() {
    if (isMobile()) {
      shell.classList.remove("sidebar-collapsed");
      updateCollapseAria(false);
      return;
    }
    var saved = null;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      saved = null;
    }
    var collapsed = saved === "1";
    setCollapsed(collapsed);
  }

  collapseBtn?.addEventListener("click", function () {
    if (isMobile()) {
      closeMobile();
      return;
    }
    setCollapsed(!isCollapsed());
  });

  mobileBtn?.addEventListener("click", function () {
    if (shell.classList.contains("sidebar-mobile-open")) {
      closeMobile();
    } else {
      openMobile();
    }
  });

  backdrop?.addEventListener("click", closeMobile);

  sidebar.querySelectorAll(".sidebar-nav-item, .sidebar-link, .sidebar-logout").forEach(function (link) {
    link.addEventListener("click", function () {
      if (isMobile()) {
        closeMobile();
      }
    });
  });

  window.addEventListener("resize", function () {
    if (isMobile()) {
      shell.classList.remove("sidebar-collapsed");
      updateCollapseAria(false);
      closeMobile();
    } else {
      restoreCollapsedState();
      closeMobile();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMobile();
    }
  });

  restoreCollapsedState();
})();

(function () {
  "use strict";

  async function refreshNotificationBell() {
    var badge = document.getElementById("notification-unread-badge");
    var list = document.getElementById("notification-dropdown-list");
    if (!badge && !list) return;
    try {
      var countResp = await fetch("/api/notifications/unread-count", { credentials: "same-origin" });
      if (!countResp.ok) return;
      var countData = await countResp.json();
      var count = Number(countData.count) || 0;
      if (badge) {
        badge.textContent = String(count);
        badge.classList.toggle("d-none", count <= 0);
      }
      if (list) {
        var itemsResp = await fetch("/api/notifications", { credentials: "same-origin" });
        if (!itemsResp.ok) return;
        var itemsData = await itemsResp.json();
        var items = (itemsData.notifications || []).slice(0, 8);
        if (!items.length) {
          list.innerHTML = '<div class="text-muted small text-center py-3">No notifications</div>';
          return;
        }
        list.innerHTML = items
          .map(function (n) {
            var unread = n.unread ? "fw-semibold" : "text-muted";
            return (
              '<a class="dropdown-item small py-2 ' +
              unread +
              '" href="' +
              (n.action_url || "/notifications") +
              '"><div>' +
              (n.title || "") +
              '</div><div class="text-muted" style="font-size:.75rem">' +
              (n.message || "").slice(0, 80) +
              "</div></a>"
            );
          })
          .join("");
      }
    } catch (err) {
      /* ignore bell errors */
    }
  }

  window.ditaknetRefreshNotificationBell = refreshNotificationBell;
  if (document.getElementById("notification-bell-root")) {
    refreshNotificationBell();
    window.setInterval(refreshNotificationBell, 60000);
  }

  function syncThemeQuickToggle(snapshot) {
    var btn = document.getElementById("theme-quick-toggle");
    if (!btn) return;
    var icon = btn.querySelector("i");
    var active = snapshot && snapshot.active;
    if (!active && window.DitakNetTheme) {
      active = window.DitakNetTheme.resolveActiveTheme(window.DitakNetTheme.loadPrefs());
    }
    if (icon) {
      icon.className = active === "dark" ? "bi bi-moon-stars" : "bi bi-sun";
    }
    if (snapshot && snapshot.label) {
      btn.setAttribute("title", snapshot.label);
    }
  }
  if (window.DitakNetTheme) {
    window.DitakNetTheme.onChange(syncThemeQuickToggle);
    syncThemeQuickToggle(window.DitakNetTheme.refresh());
  }

  var csrfToken = document.body && document.body.getAttribute("data-csrf");
  if (csrfToken) {
    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
      if (form.querySelector('input[name="csrf_token"], input[name="login_csrf"]')) {
        return;
      }
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = csrfToken;
      form.appendChild(input);
    });
  }
})();
