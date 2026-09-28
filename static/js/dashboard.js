/**
 * Small dashboard helper: auto-refresh the page every 60s so the numbers
 * (sales, tank levels, pending/on-delivery counts) stay reasonably live
 * without needing a websocket connection for the whole dashboard.
 */
(function () {
  const AUTO_REFRESH_MS = 60000;
  if (document.body.dataset.dashboard === "1") {
    setTimeout(() => window.location.reload(), AUTO_REFRESH_MS);
  }
})();
