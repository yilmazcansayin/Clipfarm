const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let tab, prefs = {}, lastHtml = "";

const send = (msg) => new Promise((resolve) => chrome.runtime.sendMessage({ tabId: tab?.id, ...msg }, resolve));

function size(n) {
  if (!n) return "";
  return n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : (n / 1e6).toFixed(1) + " MB";
}

function say(el, text, cls) {
  $(el).textContent = text;
  $(el).className = cls;
}

// --- Link yapıştırarak indirme ---
async function downloadLink() {
  const url = $("link").value.trim();
  if (!/^https?:\/\//i.test(url)) return say("msg", "Geçerli bir link yapıştır (https://…).", "err");
  say("msg", "Gönderiliyor…", "muted");
  const r = await send({ type: "download", url, noCaptured: true, referer: "", title: "", quality: $("quality").value, drive: $("linkDrive").checked });
  if (!r.ok) return say("msg", r.error, "err");
  say("msg", r.data.duplicate ? "Bu video zaten iniyor." : "İndirme başladı.", "ok");
  $("link").value = "";
  renderDownloads();
}
$("go").onclick = downloadLink;
$("link").addEventListener("keydown", (e) => e.key === "Enter" && downloadLink());
$("quality").onchange = () => chrome.storage.local.set({ quality: $("quality").value });
$("linkDrive").onchange = () => chrome.storage.local.set({ drive: $("linkDrive").checked });

// --- Sayfada yakalanan akışlar ---
async function renderMedia() {
  const r = await send({ type: "media" });
  const list = r.ok ? r.data : [];
  if (!list.length) return;
  $("media").className = "";
  $("media").innerHTML = list.slice(0, 8).map((m, i) => `
    <div class="item">
      <div class="row"><span class="name"><b>${esc(m.kind)}</b> ${size(m.size)}</span>
        <button class="mini" data-media="${i}">İndir</button></div>
      <div class="u">${esc(m.url)}</div>
    </div>`).join("");
  $("media").onclick = async (e) => {
    const b = e.target.closest("button[data-media]");
    if (!b) return;
    const m = list[+b.dataset.media];
    const r = await send({ type: "download", url: m.url, direct: m.kind === "Dosya" ? m.url : null, noCaptured: true,
                           referer: tab.url, title: tab.title, quality: $("quality").value, drive: $("linkDrive").checked });
    say("msg", r.ok ? "İndirme başladı." : r.error, r.ok ? "ok" : "err");
    renderDownloads();
  };
}

// --- İndirme listesi ---
function eta(s) {
  if (!s) return "";
  return s >= 3600 ? Math.floor(s / 3600) + " sa " + Math.floor((s % 3600) / 60) + " dk" : s >= 60 ? Math.floor(s / 60) + " dk " + (s % 60) + " sn" : s + " sn";
}

function stateText(t) {
  if (t.status === "queued") return ["sırada", "muted"];
  if (t.status === "done") return ["✓ bitti", "ok"];
  if (t.status === "failed") return ["hata", "err"];
  if (t.status === "canceled") return ["iptal edildi", "muted"];
  if (t.phase === "extracting") return ["bilgi alınıyor…", "muted"];
  if (t.phase === "processing") return ["birleştiriliyor…", "muted"];
  const parts = [];
  if (t.total) parts.push(Math.round(t.progress * 100) + "%");
  else if (t.downloaded) parts.push(size(t.downloaded));
  if (t.speed) parts.push(size(t.speed) + "/sn");
  if (t.eta) parts.push(eta(t.eta) + " kaldı");
  return [parts.join(" · ") || "başlıyor…", "muted"];
}

function driveLine(t) {
  if (t.drive_status === "uploading") return `<div class="muted">Drive'a kaydediliyor ${Math.round(t.drive_progress * 100)}%</div>
    <div class="bar drive"><i style="width:${t.drive_progress * 100}%"></i></div>`;
  if (t.drive_status === "done") {
    const where = /^https?:/.test(t.drive_link)
      ? `<a href="#" data-open="${esc(t.drive_link)}">Drive'da aç</a>` : `<span title="${esc(t.drive_link)}">Drive klasöründe</span>`;
    return `<div class="muted">☁ ${where}${t.local_deleted ? " · bilgisayardan silindi" : ""}</div>`;
  }
  if (t.drive_status === "failed") return `<div class="u err">Drive: ${esc(t.drive_error)}</div>`;
  if (t.drive && t.status !== "done" && t.status !== "failed" && t.status !== "canceled") return `<div class="muted">☁ bitince Drive'a kaydedilecek</div>`;
  return "";
}

function buttons(t) {
  const b = [];
  if (t.status === "queued" || t.status === "downloading") b.push(["cancel", "İptal"]);
  if (t.status === "failed" || t.status === "canceled") b.push(["retry", "Tekrar dene"]);
  if (t.status === "done" && !t.local_deleted) b.push(["reveal", "Göster"]);
  if (t.status === "done" && !t.local_deleted && t.drive_status !== "uploading" && t.drive_status !== "done")
    b.push(["drive", t.drive_status === "failed" ? "Drive'a tekrar" : "Drive'a gönder"]);
  if (t.status !== "queued" && t.status !== "downloading") b.push(["forget", "✕"]);
  return b.map(([a, label]) => `<button class="mini" data-act="${a}" data-id="${t.id}" title="${a === "forget" ? "Listeden kaldır" : ""}">${label}</button>`).join("");
}

async function renderDownloads() {
  const r = await send({ type: "downloads" });
  if (!r.ok) {
    $("downloads").innerHTML = `<span class="err">${esc(r.error)}</span>`;
    return;
  }
  if (!r.data.length) {
    $("downloads").textContent = "Henüz indirme yok.";
    return;
  }
  $("downloads").className = "";
  const html = r.data.slice(0, 8).map((t) => {
    const name = t.file ? t.file.split(/[\\/]/).pop() : t.title || t.url;
    const clip = t.start != null ? ` · ${fmt(t.start)}–${fmt(t.end ?? 0)}` : "";
    const [state, cls] = stateText(t);
    const busy = t.status === "downloading" && !t.total && t.phase !== "drive";
    return `<div class="item">
      <div class="row"><span class="name" title="${esc(t.url)}">${esc(name)}</span><span class="${cls}">${esc(state)}</span></div>
      <div class="row"><span class="muted" style="flex:1">${esc(t.quality)}${clip}${t.status === "done" && t.total ? " · " + size(t.total) : ""}</span>${buttons(t)}</div>
      ${t.status === "downloading" ? `<div class="bar${busy ? " busy" : ""}"><i style="width:${t.progress * 100}%"></i></div>` : ""}
      ${t.status === "failed" ? `<div class="u err">${esc(t.error.slice(0, 400))}</div>` : ""}
      ${driveLine(t)}
    </div>`;
  }).join("");
  // Değişmediyse yeniden çizme (tıklama sırasında düğme kaybolmasın).
  if (html !== lastHtml) $("downloads").innerHTML = lastHtml = html;
}

$("downloads").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open]");
  if (open) {
    e.preventDefault();
    return chrome.tabs.create({ url: open.dataset.open });
  }
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  b.disabled = true;
  const r = await send({ type: "task", id: b.dataset.id, action: b.dataset.act });
  if (!r.ok) say("msg", r.error, "err");
  renderDownloads();
});

// --- Ayarlar ---
async function renderStatus() {
  const s = await send({ type: "status" });
  if (!s.ok) {
    $("server").innerHTML = `<span class="err">● yardımcı uygulama kapalı</span>`;
    $("driveState").textContent = "";
    return null;
  }
  const d = s.data;
  $("server").innerHTML = `<span class="ok">● bağlı</span> · YouTube ${d.youtube ? "✓" : "✗"} · Drive ${d.drive.ready ? "✓" : "✗"}`;
  $("ytdlp").innerHTML = d.updating ? "yt-dlp güncelleniyor…"
    : `yt-dlp ${esc(d.ytdlp)}${d.impersonate ? "" : ' · <span class="warn">TikTok paketi eksik</span>'}${d.update_error ? ` · <span class="err" title="${esc(d.update_error)}">güncellenemedi</span>` : ""}`;
  $("update").disabled = d.updating;
  $("folder").textContent = "İndirme klasörü: " + d.download_dir;
  if (d.drive.desktop) $("driveState").innerHTML = `<span class="ok">✓ Google Drive masaüstü bulundu:</span> <span class="muted">${esc(d.drive.desktop)}</span>`;
  else if (d.drive.connected) $("driveState").innerHTML = `<span class="ok">✓ Google Drive bağlı</span> <button class="mini" id="driveLogin">Başka hesap</button>`;
  else if (d.drive.login_running) $("driveState").innerHTML = `<span class="warn">Açılan tarayıcı sekmesinde Google hesabını seç ve izin ver…</span>`;
  else $("driveState").innerHTML = `<button class="p mini" id="driveLogin">Google Drive'a bağlan</button>
    ${d.drive.login_error ? `<span class="err">${esc(d.drive.login_error)}</span>` : ""}`;
  return d;
}

$("driveState").addEventListener("click", async (e) => {
  if (!e.target.closest("#driveLogin")) return;
  const r = await send({ type: "driveLogin" });
  if (!r.ok) say("msg", r.error, "err");
  renderStatus();
});

$("update").onclick = async () => {
  const r = await send({ type: "ytdlpUpdate" });
  if (!r.ok) say("msg", r.error, "err");
  renderStatus();
};

async function loadSettings() {
  const r = await send({ type: "settings" });
  if (!r.ok) return;
  prefs = r.data;
  document.querySelectorAll("[data-set]").forEach((el) => {
    const v = prefs[el.dataset.set];
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  });
  const saved = await chrome.storage.local.get(["quality", "drive"]);
  if (saved.quality) $("quality").value = saved.quality;
  $("linkDrive").checked = typeof saved.drive === "boolean" ? saved.drive : !!prefs.drive_upload;
}

$("settings").addEventListener("change", async (e) => {
  const el = e.target.closest("[data-set]");
  if (!el) return;
  const key = el.dataset.set;
  const value = el.type === "checkbox" ? el.checked : key === "parallel" ? +el.value : el.value;
  const r = await send({ type: "settings", changes: { [key]: value } });
  if (!r.ok) return say("msg", r.error, "err");
  if (key === "drive_upload") {
    // Videoların üstündeki paneldeki "Drive'a da kaydet" kutusu da bu ayarı izlesin.
    await chrome.storage.local.set({ drive: value });
    $("linkDrive").checked = value;
  }
});

// --- Shorts ---
$("auto").onclick = async () => {
  const r = await send({ type: "shorts", url: cleanUrl(tab.url), count: +$("count").value });
  say("smsg", r.ok ? "Gönderildi. İlerlemeyi panelden izleyebilirsin." : r.error, r.ok ? "ok" : "err");
};
$("panel").onclick = () => send({ type: "panel" });

(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  // Açık sekme bir video sayfasıysa link kutusunu doldur.
  if (tab?.url && /tiktok\.com\/@[^/]+\/video\/|youtube\.com\/(watch|shorts)|instagram\.com\/(reel|p)\/|\/status\/\d+/.test(tab.url)) {
    $("link").value = cleanUrl(tab.url);
  }
  await loadSettings();
  renderStatus();
  renderMedia();
  renderDownloads();
  setInterval(renderDownloads, 1000);
  setInterval(renderStatus, 4000);
})();
