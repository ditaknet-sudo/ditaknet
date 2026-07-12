(function () {
  "use strict";

  const configEl = document.getElementById("device-detail-config");
  if (!configEl) return;

  let config = {};
  try {
    config = JSON.parse(configEl.textContent || "{}");
  } catch (err) {
    console.error("device detail config parse failed", err);
    return;
  }

  const i18n = config.i18n || {};
  const deviceId = config.device_id;
  let uptimeData = null;
  let metricsData = null;
  let activeRange = "24h";

  function $(id) {
    return document.getElementById(id);
  }

  function formatTs(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 19);
  }

  function statusClass(state) {
    const norm = String(state || "unknown").toLowerCase();
    if (norm === "ok") return "ok";
    if (norm === "warning") return "warning";
    if (norm === "critical" || norm === "down") return "critical";
    return "unknown";
  }

  async function fetchJson(path) {
    const resp = await fetch(path, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
    return resp.json();
  }

  function renderHeartbeat(range) {
    const container = $("uptime-heartbeat");
    const tooltip = $("uptime-tooltip");
    const noHistory = $("no-history-alert");
    if (!container || !uptimeData) return;

    const key = range === "7d" ? "bars_7d" : range === "30d" ? "bars_30d" : "bars_24h";
    const bars = uptimeData[key] || [];
    container.innerHTML = "";

    if (!bars.length || bars.every((b) => b.status === "unknown" && !b.checks)) {
      noHistory?.classList.remove("d-none");
      return;
    }
    noHistory?.classList.add("d-none");

    bars.forEach((bar) => {
      const el = document.createElement("div");
      el.className = `uptime-bar ${statusClass(bar.status)}`;
      const title = [
        formatTs(bar.sample_at || bar.start),
        bar.sample_status || bar.status,
        bar.avg_response_ms != null ? `${bar.avg_response_ms} ms` : "",
        `${bar.checks} checks`,
      ].filter(Boolean).join(" · ");
      el.title = title;
      el.addEventListener("mouseenter", () => {
        if (tooltip) tooltip.textContent = title;
      });
      container.appendChild(el);
    });
  }

  function drawLineChart(canvasId, points, valueKey, color) {
    const canvas = $(canvasId);
    if (!canvas || !points.length) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = canvas.width = canvas.clientWidth || 480;
    const height = canvas.height = canvas.clientHeight || 180;
    ctx.clearRect(0, 0, width, height);

    const values = points.map((p) => Number(p[valueKey] ?? 0));
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = Math.max(max - min, 1);

    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = (index / Math.max(points.length - 1, 1)) * (width - 20) + 10;
      const y = height - 20 - ((Number(point[valueKey] ?? 0) - min) / span) * (height - 40);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function drawStatusChart(points) {
    const canvas = $("status-chart");
    if (!canvas || !points.length) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = canvas.width = canvas.clientWidth || 480;
    const height = canvas.height = canvas.clientHeight || 180;
    ctx.clearRect(0, 0, width, height);
    const colors = { ok: "#17a673", warning: "#f2b544", critical: "#c2410c", unknown: "#d0d5dd" };
    const barWidth = Math.max(2, (width - 20) / points.length);
    points.forEach((point, index) => {
      const x = 10 + index * barWidth;
      ctx.fillStyle = colors[statusClass(point.status)] || colors.unknown;
      const h = point.status === "ok" ? height * 0.35 : point.status === "warning" ? height * 0.6 : height * 0.85;
      ctx.fillRect(x, height - h, Math.max(barWidth - 1, 1), h);
    });
  }

  function renderServices(checks) {
    const body = $("service-checks-body");
    if (!body) return;
    body.innerHTML = "";
    (checks || []).forEach((svc) => {
      const tr = document.createElement("tr");
      const latest = svc.latest_check || {};
      tr.innerHTML = `
        <td><a href="/services/${svc.id}">${svc.name}</a></td>
        <td><span class="badge text-bg-light border">${svc.check_type}</span></td>
        <td class="font-monospace small">${svc.target}${svc.port ? ":" + svc.port : ""}</td>
        <td><span class="badge bg-${statusClass(svc.current_state) === "ok" ? "success" : statusClass(svc.current_state) === "warning" ? "warning" : statusClass(svc.current_state) === "critical" ? "danger" : "secondary"}">${svc.current_state || "unknown"}</span></td>
        <td class="small text-muted">${formatTs(latest.checked_at)}</td>
        <td>${latest.response_time_ms != null ? latest.response_time_ms + " ms" : "—"}</td>
        <td>${svc.uptime_24h != null ? svc.uptime_24h + "%" : "—"}</td>
        <td class="text-end text-nowrap">
          <form method="post" action="/services/${svc.id}/run-now" class="d-inline"><button class="btn btn-sm btn-outline-warning">${i18n.run_check_now || "Run"}</button></form>
          <a href="/services/${svc.id}/edit" class="btn btn-sm btn-outline-secondary ms-1">${i18n["device_detail.edit"] || "Edit"}</a>
          ${svc.enabled ? `<form method="post" action="/services/${svc.id}/disable" class="d-inline ms-1"><button class="btn btn-sm btn-outline-secondary">${i18n["device_detail.disable_check"] || "Disable"}</button></form>` : ""}
        </td>`;
      body.appendChild(tr);
    });
  }

  function renderEvents(events) {
    const list = $("recent-events-list");
    if (!list) return;
    list.innerHTML = "";
    (events || []).forEach((event) => {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex justify-content-between gap-3";
      li.innerHTML = `
        <div>
          <div class="event-type">${(event.type || "").replace(/_/g, " ")}</div>
          <div class="small text-muted">${event.service_name || ""} ${event.message || ""}</div>
        </div>
        <div class="small text-muted">${formatTs(event.at)}</div>`;
      list.appendChild(li);
    });
    if (!events || !events.length) {
      const li = document.createElement("li");
      li.className = "list-group-item text-muted";
      li.textContent = i18n.no_monitoring_history || "No events yet.";
      list.appendChild(li);
    }
  }

  function applyOverview(data) {
    const status = data.status || {};
    const stats = data.stats || {};
    $("detail-status-pill")?.setAttribute("data-state", status.state || "unknown");
    if ($("detail-status-pill")) $("detail-status-pill").textContent = status.display || "UNKNOWN";
    $("detail-big-status")?.setAttribute("data-state", status.state || "unknown");
    if ($("detail-big-status")) $("detail-big-status").textContent = status.display || "UNKNOWN";
    $("main-status-card")?.setAttribute("data-state", status.state || "unknown");
    if ($("detail-last-seen")) $("detail-last-seen").textContent = formatTs(data.last_seen);
    if ($("detail-last-check")) $("detail-last-check").textContent = formatTs(data.last_check_at);
    if ($("meta-last-check")) $("meta-last-check").textContent = formatTs(data.last_check_at);
    if ($("meta-interval")) $("meta-interval").textContent = `${data.primary_service?.interval_seconds || "—"}s`;
    if ($("meta-response")) $("meta-response").textContent = data.response_time_ms != null ? `${data.response_time_ms} ms` : "—";
    if ($("detail-response-now")) {
      $("detail-response-now").textContent = data.response_time_ms != null ? `${data.response_time_ms} ms` : "—";
    }
    const setStat = (id, value, suffix = "") => {
      const el = $(id);
      if (el) el.textContent = value == null ? "—" : `${value}${suffix}`;
    };
    setStat("stat-current_response", stats.current_response_ms, " ms");
    setStat("stat-avg_response", stats.avg_response_24h, " ms");
    setStat("stat-uptime_24h", stats.uptime_24h, "%");
    setStat("stat-uptime_7d", stats.uptime_7d, "%");
    setStat("stat-uptime_30d", stats.uptime_30d, "%");
    setStat("stat-incidents", stats.incident_count_24h);
    setStat("stat-last_down", formatTs(stats.last_down_at));
    setStat("stat-recovered", formatTs(stats.last_recovery_at));
    setStat("stat-total_downtime", stats.total_downtime_display || (stats.total_downtime_seconds != null ? `${stats.total_downtime_seconds}s` : null));
    renderServices(data.checks);
    renderEvents(data.recent_events);
    if (!data.has_history) $("no-history-alert")?.classList.remove("d-none");
  }

  async function refreshAll() {
    const overview = await fetchJson(`/api/devices/${deviceId}/overview`);
    uptimeData = await fetchJson(`/api/devices/${deviceId}/uptime`);
    metricsData = await fetchJson(`/api/devices/${deviceId}/metrics`);
    applyOverview(overview);
    renderHeartbeat(activeRange);
    const responseSeries = metricsData.response_series || [];
    const tcpSeries = metricsData.tcp_response_series || [];
    drawLineChart("response-chart", responseSeries.length ? responseSeries : tcpSeries, "ms", "#1769aa");
    const tcpCanvas = $("tcp-chart");
    if (tcpCanvas) {
      if (responseSeries.length && tcpSeries.length) {
        tcpCanvas.classList.remove("d-none");
        drawLineChart("tcp-chart", tcpSeries, "ms", "#e67e22");
      } else {
        tcpCanvas.classList.add("d-none");
      }
    }
    drawStatusChart(metricsData.status_series || []);
    const packet = $("packet-loss-line");
    if (packet) {
      packet.textContent = metricsData.has_ping && metricsData.packet_loss_24h != null
        ? `${i18n["device_detail.packet_loss"] || "Packet loss 24h"}: ${metricsData.packet_loss_24h}%`
        : "";
    }
    const tcpLine = $("tcp-response-line");
    if (tcpLine) {
      if (metricsData.has_tcp && metricsData.tcp_avg_response_24h != null) {
        tcpLine.textContent = `${i18n["device_detail.tcp_response"] || "TCP avg response 24h"}: ${metricsData.tcp_avg_response_24h} ms`;
      } else {
        tcpLine.textContent = "";
      }
    }
  }

  document.querySelectorAll(".uptime-range-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".uptime-range-tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeRange = btn.getAttribute("data-range") || "24h";
      renderHeartbeat(activeRange);
    });
  });

  refreshAll().catch((err) => console.error("device detail refresh failed", err));
})();
