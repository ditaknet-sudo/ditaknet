/**
 * Appearance settings page controller.
 */
(function () {
  "use strict";

  var Theme = window.DitakNetTheme;
  if (!Theme) return;

  var form = document.getElementById("appearance-theme-form");
  if (!form) return;

  var statusEl = document.getElementById("theme-active-status");
  var scheduleFields = document.getElementById("theme-schedule-fields");
  var dayInput = document.getElementById("theme-day-starts");
  var nightInput = document.getElementById("theme-night-starts");
  var saveBtn = document.getElementById("theme-save-btn");
  var feedback = document.getElementById("theme-save-feedback");

  function syncUI(snapshot) {
    var prefs = (snapshot && snapshot.prefs) || Theme.loadPrefs();
    form.querySelectorAll('input[name="theme_mode"]').forEach(function (input) {
      var selected = input.value === prefs.mode;
      input.checked = selected;
      var card = input.closest(".theme-mode-option");
      if (card) card.classList.toggle("is-selected", selected);
    });
    if (dayInput) dayInput.value = prefs.dayStarts;
    if (nightInput) nightInput.value = prefs.nightStarts;
    if (scheduleFields) scheduleFields.hidden = prefs.mode !== "auto";
    if (statusEl) {
      var active = (snapshot && snapshot.active) || Theme.resolveActiveTheme(prefs);
      statusEl.textContent =
        (snapshot && snapshot.label ? snapshot.label : Theme.describePrefs(prefs)) +
        " → " +
        (active === "dark" ? "Dark" : "Light");
    }
  }

  function readFormPrefs() {
    var modeInput = form.querySelector('input[name="theme_mode"]:checked');
    return {
      mode: modeInput ? modeInput.value : "system",
      dayStarts: dayInput ? dayInput.value : "07:00",
      nightStarts: nightInput ? nightInput.value : "19:00",
    };
  }

  form.addEventListener("change", function () {
    var prefs = readFormPrefs();
    Theme.setPrefs(prefs);
    syncUI(Theme.refresh());
    if (feedback) {
      feedback.textContent = "";
      feedback.className = "small text-muted";
    }
  });

  if (saveBtn) {
    saveBtn.addEventListener("click", function () {
      Theme.setPrefs(readFormPrefs());
      syncUI(Theme.refresh());
      if (feedback) {
        feedback.textContent = feedback.getAttribute("data-saved") || "Saved";
        feedback.className = "small text-success";
      }
    });
  }

  Theme.onChange(syncUI);
  syncUI(Theme.refresh());
})();
