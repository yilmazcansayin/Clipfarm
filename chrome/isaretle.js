/* Clipfarm Chrome yer imi (bookmarklet).
 * YouTube / Twitch VOD / Kick VOD sayfasında:
 *   1. tık: o anki saniyeyi "başlangıç" olarak işaretler
 *   2. tık: "bitiş"i işaretler ve clipfarm komutunu panoya kopyalar
 * Twitch / Kick klip sayfasında tek tıkla komutu kopyalar.
 * Yer imi satırını üretmek için: python chrome/build_bookmarklet.py
 */
(() => {
  const toast = (msg) => {
    const d = document.createElement("div");
    d.textContent = msg;
    d.style.cssText = "position:fixed;z-index:2147483647;top:16px;left:50%;transform:translateX(-50%);background:#111;color:#fff;padding:12px 18px;border-radius:10px;font:600 15px system-ui;box-shadow:0 4px 18px rgba(0,0,0,.4);max-width:90vw;word-break:break-all";
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 4500);
  };
  const copy = (text) => {
    const fallback = () => {
      const t = document.createElement("textarea");
      t.value = text;
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).catch(fallback);
    else fallback();
  };
  const fmt = (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const u = new URL(location.href);
  ["t", "start", "si", "pp", "feature", "list", "index"].forEach((k) => u.searchParams.delete(k));
  const url = u.toString();
  if (/\/clip|clips\.twitch\.tv/.test(url)) {
    const cmd = `clipfarm make "${url}"`;
    copy(cmd);
    toast("Kopyalandı: " + cmd);
    return;
  }
  const videos = [...document.querySelectorAll("video")].sort(
    (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight
  );
  const v = videos[0];
  if (!v) {
    toast("Bu sayfada video bulunamadı.");
    return;
  }
  const now = Math.floor(v.currentTime);
  const key = "clipfarm_start";
  let prev = null;
  try {
    prev = JSON.parse(sessionStorage.getItem(key) || "null");
  } catch (e) {}
  if (prev && prev.url === url && now > prev.t) {
    sessionStorage.removeItem(key);
    const cmd = `clipfarm make "${url}" --start ${fmt(prev.t)} --end ${fmt(now)}`;
    copy(cmd);
    toast(`Kopyalandı (${now - prev.t} sn): ` + cmd);
  } else {
    sessionStorage.setItem(key, JSON.stringify({ url, t: now }));
    toast("Başlangıç: " + fmt(now) + "  •  bitiş anında tekrar tıklayın");
  }
})();
