(function () {
  "use strict";

  const configEl = document.getElementById("system-logs-config");
  let config = {};
  try {
    config = JSON.parse(configEl ? configEl.textContent || "{}" : "{}");
  } catch (_) {
    config = {};
  }
  const i18n = config.i18n || {};

  let offset = 0;
  let limit = 25;
  let activeTab = "";
  let detailModal = null;

  const $ = (id) => document.getElementById(id);

  function formatTime(value) {
    if (!value) return "—";
    try {
      const dt = new Date(String(value));
      if (!Number.isNaN(dt.getTime())) {
        const pad = (n) => String(n).padStart(2, "0");
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
      }
    } catch (_) {}
    return String(value).replace("T", " ").slice(0, 19);
  }

  function levelBadge(level) {
    const map = {
      debug: "secondary",
      info: "info",
      warning: "warning",
      error: "danger",
      critical: "danger",
    };
    const cls = map[level] || "secondary";
    return `<span class="badge bg-${cls}">${level || "—"}</span>`;
  }

  function buildParams() {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    params.set("offset", String(offset));
    const level = $("logs-filter-level")?.value || "";
    const search = $("logs-filter-search")?.value?.trim() || "";
    const from = $("logs-filter-from")?.value || "";
    const to = $("logs-filter-to")?.value || "";
    if (activeTab === "errors") {
      params.set("errors_only", "true");
    } else if (activeTab === "security") {
      params.set("category", "security");
    } else if (activeTab) {
      params.set("category", activeTab);
    }
    if (level) params.set("level", level);
    if (search) params.set("search", search);
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    return params;
  }

  function showDetail(log) {
    const body = $("log-detail-body");
    if (!body) return;
    const rows = [
      ["Time", formatTime(log.created_at)],
      ["Level", log.level],
      ["Category", log.category],
      ["Event", log.event_type],
      ["Message", log.message],
      ["Source", log.source],
    ];
    if (log.metadata && Object.keys(log.metadata).length) {
      rows.push(["Metadata", JSON.stringify(log.metadata, null, 2)]);
    }
    body.innerHTML = rows
      .map(
        ([k, v]) =>
          `<dt class="col-sm-3">${k}</dt><dd class="col-sm-9"><pre class="mb-2 small text-break" style="white-space:pre-wrap">${String(v || "—")}</pre></dd>`
      )
      .join("");
    if (!detailModal && window.bootstrap) {
      detailModal = new bootstrap.Modal($("log-detail-modal"));
    }
    detailModal?.show();
  }

  async function loadLogs() {
    const tbody = $("logs-table-body");
    const emptyEl = $("logs-empty");
    const table = $("logs-table");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="5" class="text-muted text-center py-4">${i18n.loading || "Loading…"}</td></tr>`;
    try {
      const resp = await (window.ditaknetFetch || fetch)(`/api/system/logs/list?${buildParams()}`, {
        credentials: "same-origin",
      });
      const data = await resp.json();
      const logs = data.logs || [];
      const total = data.total || 0;
      if (!logs.length) {
        tbody.innerHTML = "";
        table?.classList.add("d-none");
        emptyEl?.classList.remove("d-none");
      } else {
        table?.classList.remove("d-none");
        emptyEl?.classList.add("d-none");
        tbody.innerHTML = logs
          .map(
            (log, idx) => `<tr>
              <td class="text-nowrap small">${formatTime(log.created_at)}</td>
              <td>${levelBadge(log.level)}</td>
              <td class="small">${log.category || "—"}</td>
              <td class="small text-truncate" style="max-width:420px">${log.message || "—"}</td>
              <td class="text-end"><button type="button" class="btn btn-sm btn-outline-secondary" data-log-idx="${idx}">${i18n.details || "Details"}</button></td>
            </tr>`
          )
          .join("");
        tbody.querySelectorAll("[data-log-idx]").forEach((btn) => {
          btn.addEventListener("click", () => showDetail(logs[Number(btn.getAttribute("data-log-idx"))]));
        });
      }
      const info = $("logs-pagination-info");
      if (info) {
        const from = total ? offset + 1 : 0;
        const to = Math.min(offset + limit, total);
        info.textContent = total ? `${from}–${to} of ${total}` : "0";
      }
      $("logs-prev-btn").disabled = offset <= 0;
      $("logs-next-btn").disabled = offset + limit >= total;
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-danger text-center py-4">${i18n.load_error || "Could not load logs"}</td></tr>`;
    }
  }

  document.querySelectorAll("#logs-tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#logs-tabs .nav-link").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      activeTab = btn.getAttribute("data-tab") || "";
      offset = 0;
      loadLogs();
    });
  });

  ["logs-filter-level", "logs-filter-search", "logs-filter-from", "logs-filter-to", "logs-page-size"].forEach((id) => {
    $(id)?.addEventListener("change", () => {
      if (id === "logs-page-size") {
        limit = Number($("logs-page-size").value) || 25;
        offset = 0;
      }
      loadLogs();
    });
    if (id === "logs-filter-search") {
      $(id)?.addEventListener("input", () => {
        window.clearTimeout(window._logsSearchTimer);
        window._logsSearchTimer = window.setTimeout(() => {
          offset = 0;
          loadLogs();
        }, 350);
      });
    }
  });

  $("logs-refresh-btn")?.addEventListener("click", () => loadLogs());
  $("logs-prev-btn")?.addEventListener("click", () => {
    offset = Math.max(0, offset - limit);
    loadLogs();
  });
  $("logs-next-btn")?.addEventListener("click", () => {
    offset += limit;
    loadLogs();
  });

  limit = Number($("logs-page-size")?.value) || 25;
  loadLogs();
})();
