const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let tab;

const send = (msg) => new Promise((resolve) => chrome.runtime.sendMessage({ tabId: tab?.id, ...msg }, resolve));

function size(n) {
  if (!n) return "";
  return n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : (n / 1e6).toFixed(1) + " MB";
}

async function renderMedia() {
  const r = await send({ type: "media" });
  const list = r.ok ? r.data : [];
  if (!list.length) return;
  $("media").className = "";
  $("media").innerHTML = list.slice(0, 8).map((m, i) => `
    <div class="item">
      <div class="row"><span class="name"><b>${esc(m.kind)}</b> ${size(m.size)}</span>
        <button data-media="${i}">İndir</button></div>
      <div class="u">${esc(m.url)}</div>
    </div>`).join("");
  $("media").onclick = async (e) => {
    const b = e.target.closest("button[data-media]");
    if (!b) return;
    const m = list[+b.dataset.media];
    const r = await send({ type: "download", url: m.url, direct: m.kind === "Dosya" ? m.url : null, referer: tab.url, title: tab.title });
    $("msg").textContent = r.ok ? "İndirme başladı." : r.error;
    $("msg").className = r.ok ? "ok" : "err";
    renderDownloads();
  };
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
  $("downloads").innerHTML = r.data.slice(0, 6).map((t) => {
    const name = t.file ? t.file.split(/[\\/]/).pop() : t.title || t.url;
    const clip = t.start != null ? ` · ${fmt(t.start)}–${fmt(t.end)}` : "";
    const state = { queued: "sırada", downloading: Math.round(t.progress * 100) + "%", done: "✓ bitti", failed: "hata" }[t.status];
    return `<div class="item">
      <div class="row"><span class="name" title="${esc(name)}">${esc(name)}</span>
        <span class="${t.status === "done" ? "ok" : t.status === "failed" ? "err" : "muted"}">${state}</span>
        ${t.status === "done" ? `<button data-reveal="${t.id}">Göster</button>` : ""}</div>
      <div class="muted">${esc(t.quality)}${clip}</div>
      ${t.status === "downloading" ? `<div class="bar"><i style="width:${t.progress * 100}%"></i></div>` : ""}
      ${t.status === "failed" ? `<div class="u err">${esc(t.error.slice(0, 200))}</div>` : ""}
    </div>`;
  }).join("");
}

$("downloads").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-reveal]");
  if (b) send({ type: "reveal", id: b.dataset.reveal });
});

$("auto").onclick = async () => {
  const r = await send({ type: "shorts", url: cleanUrl(tab.url), count: +$("count").value });
  $("msg").textContent = r.ok ? "Gönderildi. İlerlemeyi panelden izleyebilirsin." : r.error;
  $("msg").className = r.ok ? "ok" : "err";
};
$("panel").onclick = () => send({ type: "panel" });

(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const s = await send({ type: "status" });
  $("server").innerHTML = s.ok
    ? `<span class="ok">● bağlı</span> · Claude ${s.data.claude ? "✓" : "✗"} · YouTube ${s.data.youtube ? "✓" : "✗"}`
    : `<span class="err">● yardımcı uygulama kapalı</span>`;
  renderMedia();
  renderDownloads();
  setInterval(renderDownloads, 1500);
})();
