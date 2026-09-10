// Copy to dashboard-config.js (or let install/deploy.py generate it).
// Point the dashboard at YOUR Cerbo + location. Edit freely, then refresh the page.
window.BSF_CONFIG = {
  cerboIp: "192.168.1.50",          // your Cerbo GX LAN IP
  lat: -27.47,                       // your site latitude  (negative = southern hemisphere)
  lon: 153.02,                       // your site longitude
  tipAfterDays: 30,                  // days before the one-time "buy me a coffee" card (0 = never)
  tipUrl: "https://ko-fi.com/banksiaspringsfarm"   // where the ☕ link goes ("" hides it)
};
