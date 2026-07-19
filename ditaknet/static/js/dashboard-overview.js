(function () {
  "use strict";

  const STATE_COLORS = {
    ok: "#2ee880",
    warning: "#f5c542",
    critical: "#ff5b66",
    unknown: "#9fb2c8",
    disabled: "#9fb2c8",
  };

  const KIND_GLYPH = {
    internet: "WAN",
    router: "RTR",
    gateway: "GW",
    switch: "SW",
    subnet: "LAN",
    server: "SRV",
    linux_server: "LIN",
    windows_server: "WIN",
    camera: "CAM",
    nvr: "NVR",
    printer: "PRN",
    workstation: "PC",
    ap: "AP",
    nas: "NAS",
    cluster: "+",
    unknown: "?",
  };

  function parseConfig() {
    const el = document.getElementById("dashboard-topology-data");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (_) {
      return null;
    }
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") el.setAttribute(key, String(value));
    });
    return el;
  }

  function colorFor(state) {
    return STATE_COLORS[String(state || "unknown").toLowerCase()] || STATE_COLORS.unknown;
  }

  function shortText(value, max) {
    const text = String(value || "");
    return text.length > max ? text.slice(0, max - 1) + "." : text;
  }

  function groupBy(items, keyFn) {
    return items.reduce((acc, item) => {
      const key = keyFn(item);
      if (!acc[key]) acc[key] = [];
      acc[key].push(item);
      return acc;
    }, {});
  }

  function stateLabel(node) {
    if (node.discovered) return "discovered";
    return node.state || "unknown";
  }

  function nodeAnchor(node, side) {
    if (node.layer === 3) {
      return { x: side === "left" ? node.x : node.x + 206, y: node.y };
    }
    const offset = node.kind === "subnet" ? 32 : 28;
    return { x: node.x + (side === "right" ? offset : -offset), y: node.y };
  }

  function layoutGraph(graph) {
    const nodes = (graph.nodes || []).map((n) => ({ ...n }));
    const edges = graph.edges || [];
    const byLayer = groupBy(nodes, (n) => n.layer || 0);
    const subnets = byLayer[2] || [];
    const devices = byLayer[3] || [];
    const devicesBySubnet = groupBy(devices, (n) => n.subnet_id || "default");
    const tallestGroup = Math.max(1, ...subnets.map((subnet) => (devicesBySubnet[subnet.id] || []).length));
    const height = Math.max(420, Math.max(subnets.length, tallestGroup) * 68 + 170);
    const width = 1080;
    const layerX = [96, 285, 482, 710];

    (byLayer[0] || []).forEach((node) => {
      node.x = layerX[0];
      node.y = height / 2;
    });
    (byLayer[1] || []).forEach((node) => {
      node.x = layerX[1];
      node.y = height / 2;
    });

    const subnetGap = height / (subnets.length + 1);
    subnets.forEach((node, index) => {
      node.x = layerX[2];
      node.y = subnetGap * (index + 1);
    });

    subnets.forEach((subnet) => {
      const group = devicesBySubnet[subnet.id] || [];
      const gap = 66;
      const startY = subnet.y - ((group.length - 1) * gap) / 2;
      group.forEach((node, index) => {
        node.x = layerX[3];
        node.y = startY + index * gap;
      });
    });

    if (!subnets.length && devices.length) {
      const gap = height / (devices.length + 1);
      devices.forEach((node, index) => {
        node.x = layerX[3];
        node.y = gap * (index + 1);
      });
    }

    return { nodes, edges, subnets, devicesBySubnet, width, height };
  }

  function addDefs(svg) {
    const defs = svgEl("defs");
    const glow = svgEl("filter", { id: "topo-soft-glow", x: "-40%", y: "-40%", width: "180%", height: "180%" });
    glow.appendChild(svgEl("feGaussianBlur", { stdDeviation: 4, result: "blur" }));
    const merge = svgEl("feMerge");
    merge.appendChild(svgEl("feMergeNode", { in: "blur" }));
    merge.appendChild(svgEl("feMergeNode", { in: "SourceGraphic" }));
    glow.appendChild(merge);
    defs.appendChild(glow);

    const grid = svgEl("pattern", { id: "topo-grid", width: 32, height: 32, patternUnits: "userSpaceOnUse" });
    grid.appendChild(svgEl("path", {
      d: "M 32 0 L 0 0 0 32",
      fill: "none",
      stroke: "rgba(148, 163, 184, 0.14)",
      "stroke-width": 1,
    }));
    defs.appendChild(grid);
    svg.appendChild(defs);
  }

  function renderSubnetGroups(layer, laid) {
    laid.subnets.forEach((subnet) => {
      const devices = laid.devicesBySubnet[subnet.id] || [];
      const ys = [subnet.y, ...devices.map((node) => node.y)];
      const minY = Math.min(...ys) - 58;
      const maxY = Math.max(...ys) + 58;
      const rect = svgEl("rect", {
        class: "topo-subnet-group",
        x: subnet.x - 70,
        y: minY,
        width: 510,
        height: Math.max(116, maxY - minY),
        rx: 16,
      });
      layer.appendChild(rect);

      const label = svgEl("text", {
        class: "topo-subnet-title",
        x: subnet.x - 48,
        y: minY + 25,
      });
      label.textContent = shortText(subnet.label || "Subnet", 34);
      layer.appendChild(label);

      const meta = svgEl("text", {
        class: "topo-subnet-meta",
        x: subnet.x - 48,
        y: minY + 43,
      });
      const count = devices.length;
      meta.textContent = `${subnet.ip || ""}${count ? " - " + count + " devices" : ""}`;
      layer.appendChild(meta);
    });
  }

  function edgePath(from, to) {
    const start = nodeAnchor(from, "right");
    const end = nodeAnchor(to, "left");
    const mid = Math.max(60, (end.x - start.x) * 0.5);
    return `M ${start.x} ${start.y} C ${start.x + mid} ${start.y}, ${end.x - mid} ${end.y}, ${end.x} ${end.y}`;
  }

  function renderEdge(layer, edge, nodeMap) {
    const from = nodeMap[edge.from];
    const to = nodeMap[edge.to];
    if (!from || !to) return;
    const pathD = edgePath(from, to);
    const color = colorFor(edge.state);
    const path = svgEl("path", {
      class: `topo-edge${edge.animated ? " topo-edge--animated" : ""}${edge.discovered ? " topo-edge--discovered" : ""}`,
      d: pathD,
      stroke: color,
      "stroke-width": edge.discovered ? 1.4 : 2.4,
      "stroke-opacity": edge.discovered ? 0.42 : 0.68,
      fill: "none",
      "stroke-linecap": "round",
    });
    layer.appendChild(path);

    if (edge.animated || edge.state === "ok") {
      const dot = svgEl("circle", { class: "topo-flow-dot", r: 3.4, fill: color });
      const motion = svgEl("animateMotion", {
        dur: edge.animated ? "1.35s" : "2.5s",
        repeatCount: "indefinite",
        path: pathD,
      });
      dot.appendChild(motion);
      layer.appendChild(dot);
    }
  }

  function renderCoreNode(layer, node, tooltip) {
    const color = colorFor(node.state);
    const radius = node.kind === "subnet" ? 31 : 28;
    const group = svgEl("g", {
      class: `topo-node topo-core-node topo-node--${node.state || "unknown"}${node.checking ? " topo-node--checking" : ""}`,
      transform: `translate(${node.x},${node.y})`,
      tabindex: node.href ? 0 : -1,
      role: node.href ? "link" : "img",
    });

    group.appendChild(svgEl("circle", { class: "topo-core-halo", r: radius + 11, fill: color }));
    group.appendChild(svgEl("circle", {
      class: "topo-core-circle",
      r: radius,
      fill: "rgba(9, 29, 44, 0.92)",
      stroke: color,
      "stroke-width": 3,
      filter: node.state === "ok" ? "url(#topo-soft-glow)" : "",
    }));

    const glyph = svgEl("text", { class: "topo-core-glyph", "text-anchor": "middle", dy: "0.35em" });
    glyph.textContent = KIND_GLYPH[node.kind] || "?";
    group.appendChild(glyph);

    const label = svgEl("text", { class: "topo-core-label", y: 45, "text-anchor": "middle" });
    label.textContent = shortText(node.label || "", 20);
    group.appendChild(label);

    if (node.ip) {
      const ip = svgEl("text", { class: "topo-core-meta", y: 61, "text-anchor": "middle" });
      ip.textContent = shortText(node.ip, 24);
      group.appendChild(ip);
    }

    attachInteractions(group, node, tooltip);
    layer.appendChild(group);
  }

  function renderDeviceCard(layer, node, tooltip) {
    const color = colorFor(node.state);
    const group = svgEl("g", {
      class: `topo-node topo-device-card${node.discovered ? " topo-device-card--discovered" : ""} topo-node--${node.state || "unknown"}`,
      transform: `translate(${node.x},${node.y})`,
      tabindex: node.href ? 0 : -1,
      role: node.href ? "link" : "img",
    });

    group.appendChild(svgEl("rect", {
      class: "topo-device-card-bg",
      x: 0,
      y: -24,
      width: 220,
      height: 50,
      rx: 12,
      stroke: color,
      "stroke-dasharray": node.discovered ? "5 4" : "",
    }));
    group.appendChild(svgEl("rect", { class: "topo-device-state-bar", x: 0, y: -24, width: 4, height: 50, rx: 4, fill: color }));
    group.appendChild(svgEl("circle", { class: "topo-device-icon-bg", cx: 25, cy: 1, r: 15, fill: color }));

    const glyph = svgEl("text", { class: "topo-device-glyph", x: 25, y: 5, "text-anchor": "middle" });
    glyph.textContent = KIND_GLYPH[node.kind] || "?";
    group.appendChild(glyph);

    const title = svgEl("text", { class: "topo-device-title", x: 49, y: -3 });
    title.textContent = shortText(node.label || node.ip || "Device", 24);
    group.appendChild(title);

    const subtitle = svgEl("text", { class: "topo-device-meta", x: 49, y: 14 });
    subtitle.textContent = shortText(node.ip || stateLabel(node), 28);
    group.appendChild(subtitle);

    const pill = svgEl("g", { transform: "translate(154,-13)" });
    pill.appendChild(svgEl("rect", { class: "topo-device-pill", width: 55, height: 18, rx: 9, fill: color }));
    const pillText = svgEl("text", { class: "topo-device-pill-text", x: 27.5, y: 12, "text-anchor": "middle" });
    pillText.textContent = shortText(stateLabel(node).toUpperCase(), 9);
    pill.appendChild(pillText);
    group.appendChild(pill);

    attachInteractions(group, node, tooltip);
    layer.appendChild(group);
  }

  function attachInteractions(group, node, tooltip) {
    const meta = [
      node.label || "",
      node.ip || "",
      node.discovered ? "discovered" : "monitored",
      node.state || "unknown",
      node.confidence != null ? `${node.confidence}% confidence` : "",
    ].filter(Boolean).join(" - ");

    group.addEventListener("mouseenter", (event) => {
      tooltip.textContent = meta;
      tooltip.classList.add("visible");
      tooltip.style.left = event.clientX + 12 + "px";
      tooltip.style.top = event.clientY + 12 + "px";
    });
    group.addEventListener("mousemove", (event) => {
      tooltip.style.left = event.clientX + 12 + "px";
      tooltip.style.top = event.clientY + 12 + "px";
    });
    group.addEventListener("mouseleave", () => tooltip.classList.remove("visible"));

    if (node.href) {
      group.style.cursor = "pointer";
      group.addEventListener("click", () => {
        window.location.href = node.href;
      });
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = node.href;
        }
      });
    }
  }

  function renderTopology(container, graph) {
    if (!container || !graph || !graph.nodes || !graph.nodes.length) return;

    const laid = layoutGraph(graph);
    const nodeMap = {};
    laid.nodes.forEach((node) => {
      nodeMap[node.id] = node;
    });

    const svg = svgEl("svg", {
      class: "dash-topology-svg",
      viewBox: `0 0 ${laid.width} ${laid.height}`,
      preserveAspectRatio: "xMidYMid meet",
    });
    addDefs(svg);
    svg.appendChild(svgEl("rect", { class: "topo-grid-fill", x: 0, y: 0, width: laid.width, height: laid.height, fill: "url(#topo-grid)" }));

    const viewport = svgEl("g", { class: "topo-viewport" });
    const groups = svgEl("g", { class: "topo-groups" });
    const edges = svgEl("g", { class: "topo-edges" });
    const nodes = svgEl("g", { class: "topo-nodes" });
    renderSubnetGroups(groups, laid);
    laid.edges.forEach((edge) => renderEdge(edges, edge, nodeMap));

    let tooltip = document.querySelector(".topo-tooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.className = "topo-tooltip";
      document.body.appendChild(tooltip);
    }

    laid.nodes.forEach((node) => {
      if ((node.layer || 0) === 3) renderDeviceCard(nodes, node, tooltip);
      else renderCoreNode(nodes, node, tooltip);
    });

    viewport.appendChild(groups);
    viewport.appendChild(edges);
    viewport.appendChild(nodes);
    svg.appendChild(viewport);
    container.innerHTML = "";
    container.appendChild(svg);

    const wrap = container.closest(".dash-topology-wrap");
    if (!wrap) return;

    let panX = 0;
    let panY = 0;
    let scale = 1;
    let dragging = false;
    let startX = 0;
    let startY = 0;

    function applyTransform() {
      viewport.setAttribute("transform", `translate(${panX} ${panY}) scale(${scale})`);
    }

    function setScale(next) {
      scale = Math.max(0.75, Math.min(1.7, next));
      applyTransform();
    }

    wrap.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      dragging = true;
      wrap.setPointerCapture(event.pointerId);
      wrap.classList.add("is-dragging");
      startX = event.clientX - panX;
      startY = event.clientY - panY;
    });
    wrap.addEventListener("pointerup", (event) => {
      dragging = false;
      wrap.classList.remove("is-dragging");
      try {
        wrap.releasePointerCapture(event.pointerId);
      } catch (_) {
        /* pointer may already be released */
      }
    });
    wrap.addEventListener("pointercancel", () => {
      dragging = false;
      wrap.classList.remove("is-dragging");
    });
    wrap.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      panX = event.clientX - startX;
      panY = event.clientY - startY;
      applyTransform();
    });
    wrap.addEventListener("wheel", (event) => {
      event.preventDefault();
      setScale(scale + (event.deltaY < 0 ? 0.08 : -0.08));
    }, { passive: false });

    document.getElementById("topology-zoom-in")?.addEventListener("click", () => setScale(scale + 0.12));
    document.getElementById("topology-zoom-out")?.addEventListener("click", () => setScale(scale - 0.12));
    document.getElementById("topology-reset")?.addEventListener("click", () => {
      panX = 0;
      panY = 0;
      scale = 1;
      applyTransform();
    });
  }

  const graph = parseConfig();
  const mount = document.getElementById("dashboard-topology-mount");
  const empty = document.getElementById("dashboard-topology-empty");
  if (graph && !graph.empty && graph.nodes && graph.nodes.length > 0) {
    if (empty) empty.style.display = "none";
    renderTopology(mount, graph);
  } else if (empty) {
    empty.style.display = "flex";
  }
})();
