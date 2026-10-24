// Clipfarm: sayfadaki her videonun (ve büyük görsellerin) üstüne indirme düğmesi ekler.
(() => {
  if (window.__clipfarm) return;
  window.__clipfarm = true;

  const MIN_W = 200, MIN_H = 112, IMG_MIN = 250;

  const fmt = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const parse = (t) => {
    t = String(t).trim();
    if (!t) return null;
    if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return NaN;
    return t.split(":").reduce((acc, p) => acc * 60 + parseFloat(p), 0);
  };

  function pageUrl() {
    const u = new URL(location.href);
    // YouTube: /watch, /shorts/ID, /live/ID ve gömülü oynatıcılar yt-dlp'nin anladığı biçime getirilir.
    if (/(^|\.)youtube\.com$/.test(u.hostname)) {
      const m = u.pathname.match(/^\/(shorts|live|embed)\/([\w-]{6,})/);
      if (m) return "https://www.youtube.com/watch?v=" + m[2];
      if (u.searchParams.get("v")) return "https://www.youtube.com/watch?v=" + u.searchParams.get("v");
    }
    ["t", "start", "si", "pp", "feature", "list", "index"].forEach((k) => u.searchParams.delete(k));
    return u.toString();
  }

  function directSrc(video) {
    const src = video.currentSrc || video.src || video.querySelector("source")?.src || "";
    return /^https?:/i.test(src) ? src : null;
  }

  function send(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => resolve(r || { ok: false, error: chrome.runtime.lastError?.message || "Eklenti yanıt vermedi." }));
      } catch (e) {
        resolve({ ok: false, error: "Eklenti yeniden yüklendi; sayfayı yenileyin." });
      }
    });
  }

  // --- Arayüz (sayfanın CSS'inden etkilenmesin diye shadow DOM içinde) ---
  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
  <style>
    * { box-sizing: border-box; font: 13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; }
    .btn { position: fixed; display: none; align-items: center; gap: 6px; padding: 6px 10px; border: 0; border-radius: 8px;
      background: rgba(255, 59, 92, .95); color: #fff; font-weight: 600; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,.35); }
    .btn:hover { background: #ff3b5c; }
    .btn.img { padding: 5px 8px; }
    .panel { position: fixed; display: none; width: 300px; padding: 12px; border-radius: 12px; background: #15181e; color: #e8eaf0;
      box-shadow: 0 8px 30px rgba(0,0,0,.5); border: 1px solid #2a2f3a; }
    .panel h3 { margin: 0 0 8px; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row { display: flex; gap: 6px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
    .lbl { width: 62px; color: #8b93a7; font-size: 12px; }
    input, select { width: 76px; padding: 5px 7px; border-radius: 7px; border: 1px solid #2a2f3a; background: #0c0e12; color: inherit; }
    select { width: auto; }
    button.s { padding: 6px 9px; border-radius: 7px; border: 0; background: #262b36; color: inherit; cursor: pointer; }
    button.s:hover { background: #313745; }
    button.p { background: #ff3b5c; color: #fff; font-weight: 600; }
    button.p:hover { background: #ff5572; }
    hr { border: 0; border-top: 1px solid #2a2f3a; margin: 10px 0; }
    .msg { min-height: 16px; font-size: 12px; color: #f5a623; margin-top: 6px; white-space: pre-wrap; }
    .msg.ok { color: #2ecc71; } .msg.err { color: #ff6b6b; }
    .x { position: absolute; top: 8px; right: 10px; cursor: pointer; color: #8b93a7; }
    .muted { color: #8b93a7; font-size: 11px; }
  </style>
  <button class="btn" id="btn" title="Clipfarm ile indir">⬇ İndir</button>
  <div class="panel" id="panel">
    <span class="x" id="close">✕</span>
    <h3 id="title">Video</h3>
    <div class="row">
      <select id="quality">
        <option value="best">En iyi kalite</option><option value="1080">1080p</option>
        <option value="720">720p</option><option value="480">480p</option><option value="audio">Sadece ses (mp3)</option>
      </select>
      <button class="s p" id="all">Tümünü indir</button>
    </div>
    <hr>
    <b>Klip al</b> <span class="muted" id="dur"></span>
    <div class="row"><span class="lbl">Başlangıç</span><input id="start" placeholder="0:00"><button class="s" data-now="start">⏱ şu an</button></div>
    <div class="row"><span class="lbl">Bitiş</span><input id="end" placeholder="1:00"><button class="s" data-now="end">⏱ şu an</button></div>
    <div class="row">
      <button class="s" data-last="30">Son 30 sn</button><button class="s" data-last="60">Son 1 dk</button>
      <button class="s" data-next="60">Buradan 1 dk</button>
    </div>
    <div class="row"><button class="s p" id="clip">Klibi indir</button><button class="s" id="shorts" title="Seçili aralıktan dikey, altyazılı Shorts üretir">Shorts yap</button></div>
    <div class="msg" id="msg"></div>
  </div>`;
  const $ = (id) => root.getElementById(id);
  const btn = $("btn"), panel = $("panel");
  (document.body || document.documentElement).appendChild(host);

  let target = null; // { el, kind: "video" | "img" }
  let panelVideo = null;

  function visibleRect(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight) return null;
    return r;
  }

  function findTarget(x, y) {
    for (const v of document.querySelectorAll("video")) {
      const r = visibleRect(v);
      if (r && r.width >= MIN_W && r.height >= MIN_H && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
        return { el: v, kind: "video" };
      }
    }
    const el = document.elementFromPoint(x, y);
    if (el && el.tagName === "IMG" && el.naturalWidth >= IMG_MIN && el.naturalHeight >= IMG_MIN) {
      const r = visibleRect(el);
      if (r && r.width >= IMG_MIN / 2) return { el, kind: "img" };
    }
    return null;
  }

  function placeButton() {
    if (!target) return (btn.style.display = "none");
    const r = visibleRect(target.el);
    if (!r) return (btn.style.display = "none");
    btn.textContent = target.kind === "img" ? "⬇" : "⬇ İndir";
    btn.className = "btn" + (target.kind === "img" ? " img" : "");
    btn.style.display = "flex";
    const w = btn.offsetWidth;
    btn.style.left = Math.max(4, Math.min(r.right - w - 10, innerWidth - w - 4)) + "px";
    btn.style.top = Math.max(4, r.top + 10) + "px";
  }

  let pending = false;
  document.addEventListener("mousemove", (e) => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      if (e.composedPath().includes(host)) return; // düğmenin/panelin üstündeyiz
      const t = findTarget(e.clientX, e.clientY);
      if (t?.el !== target?.el) {
        target = t;
        placeButton();
      }
    });
  }, { passive: true });
  addEventListener("scroll", placeButton, { passive: true });
  addEventListener("resize", placeButton, { passive: true });

  function say(text, cls = "") {
    $("msg").textContent = text;
    $("msg").className = "msg " + cls;
  }

  function openPanel() {
    panelVideo = target.el;
    const r = visibleRect(panelVideo) || { right: innerWidth, top: 10 };
    $("title").textContent = document.title || location.hostname;
    const d = panelVideo.duration;
    $("dur").textContent = isFinite(d) && d > 0 ? "(video " + fmt(d) + ")" : "(canlı / süre bilinmiyor)";
    if (!$("start").value) $("start").value = fmt(Math.max(0, panelVideo.currentTime - 30));
    if (!$("end").value) $("end").value = fmt(panelVideo.currentTime);
    say("");
    panel.style.display = "block";
    panel.style.left = Math.max(8, Math.min(r.right - 310, innerWidth - 310)) + "px";
    panel.style.top = Math.max(8, Math.min(r.top + 48, innerHeight - panel.offsetHeight - 8)) + "px";
  }

  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!target) return;
    if (target.kind === "img") {
      const r = await send({ type: "image", url: target.el.currentSrc || target.el.src });
      btn.textContent = r.ok ? "✓" : "✕";
      return;
    }
    openPanel();
  });
  $("close").addEventListener("click", () => (panel.style.display = "none"));

  root.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || !panelVideo) return;
    const now = panelVideo.currentTime;
    if (b.dataset.now) $(b.dataset.now).value = fmt(now);
    if (b.dataset.last) { $("start").value = fmt(now - +b.dataset.last); $("end").value = fmt(now); }
    if (b.dataset.next) { $("start").value = fmt(now); $("end").value = fmt(now + +b.dataset.next); }
  });

  function range() {
    const start = parse($("start").value), end = parse($("end").value);
    if (start == null || end == null || isNaN(start) || isNaN(end)) throw new Error("Başlangıç ve bitişi 1:02:30 biçiminde yazın.");
    if (end <= start) throw new Error("Bitiş, başlangıçtan sonra olmalı.");
    return { start, end };
  }

  async function download(clip) {
    try {
      const msg = {
        type: "download", url: pageUrl(), direct: directSrc(panelVideo), referer: location.href,
        title: document.title, quality: $("quality").value,
      };
      if (clip) Object.assign(msg, range());
      say("Gönderiliyor…");
      const r = await send(msg);
      if (!r.ok) throw new Error(r.error);
      say(r.data.via === "chrome" ? "Chrome indirmelerine eklendi." : "İndirme başladı. İlerlemeyi eklenti simgesinden izleyebilirsin.", "ok");
    } catch (err) {
      say(err.message, "err");
    }
  }

  $("all").addEventListener("click", () => download(false));
  $("clip").addEventListener("click", () => download(true));
  $("shorts").addEventListener("click", async () => {
    try {
      const { start, end } = range();
      if (end - start > 180) throw new Error("Shorts en fazla 3 dakika olabilir.");
      const r = await send({ type: "shorts", url: pageUrl(), ranges: [[start, end]] });
      if (!r.ok) throw new Error(r.error);
      say("Shorts sıraya alındı. Paneli eklenti simgesinden açabilirsin.", "ok");
    } catch (err) {
      say(err.message, "err");
    }
  });
})();
