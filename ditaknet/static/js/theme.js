/**
 * DitakNet appearance / theme engine.
 *
 * Modes: light | dark | system | auto
 * Persistence: localStorage (structured for future backend sync).
 * Storage key: ditaknet.theme.v1
 */
(function (global) {
  "use strict";

  var STORAGE_KEY = "ditaknet.theme.v1";
  var MODES = { light: 1, dark: 1, system: 1, auto: 1 };
  var DEFAULTS = {
    mode: "system",
    dayStarts: "07:00",
    nightStarts: "19:00",
  };
  var CHECK_MS = 60000;
  var mediaQuery = null;
  var intervalId = null;
  var listeners = [];

  function safeStorageGet() {
    try {
      if (!global.localStorage) return null;
      return global.localStorage.getItem(STORAGE_KEY);
    } catch (_err) {
      return null;
    }
  }

  function safeStorageSet(value) {
    try {
      if (!global.localStorage) return false;
      global.localStorage.setItem(STORAGE_KEY, value);
      return true;
    } catch (_err) {
      return false;
    }
  }

  function parseHHMM(value, fallback) {
    var raw = String(value || "").trim();
    // Accept HH:MM or HH:MM:SS (some browsers include seconds on <input type="time">)
    var match = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(raw);
    if (!match) return fallback;
    var h = Number(match[1]);
    var m = Number(match[2]);
    if (h < 0 || h > 23 || m < 0 || m > 59) return fallback;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function minutesFromHHMM(value) {
    var parts = String(value).split(":");
    return Number(parts[0]) * 60 + Number(parts[1]);
  }

  function normalizePrefs(raw) {
    var prefs = {
      mode: DEFAULTS.mode,
      dayStarts: DEFAULTS.dayStarts,
      nightStarts: DEFAULTS.nightStarts,
    };
    if (!raw || typeof raw !== "object") return prefs;
    var mode = String(raw.mode || "").toLowerCase();
    if (MODES[mode]) prefs.mode = mode;
    prefs.dayStarts = parseHHMM(raw.dayStarts || raw.day_starts, DEFAULTS.dayStarts);
    prefs.nightStarts = parseHHMM(raw.nightStarts || raw.night_starts, DEFAULTS.nightStarts);
    return prefs;
  }

  function loadPrefs() {
    var raw = safeStorageGet();
    if (!raw) return normalizePrefs(null);
    try {
      return normalizePrefs(JSON.parse(raw));
    } catch (_err) {
      return normalizePrefs(null);
    }
  }

  function savePrefs(prefs) {
    var normalized = normalizePrefs(prefs);
    safeStorageSet(JSON.stringify(normalized));
    return normalized;
  }

  function systemPrefersDark(nowMedia) {
    try {
      var mq = nowMedia || global.matchMedia("(prefers-color-scheme: dark)");
      return !!(mq && mq.matches);
    } catch (_err) {
      return false;
    }
  }

  /**
   * Resolve effective light/dark for auto schedule using local timezone.
   * Day window is [dayStarts, nightStarts). Overnight night wraps midnight.
   */
  function resolveAutoTheme(dayStarts, nightStarts, date) {
    var day = parseHHMM(dayStarts, DEFAULTS.dayStarts);
    var night = parseHHMM(nightStarts, DEFAULTS.nightStarts);
    var d = date || new Date();
    var mins = d.getHours() * 60 + d.getMinutes();
    var dayMin = minutesFromHHMM(day);
    var nightMin = minutesFromHHMM(night);

    if (dayMin === nightMin) {
      return "light";
    }
    if (dayMin < nightMin) {
      return mins >= dayMin && mins < nightMin ? "light" : "dark";
    }
    // Night window crosses midnight (unusual config)
    return mins >= dayMin || mins < nightMin ? "light" : "dark";
  }

  function resolveActiveTheme(prefs, options) {
    var p = normalizePrefs(prefs);
    var opts = options || {};
    if (p.mode === "light") return "light";
    if (p.mode === "dark") return "dark";
    if (p.mode === "auto") {
      return resolveAutoTheme(p.dayStarts, p.nightStarts, opts.now);
    }
    return systemPrefersDark(opts.media) ? "dark" : "light";
  }

  function applyTheme(theme) {
    var active = theme === "dark" ? "dark" : "light";
    var root = global.document && global.document.documentElement;
    if (!root) return active;
    root.setAttribute("data-theme", active);
    root.setAttribute("data-bs-theme", active);
    try {
      root.style.colorScheme = active;
    } catch (_err) {
      /* ignore */
    }
    return active;
  }

  function describePrefs(prefs) {
    var p = normalizePrefs(prefs);
    if (p.mode === "light") return "Light";
    if (p.mode === "dark") return "Dark";
    if (p.mode === "system") return "System";
    return "Auto: Light " + p.dayStarts + "–" + p.nightStarts + ", Dark " + p.nightStarts + "–" + p.dayStarts;
  }

  function notify(snapshot) {
    listeners.forEach(function (fn) {
      try {
        fn(snapshot);
      } catch (_err) {
        /* ignore listener errors */
      }
    });
  }

  function syncQuickToggle(snapshot) {
    var btn = global.document && global.document.getElementById("theme-quick-toggle");
    if (!btn) return;
    var active = (snapshot && snapshot.active) || resolveActiveTheme(loadPrefs());
    var icon = btn.querySelector("i");
    if (icon) {
      icon.className = active === "dark" ? "bi bi-sun" : "bi bi-moon-stars";
    }
    var nextLabel = active === "dark" ? "Switch to light mode" : "Switch to dark mode";
    btn.setAttribute("title", nextLabel);
    btn.setAttribute("aria-label", nextLabel);
    btn.setAttribute("aria-pressed", active === "dark" ? "true" : "false");
  }

  function toggleLightDark() {
    var active = resolveActiveTheme(loadPrefs());
    return setPrefs({ mode: active === "dark" ? "light" : "dark" });
  }

  function refresh() {
    var prefs = loadPrefs();
    var active = resolveActiveTheme(prefs);
    applyTheme(active);
    var snapshot = {
      prefs: prefs,
      active: active,
      label: describePrefs(prefs),
      mode: prefs.mode,
    };
    syncQuickToggle(snapshot);
    notify(snapshot);
    return snapshot;
  }

  function setPrefs(partial) {
    var current = loadPrefs();
    var next = normalizePrefs({
      mode: partial && partial.mode != null ? partial.mode : current.mode,
      dayStarts: partial && (partial.dayStarts != null || partial.day_starts != null)
        ? partial.dayStarts || partial.day_starts
        : current.dayStarts,
      nightStarts: partial && (partial.nightStarts != null || partial.night_starts != null)
        ? partial.nightStarts || partial.night_starts
        : current.nightStarts,
    });
    savePrefs(next);
    return refresh();
  }

  function startWatchers() {
    stopWatchers();
    try {
      mediaQuery = global.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        var prefs = loadPrefs();
        if (prefs.mode === "system") refresh();
      };
      if (mediaQuery.addEventListener) {
        mediaQuery.addEventListener("change", onChange);
      } else if (mediaQuery.addListener) {
        mediaQuery.addListener(onChange);
      }
      mediaQuery._ditaknetHandler = onChange;
    } catch (_err) {
      mediaQuery = null;
    }
    intervalId = global.setInterval(function () {
      var prefs = loadPrefs();
      if (prefs.mode === "auto") refresh();
    }, CHECK_MS);
  }

  function stopWatchers() {
    if (intervalId != null) {
      global.clearInterval(intervalId);
      intervalId = null;
    }
    if (mediaQuery && mediaQuery._ditaknetHandler) {
      try {
        if (mediaQuery.removeEventListener) {
          mediaQuery.removeEventListener("change", mediaQuery._ditaknetHandler);
        } else if (mediaQuery.removeListener) {
          mediaQuery.removeListener(mediaQuery._ditaknetHandler);
        }
      } catch (_err) {
        /* ignore */
      }
      mediaQuery = null;
    }
  }

  function onChange(fn) {
    if (typeof fn === "function") listeners.push(fn);
    return function () {
      listeners = listeners.filter(function (x) {
        return x !== fn;
      });
    };
  }

  function bindQuickToggle() {
    var btn = global.document && global.document.getElementById("theme-quick-toggle");
    if (!btn || btn.dataset.themeBound === "1") return;
    btn.dataset.themeBound = "1";
    btn.addEventListener("click", function (event) {
      event.preventDefault();
      toggleLightDark();
    });
  }

  function boot() {
    refresh();
    if (global.document) {
      var onReady = function () {
        startWatchers();
        bindQuickToggle();
        syncQuickToggle();
      };
      if (global.document.readyState === "loading") {
        global.document.addEventListener("DOMContentLoaded", onReady);
      } else {
        onReady();
      }
      global.addEventListener("beforeunload", stopWatchers);
    }
  }

  var api = {
    STORAGE_KEY: STORAGE_KEY,
    DEFAULTS: DEFAULTS,
    loadPrefs: loadPrefs,
    savePrefs: savePrefs,
    setPrefs: setPrefs,
    normalizePrefs: normalizePrefs,
    resolveActiveTheme: resolveActiveTheme,
    resolveAutoTheme: resolveAutoTheme,
    applyTheme: applyTheme,
    describePrefs: describePrefs,
    refresh: refresh,
    toggleLightDark: toggleLightDark,
    onChange: onChange,
    startWatchers: startWatchers,
    stopWatchers: stopWatchers,
    boot: boot,
  };

  global.DitakNetTheme = api;
  boot();
})(typeof window !== "undefined" ? window : globalThis);
