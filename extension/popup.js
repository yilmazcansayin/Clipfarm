const SERVER = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
let tab, url;

function cleanUrl(href) {
  const u = new URL(href);
  ["t", "start", "si", "pp", "feature", "list", "index"].forEach((k) => u.searchParams.delete(k));
  return u.toString();
}

function fmt(s) {
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}

function say(text, error = false) {
  $("msg").textContent = text;
  $("msg").className = error ? "err" : "";
}

async function currentTime() {
  const [res] = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: false },
    func: () => {
      const v = [...document.querySelectorAll("video")].sort(
        (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight
      )[0];
      return v ? v.currentTime : null;
    },
  });
  if (res.result == null) throw new Error("Bu sayfada video bulunamadı.");
  return Math.floor(res.result);
}

async function createJob(body) {
  const r = await fetch(SERVER + "/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, ...body }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Sunucu hatası");
  say("Gönderildi. İlerlemeyi panelden izleyebilirsin.");
  return data;
}

async function run(fn) {
  try { await fn(); } catch (e) { say(e.message.includes("fetch") ? "Clipfarm sunucusu kapalı: terminalde `clipfarm serve` çalıştır." : e.message, true); }
}

async function refreshMark() {
  const key = "mark:" + url;
  const mark = (await chrome.storage.session.get(key))[key];
  $("send").disabled = mark == null;
  $("markInfo").textContent = mark == null ? "Videoyu istediğin ana getir ve işaretle." : "Başlangıç: " + fmt(mark) + ". Bitişe gelince gönder.";
  return mark;
}

(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    url = cleanUrl(tab.url);
    $("url").textContent = url;
    refreshMark();
  } catch {
    $("url").textContent = "Bu sekmenin adresi okunamadı.";
    ["auto", "mark", "last30"].forEach((id) => ($(id).disabled = true));
  }
  try {
    const s = await (await fetch(SERVER + "/api/status")).json();
    $("server").textContent = `sunucu açık · Claude ${s.claude ? "✓" : "✗"} · YouTube ${s.youtube ? "✓" : "✗"}`;
  } catch {
    $("server").textContent = "sunucu kapalı";
    $("server").className = "err";
  }
})();

$("auto").onclick = () => run(() => createJob({ count: +$("count").value, layout: $("layout").value }));

$("mark").onclick = () => run(async () => {
  const t = await currentTime();
  await chrome.storage.session.set({ ["mark:" + url]: t });
  refreshMark();
});

$("send").onclick = () => run(async () => {
  const start = await refreshMark();
  const end = await currentTime();
  if (end <= start) throw new Error("Bitiş, başlangıçtan sonra olmalı.");
  if (end - start > 180) throw new Error("Shorts en fazla 180 saniye olabilir.");
  await createJob({ ranges: [[start, end]], layout: $("layout").value });
  await chrome.storage.session.remove("mark:" + url);
  refreshMark();
});

$("last30").onclick = () => run(async () => {
  const end = await currentTime();
  await createJob({ ranges: [[Math.max(0, end - 30), end]], layout: $("layout").value });
});

$("panel").onclick = () => chrome.tabs.create({ url: SERVER });
