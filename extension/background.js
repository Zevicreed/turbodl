// TurboDL — fondo: vigila la red de cada pestaña y caza los streams de video.
const MEDIA_RE = /\.(mp4|webm|mkv|avi|mov|m3u8|mpd|mp3|m4a|flv)([?#]|$)/i;
const TDL = "http://127.0.0.1:8765";
const store = new Map(); // tabId -> Map(url -> item)

function kindOf(url) {
  const m = url.split("?")[0].match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toLowerCase() : "stream";
}

function nameOf(url) {
  try {
    const p = new URL(url).pathname.split("/").filter(Boolean).pop() || "video";
    return decodeURIComponent(p);
  } catch { return "video"; }
}

async function restore(tabId) {
  if (store.has(tabId)) return store.get(tabId);
  const key = "tab" + tabId;
  const data = await chrome.storage.session.get(key);
  const m = new Map((data[key] || []).map((it) => [it.url, it]));
  store.set(tabId, m);
  return m;
}

async function persist(tabId) {
  const m = store.get(tabId);
  if (!m) return;
  await chrome.storage.session.set({ ["tab" + tabId]: [...m.values()] });
  const n = m.size;
  chrome.action.setBadgeText({ tabId, text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#e8590c" });
}

async function addStream(tabId, url, title) {
  if (tabId < 0 || !url || url.startsWith("blob:") || url.startsWith("data:")) return;
  const m = await restore(tabId);
  if (!m.has(url)) {
    m.set(url, { url, kind: kindOf(url), name: nameOf(url), title: title || "", ts: Date.now() });
    if (m.size > 40) m.delete(m.keys().next().value); // fuera lo más viejo
    await persist(tabId);
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  (d) => { if (MEDIA_RE.test(d.url)) addStream(d.tabId, d.url, ""); },
  { urls: ["<all_urls>"] }
);

chrome.tabs.onRemoved.addListener((tabId) => {
  store.delete(tabId);
  chrome.storage.session.remove("tab" + tabId);
});

async function sendToTurboDL(tab, item) {
  try {
    const r = await fetch(TDL + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: item.url, referer: tab ? tab.url : "", title: item.title || (tab ? tab.title : "") }),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok && data.ok !== false };
  } catch {
    return { ok: false, error: "sin-conexion" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const tabId = msg.tabId ?? (sender.tab ? sender.tab.id : -1);
    if (msg.type === "tdl-video") {
      await addStream(tabId, msg.src, msg.title);
      sendResponse({ ok: true });
    } else if (msg.type === "tdl-get") {
      const m = await restore(tabId);
      sendResponse({ items: [...m.values()].reverse() });
    } else if (msg.type === "tdl-clear") {
      store.set(tabId, new Map());
      await persist(tabId);
      sendResponse({ ok: true });
    } else if (msg.type === "tdl-main") {
      // El stream principal: el del <video> si se conoce; si no, el mejor cazado.
      const tab = sender.tab || (await chrome.tabs.get(tabId).catch(() => null));
      // El título bueno es el de la PESTAÑA (el episodio); el título del
      // marco del reproductor suele ser basura del sitio de embeds.
      let item = msg.src ? { url: msg.src, title: (tab && tab.title) || msg.title } : null;
      if (!item) {
        const m = await restore(tabId);
        const items = [...m.values()];
        item = items.find((i) => i.kind === "mp4") || items.find((i) => i.kind === "m3u8") || items[items.length - 1];
      }
      if (!item) { sendResponse({ ok: false, error: "sin-stream" }); return; }
      sendResponse(await sendToTurboDL(tab, item));
    } else if (msg.type === "tdl-send") {
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      sendResponse(await sendToTurboDL(tab, msg.item));
    } else if (msg.type === "tdl-send-page") {
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (!tab) { sendResponse({ ok: false }); return; }
      sendResponse(await sendToTurboDL(tab, { url: tab.url, title: tab.title }));
    } else if (msg.type === "tdl-size") {
      try {
        const r = await fetch(msg.url, { method: "HEAD" });
        sendResponse({ size: Number(r.headers.get("content-length")) || 0 });
      } catch { sendResponse({ size: 0 }); }
    } else if (msg.type === "tdl-ping") {
      try {
        const r = await fetch(TDL + "/ping");
        sendResponse({ ok: r.ok });
      } catch { sendResponse({ ok: false }); }
    }
  })();
  return true; // respuesta asíncrona
});
