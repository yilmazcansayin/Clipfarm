importScripts("shared.js");

// --- Sayfadaki video akışlarını yakalama (m3u8 / mpd / mp4 / webm) ---
const MEDIA_RE = /\.(m3u8|mpd|mp4|webm|m4v|mov)(\?|#|$)/i;
const MEDIA_TYPES = /^(video\/|application\/(vnd\.apple\.mpegurl|x-mpegurl|dash\+xml))/i;
// YouTube'un parça istekleri, HLS/DASH parçaları ve reklam/izleme adresleri listeyi kirletmesin.
const SKIP_RE = /googlevideo\.com|doubleclick|googlesyndication|\.(ts|m4s|cmfv|cmfa|aac|m4a)(\?|$)|[?&]range=|\/seg-\d|segment/i;
// Kendi çözücüsü olan siteler: sayfa adresi yeterli, yakalanan akışlar başka videoya ait olabilir.
const KNOWN_RE = /(^|\.)(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|twitter\.com|x\.com|twitch\.tv|kick\.com|facebook\.com|reddit\.com|vimeo\.com|dailymotion\.com|pinterest\.[a-z.]+|pin\.it|bilibili\.com|soundcloud\.com|streamable\.com|rumble\.com)$/i;

async function getMedia(tabId) {
  const key = "media:" + tabId;
  return (await chrome.storage.session.get(key))[key] || [];
}

async function addMedia(tabId, item) {
  if (tabId < 0) return;
  const key = "media:" + tabId;
  const list = await getMedia(tabId);
  if (list.some((m) => m.url === item.url)) return;
  list.unshift(item);
  await chrome.storage.session.set({ [key]: list.slice(0, 30) });
  chrome.action.setBadgeText({ tabId, text: String(Math.min(list.length, 99)) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#ff3b5c" });
}

chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    if (SKIP_RE.test(d.url)) return;
    const header = (name) => (d.responseHeaders || []).find((h) => h.name.toLowerCase() === name)?.value || "";
    const ct = header("content-type");
    // 206 yanıtlarında content-length parçanın boyudur; asıl boyut content-range'in sonundadır.
    const len = +(header("content-range").split("/")[1] || header("content-length") || 0);
    if (!MEDIA_RE.test(d.url) && !MEDIA_TYPES.test(ct)) return;
    if (/mp4|webm|video\//i.test(ct + d.url) && len && len < 300 * 1024) return; // küçük parça / önizleme
    const kind = /mpegurl|\.m3u8/i.test(ct + d.url) ? "HLS" : /dash|\.mpd/i.test(ct + d.url) ? "DASH" : "Dosya";
    addMedia(d.tabId, { url: d.url, kind, size: len || null, time: Date.now() });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] },
  ["responseHeaders"]
);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) {
    chrome.storage.session.remove("media:" + tabId);
    chrome.action.setBadgeText({ tabId, text: "" });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove("media:" + tabId));

// Önce ana listeler (master), sonra diğer akışlar, en son büyükten küçüğe dosyalar.
function rankMedia(list) {
  const score = (m) => (/master/i.test(m.url) ? 0 : m.kind !== "Dosya" ? 1 : 2);
  return [...list].sort((a, b) => score(a) - score(b) || (b.size || 0) - (a.size || 0));
}

// Giriş gerektiren videolar (Instagram, yaş sınırlı YouTube…) için sayfanın çerezleri yalnızca
// bilgisayardaki Clipfarm uygulamasına gider ve indirme bitince silinir.
async function cookiesFor(url) {
  try {
    if (!/^https?:/i.test(url)) return [];
    return (await chrome.cookies.getAll({ url })).slice(0, 300).map((c) => ({
      domain: c.domain, hostOnly: c.hostOnly, path: c.path, secure: c.secure, httpOnly: c.httpOnly,
      expirationDate: c.expirationDate, name: c.name, value: c.value,
    }));
  } catch {
    return [];
  }
}

// --- Yerel Clipfarm sunucusuyla konuşma ---
async function api(path, body) {
  const r = await fetch(SERVER + path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Sunucu hatası");
  return data;
}

const OFFLINE = "Clipfarm yardımcı uygulaması çalışmıyor. Masaüstündeki \"Clipfarm Baslat\" ile başlatın.";

async function startDownload(msg, sender, tabId) {
  const url = msg.url;
  let host = "";
  try { host = new URL(url).hostname; } catch { throw new Error("Geçersiz adres."); }
  const direct = msg.direct && /^https?:/i.test(msg.direct) ? msg.direct : null;
  const captured = KNOWN_RE.test(host) || msg.noCaptured ? [] : rankMedia(await getMedia(tabId)).map((m) => m.url);
  const sources = [...new Set([direct, ...captured].filter((s) => s && s !== url))];
  const referer = msg.referer || sender.tab?.url || "";
  const payload = {
    url, sources, referer, title: msg.title || "", start: msg.start ?? null, end: msg.end ?? null,
    quality: msg.quality || "best", cookies: await cookiesFor(url),
  };
  if (msg.drive !== undefined && msg.drive !== null) payload.drive = !!msg.drive;
  try {
    const task = await api("/api/downloads", payload);
    return { ok: true, via: "server", duplicate: !!task.duplicate };
  } catch (e) {
    // Sunucu kapalıysa ve elimizde doğrudan bir dosya adresi varsa tüm videoyu Chrome ile indir.
    const file = direct && !/\.(m3u8|mpd)/i.test(direct) ? direct : null;
    if (e instanceof TypeError && file && msg.start == null && msg.end == null) {
      await chrome.downloads.download({ url: file, saveAs: false });
      return { ok: true, via: "chrome" };
    }
    throw e instanceof TypeError ? new Error(OFFLINE) : e;
  }
}

async function handle(msg, sender) {
  const tabId = msg.tabId ?? sender.tab?.id;
  switch (msg.type) {
    case "status":
      return api("/api/status");
    case "media":
      return rankMedia(await getMedia(tabId));
    case "downloads":
      return api("/api/downloads");
    case "task": // reveal, cancel, retry, drive, forget
      return api(`/api/downloads/${encodeURIComponent(msg.id)}/${msg.action}`, {});
    case "reveal":
      return api(`/api/downloads/${encodeURIComponent(msg.id)}/reveal`, {});
    case "download":
      return startDownload(msg, sender, tabId);
    case "settings":
      return msg.changes ? api("/api/settings", msg.changes) : api("/api/settings");
    case "driveLogin":
      return api("/api/drive/login", {});
    case "ytdlpUpdate":
      return api("/api/ytdlp/update", {});
    case "shorts":
      return api("/api/jobs", { url: msg.url, ranges: msg.ranges || null, count: msg.count || 5, layout: msg.layout || "blur" });
    case "image":
      await chrome.downloads.download({ url: msg.url, saveAs: false });
      return { ok: true };
    case "panel":
      chrome.tabs.create({ url: SERVER });
      return { ok: true };
  }
  throw new Error("bilinmeyen istek");
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg, sender)
    .then((data) => reply({ ok: true, data }))
    .catch((e) => reply({ ok: false, error: e instanceof TypeError ? OFFLINE : e.message }));
  return true; // yanıt asenkron
});
