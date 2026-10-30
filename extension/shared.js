// popup.js ve background.js tarafından paylaşılan yardımcılar (content.js kendi kopyasını taşır).
const SERVER = "http://127.0.0.1:38765";

function cleanUrl(href) {
  const u = new URL(href);
  ["t", "start", "si", "pp", "feature", "list", "index", "ab_channel"].forEach((k) => u.searchParams.delete(k));
  return u.toString();
}

function fmt(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}
