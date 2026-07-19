(function () {
  if (document.querySelector('[data-page-state="scan_running"]')) {
    window.setTimeout(function () { window.location.reload(); }, 5000);
  }
})();
