// Minimal service worker for Web Push on the Customer Portal.
// Only handles background push display - no offline caching (not needed here).
self.addEventListener("push", function (event) {
  let data = { title: "Custodio Water", body: "May bagong update.", url: "/customer" };
  try {
    data = event.data.json();
  } catch (e) {
    // ignore malformed payloads
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Custodio Water", {
      body: data.body || "",
      icon: "/static/icon.png",
      data: { url: data.url || "/customer" },
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/customer";
  event.waitUntil(clients.openWindow(url));
});
