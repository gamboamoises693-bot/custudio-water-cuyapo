// Service worker - powers Web Push AND makes the app installable
// ("Add to Home Screen" / "Install app", like the Omega Ice app - see
// static/manifest.json for the Custodio Water icon). No offline caching
// yet (not needed here) - the fetch handler below is just a network
// passthrough, present because some browsers still require a fetch
// listener before showing the install prompt.
self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  event.respondWith(fetch(event.request));
});

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
      icon: "/static/images/icon-192.png",
      data: { url: data.url || "/customer" },
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/customer";
  event.waitUntil(clients.openWindow(url));
});
