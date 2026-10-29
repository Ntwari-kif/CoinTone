/* Install-as-app support: registers the service worker and shows an "Install" button when the browser allows it. */
(function () {
  var deferred = null;
  var standalone = (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  var ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone;
  function show() {
    document.querySelectorAll("[data-install-btn]").forEach(function (b) { b.hidden = !deferred || standalone; });
    document.querySelectorAll("[data-ios-hint]").forEach(function (b) { b.hidden = !ios; });
  }
  window.addEventListener("beforeinstallprompt", function (e) { e.preventDefault(); deferred = e; show(); });
  window.addEventListener("appinstalled", function () { deferred = null; show(); });
  window.installApp = function () {
    if (!deferred) return;
    deferred.prompt();
    deferred.userChoice.then(function () { deferred = null; show(); });
  };
  document.addEventListener("DOMContentLoaded", show);
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }
})();
