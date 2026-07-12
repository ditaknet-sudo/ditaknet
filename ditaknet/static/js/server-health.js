(function () {
  "use strict";

  const configEl = document.getElementById("health-dashboard-config");
  if (!configEl) return;

  let config = {};
  try {
    config = JSON.parse(configEl.textContent || "{}");
  } catch (err) {
    console.error("health dashboard config parse failed", err);
    return;
  }

  const i18n = config.i18n || {};
  const POLL_MS = 5000;
  let pollTimer = null;
  let logsOffset = 0;
  let logsTab = "important";

  const $ = (id) => document.getElementById(id);

  function formatBytesRate(bps) {
    if (bps == null || Number.isNaN(Number(bps))) return "—";
    const n = Number(bps);
    if (n < 1024) return `${n.toFixed(0)} B/s`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB/s`;
    return `${(n / (1024 * 1024)).toFixed(2)} MB/s`;
  }

  function formatUptime(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function formatTimestamp(value) {
    if (!value) return "—";
    const raw = String(value);
    try {
      const dt = new Date(raw);
      if (!Number.isNaN(dt.getTime())) {
        const pad = (n) => String(n).padStart(2, "0");
        return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
      }
    } catch (_) {}
    return raw.replace("T", " ").slice(0, 19);
  }

  function metricState(value, warn, critical) {
    if (value == null) return "unknown";
    const n = Number(value);
    if (n >= critical) return "critical";
    if (n >= warn) return "warning";
    return "normal";
  }

  function stateLabel(state) {
    if (state === "warning") return i18n.warning || "warning";
    if (state === "critical") return i18n.critical || "critical";
    if (state === "normal") return i18n.normal || "normal";
    return "—";
  }

  function applyMetricCard(prefix, percent, warn, critical) {
    const state = metricState(percent, warn, critical);
    const valueEl = $(`${prefix}-value`);
    const barEl = $(`${prefix}-bar`);
    const stateEl = $(`${prefix}-state`);
    if (valueEl) valueEl.textContent = percent != null ? `${Number(percent).toFixed(1)}%` : "—";
    if (barEl) {
      barEl.style.width = percent != null ? `${Math.min(100, Number(percent))}%` : "0%";
      barEl.className = `progress-bar state-${state}`;
    }
    if (stateEl) {
      stateEl.textContent = stateLabel(state);
      stateEl.className = `health-metric-state state-${state}`;
    }
  }

  function renderMetrics(metrics) {
    metrics = metrics || {};
    applyMetricCard("health-cpu", metrics.cpu_percent, 70, 90);
    applyMetricCard("health-ram", metrics.ram_percent, 75, 90);
    applyMetricCard("health-disk", metrics.disk_percent, 80, 90);

    const ramValue = $("health-ram-value");
    if (ramValue && metrics.ram_used != null && metrics.ram_total != null) {
      const usedGb = (metrics.ram_used / (1024 ** 3)).toFixed(1);
      const totalGb = (metrics.ram_total / (1024 ** 3)).toFixed(1);
      ramValue.textContent = `${usedGb} / ${totalGb} GB`;
    }

    const diskPaths = $("health-disk-paths");
    if (diskPaths) {
      const parts = [];
      if (metrics.data_dir_disk_percent != null) parts.push(`Data ${metrics.data_dir_disk_percent}%`);
      if (metrics.logs_dir_disk_percent != null) parts.push(`Logs ${metrics.logs_dir_disk_percent}%`);
      if (metrics.backups_dir_disk_percent != null) parts.push(`Backup ${metrics.backups_dir_disk_percent}%`);
      diskPaths.textContent = parts.join(" · ");
    }

    const upload = $("health-upload-rate");
    const download = $("health-download-rate");
    const netUnavailable = $("health-network-unavailable");
    const hasRates = metrics.network_upload_rate_bps != null || metrics.network_download_rate_bps != null;
    if (upload) upload.textContent = hasRates ? formatBytesRate(metrics.network_upload_rate_bps) : "—";
    if (download) download.textContent = hasRates ? formatBytesRate(metrics.network_download_rate_bps) : "—";
    if (netUnavailable) {
      netUnavailable.classList.toggle("d-none", hasRates || metrics.network_bytes_sent != null);
      if (!hasRates && metrics.network_bytes_sent == null) {
        netUnavailable.textContent = i18n.metrics_unavailable || "Not available";
      }
    }
  }

  function renderStatusBadge(status) {
    const badge = $("health-overall-badge");
    if (!badge) return;
    const key = status || "healthy";
    badge.className = `badge health-status-badge health-status-${key}`;
    badge.textContent = i18n[key] || key;
  }

  function renderJobs(jobs) {
    const body = $("health-active-jobs-body");
    const badge = $("health-active-jobs-badge");
    const countEl = $("health-jobs-count-value");
    jobs = jobs || [];
    if (badge) badge.textContent = String(jobs.length);
    if (countEl) countEl.textContent = String(jobs.length);
    if (!body) return;
    if (!jobs.length) {
      body.innerHTML = `<p class="text-muted small mb-0">${i18n.no_active_jobs || "No active jobs"}</p>`;
      return;
    }
    body.innerHTML = jobs
      .map(
        (job) => `
      <div class="health-job-item">
        <div class="d-flex justify-content-between">
          <strong>${job.type || "job"}</strong>
          <span class="text-muted">${formatUptime(job.elapsed_seconds)}</span>
        </div>
        <div>${job.current_target || job.target || "—"}</div>
        <div class="text-muted">${job.message || ""}</div>
        ${job.progress_percent != null ? `<div class="progress health-progress mt-1"><div class="progress-bar" style="width:${job.progress_percent}%"></div></div>` : ""}
      </div>`
      )
      .join("");
  }

  function renderChecks(checks, lastChecks) {
    const body = $("health-checks-body");
    if (!body) return;
    checks = checks || [];
    if (checks.length) {
      body.innerHTML = checks
        .slice(0, 5)
        .map(
          (c) => `
        <div class="health-check-item">
          <div><strong>${c.metadata?.host_name || c.metadata?.service_name || "check"}</strong> · ${c.metadata?.check_type || c.type || ""}</div>
          <div class="text-muted">${c.current_target || c.target || ""} · ${formatUptime(c.elapsed_seconds)}</div>
        </div>`
        )
        .join("");
      return;
    }
    const fallback = (lastChecks || []).slice(0, 5);
    if (fallback.length) {
      body.innerHTML = fallback
        .map(
          (c) => `
        <div class="health-check-item text-muted">
          <div>${c.message || c.event_type}</div>
          <div>${formatTimestamp(c.created_at)}</div>
        </div>`
        )
        .join("");
      return;
    }
    body.innerHTML = `<p class="text-muted small mb-0">${i18n.no_checks_running || "No checks running"}</p>`;
  }

  function renderDiscovery(scans) {
    const body = $("health-discovery-body");
    if (!body) return;
    scans = scans || [];
    if (!scans.length) {
      body.innerHTML = `
        <p class="text-muted small mb-2">${i18n.no_discovery_scan_running || "No discovery scan running"}</p>
        <a href="/discovery" class="btn btn-sm btn-outline-primary">Start discovery</a>`;
      return;
    }
    const scan = scans[0];
    body.innerHTML = `
      <div class="health-discovery-item">
        <div><strong>${scan.subnet || "subnet"}</strong> · ${scan.current_ip || "—"}</div>
        <div class="text-muted">${scan.stage || ""} · ${scan.scanned || 0}/${scan.total || 0} · found ${scan.found || 0}</div>
        <div class="progress health-progress mt-1"><div class="progress-bar" style="width:${scan.percent || 0}%"></div></div>
        <a href="/discovery?scan=${scan.scan_id}" class="small">View scan</a>
      </div>`;
  }

  function renderPreview(events) {
    const list = $("health-preview-events");
    if (!list) return;
    events = events || [];
    if (!events.length) {
      list.innerHTML = `<li class="list-group-item py-2 text-muted small">—</li>`;
      return;
    }
    list.innerHTML = events
      .slice(0, 5)
      .map(
        (e) => `
      <li class="list-group-item py-2 small health-preview-item">
        <span class="badge bg-secondary me-1">${e.level || "info"}</span>
        <span class="text-muted">${formatTimestamp(e.created_at)}</span>
        <span>${e.message || ""}</span>
      </li>`
      )
      .join("");
  }

  function renderSidePanels(data) {
    const health = data.health || config.health || {};
    const workload = data.workload || config.workload || {};

    const uptime = $("health-uptime-value");
    if (uptime) uptime.textContent = formatUptime(data.uptime_seconds || health.uptime_seconds);

    const scheduler = $("health-scheduler-value");
    if (scheduler) scheduler.textContent = data.scheduler_status || health.scheduler_status || "—";

    const database = $("health-database-value");
    if (database) database.textContent = data.database_status || health.database_status || "—";

    const workloadBody = $("health-workload-body");
    if (workloadBody) {
      workloadBody.innerHTML = `
        <div class="small">
          <div>Jobs: <strong>${workload.active_jobs ?? data.active_jobs_count ?? 0}</strong></div>
          <div>Checks: <strong>${workload.checks_running ?? data.checks_running_count ?? 0}</strong></div>
          <div>Discovery: <strong>${workload.discovery_running ? "yes" : "no"}</strong></div>
          <div>Scheduler: <strong>${workload.scheduler_status || "—"}</strong></div>
        </div>`;
    }

    const details = $("health-details-summary");
    if (details) {
      details.innerHTML = `
        <div>Version: ${health.app_version || "—"}</div>
        <div>Port: ${health.current_port || "—"}</div>
        <div>Process memory: ${data.metrics?.process_memory_mb != null ? data.metrics.process_memory_mb + " MB" : "—"}</div>
        <div>Errors (24h): ${data.errors_last_24h ?? 0}</div>
        <div>Warnings (24h): ${data.warnings_last_24h ?? 0}</div>`;
    }

    const alerts = $("health-last-alerts");
    if (alerts) {
      const err = data.last_error;
      const warn = data.last_warning;
      alerts.innerHTML = `
        <div class="mb-1"><span class="badge bg-danger me-1">error</span>${err ? formatTimestamp(err.created_at) + " — " + (err.message || "") : "—"}</div>
        <div><span class="badge bg-warning text-dark me-1">warning</span>${warn ? formatTimestamp(warn.created_at) + " — " + (warn.message || "") : "—"}</div>`;
    }

    const modules = $("health-modules-body");
    if (modules) {
      const mods = data.active_modules_friendly || health.active_modules || [];
      modules.innerHTML = mods.length
        ? mods.map((m) => `<span class="health-module-chip">${m}</span>`).join("")
        : `<span class="text-muted small">—</span>`;
    }

    const deploy = $("health-deployment-body");
    if (deploy) {
      deploy.innerHTML = `
        <div>Mode: ${health.deployment_mode || data.deployment_mode || "—"}</div>
        <div>License: ${health.license_package || data.license_package || "—"}</div>
        <div>Data dir writable: ${health.data_dir_writable ? "yes" : "no"}</div>`;
    }

    const modalBody = $("health-details-modal-body");
    if (modalBody) {
      modalBody.innerHTML = `<pre class="mb-0">${JSON.stringify({ health, metrics: data.metrics }, null, 2)}</pre>`;
    }
  }

  function applyDashboard(data) {
    renderStatusBadge(data.overall_status);
    renderMetrics(data.metrics || {});
    renderJobs(data.active_jobs);
    renderChecks(data.running_checks, data.recent_checks);
    renderDiscovery(data.discovery);
    renderPreview(data.important_events);
    renderSidePanels(data);
  }

  async function fetchDashboard() {
    const resp = await fetch("/api/system/health-dashboard", { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`dashboard ${resp.status}`);
    return resp.json();
  }

  async function refreshDashboard() {
    try {
      const data = await fetchDashboard();
      applyDashboard(data);
    } catch (err) {
      console.warn("health dashboard refresh failed", err);
    }
  }

  function tabCategory(tab) {
    const map = {
      important: null,
      errors: null,
      monitoring: "monitoring",
      discovery: "discovery",
      security: "security",
      raw: null,
    };
    return map[tab];
  }

  function tabErrorsOnly(tab) {
    return tab === "errors";
  }

  async function loadLogs() {
    const limit = Number($("logs-filter-limit")?.value || 50);
    const level = $("logs-filter-level")?.value || "all";
    const category = $("logs-filter-category")?.value || tabCategory(logsTab) || "all";
    const search = $("logs-filter-search")?.value || "";
    const dateFrom = $("logs-filter-from")?.value || "";
    const dateTo = $("logs-filter-to")?.value || "";
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(logsOffset),
      errors_only: tabErrorsOnly(logsTab) ? "true" : "false",
    });
    if (level !== "all") params.set("level", level);
    if (category && category !== "all") params.set("category", category);
    if (search) params.set("search", search);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);

    const resp = await fetch(`/api/system/logs/list?${params}`, { credentials: "same-origin" });
    if (!resp.ok) return;
    const data = await resp.json();
    const list = $("health-logs-list");
    const logs = data.logs || [];
    if (list) {
      list.innerHTML = logs.length
        ? logs
            .map(
              (row) => `
          <div class="health-log-row">
            <div><span class="badge bg-secondary me-1">${row.level}</span><span class="text-muted">${formatTimestamp(row.created_at)}</span></div>
            <div>${row.message || ""}</div>
            <div class="text-muted">${row.category || ""} · ${row.event_type || ""}</div>
          </div>`
            )
            .join("")
        : `<div class="p-3 text-muted small">—</div>`;
    }
    const pageInfo = $("logs-page-info");
    if (pageInfo) pageInfo.textContent = `${logsOffset + 1}–${logsOffset + logs.length}`;
    const prev = $("logs-prev-btn");
    const next = $("logs-next-btn");
    if (prev) prev.disabled = logsOffset <= 0;
    if (next) next.disabled = logs.length < limit;
  }

  function setupLogsDrawer() {
    document.querySelectorAll("#health-logs-tabs .nav-link").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#health-logs-tabs .nav-link").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        logsTab = btn.getAttribute("data-tab") || "raw";
        logsOffset = 0;
        loadLogs();
      });
    });
    ["logs-filter-level", "logs-filter-category", "logs-filter-search", "logs-filter-from", "logs-filter-to", "logs-filter-limit"].forEach(
      (id) => {
        const el = $(id);
        if (el) el.addEventListener("change", () => {
          logsOffset = 0;
          loadLogs();
        });
        if (el && el.type === "search") el.addEventListener("input", () => {
          logsOffset = 0;
          loadLogs();
        });
      }
    );
    $("logs-prev-btn")?.addEventListener("click", () => {
      const limit = Number($("logs-filter-limit")?.value || 50);
      logsOffset = Math.max(0, logsOffset - limit);
      loadLogs();
    });
    $("logs-next-btn")?.addEventListener("click", () => {
      const limit = Number($("logs-filter-limit")?.value || 50);
      logsOffset += limit;
      loadLogs();
    });
    const drawer = $("health-logs-drawer");
    if (drawer) {
      drawer.addEventListener("shown.bs.offcanvas", () => loadLogs());
    }
  }

  function setupPolling() {
    const auto = $("health-auto-refresh");
    const refresh = () => {
      if (pollTimer) clearInterval(pollTimer);
      if (auto?.checked) pollTimer = setInterval(refreshDashboard, POLL_MS);
    };
    auto?.addEventListener("change", refresh);
    $("health-refresh-btn")?.addEventListener("click", refreshDashboard);
    refresh();
  }

  applyDashboard({
    overall_status: config.overall_status,
    metrics: config.metrics,
    health: config.health,
    workload: config.workload,
    active_jobs: config.active_jobs,
    running_checks: config.running_checks,
    discovery: config.discovery,
    important_events: config.preview_events,
    active_modules_friendly: config.health?.active_modules,
    uptime_seconds: config.health?.uptime_seconds,
    scheduler_status: config.health?.scheduler_status,
    database_status: config.health?.database_status,
    active_jobs_count: (config.active_jobs || []).length,
    checks_running_count: config.workload?.checks_running,
    deployment_mode: config.health?.deployment_mode,
    license_package: config.health?.license_package,
  });

  setupLogsDrawer();
  setupPolling();
})();
