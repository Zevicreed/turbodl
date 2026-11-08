// TurboDL — popup: lista los streams cazados y los manda a descargar.
const $ = (s) => document.querySelector(s);
let tabId = -1;

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return n.toFixed(n >= 100 || i === 0 ? 0 : 1) + " " + u[i];
}

function status(t) { $("#status").textContent = t; }

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab.id;

  const ping = await chrome.runtime.sendMessage({ type: "tdl-ping" }).catch(() => ({ ok: false }));
  const conn = $("#conn");
  if (ping && ping.ok) { conn.textContent = "TurboDL conectado"; conn.className = "conn ok"; }
  else { conn.textContent = "TurboDL no está abierto"; conn.className = "conn bad"; }

  const { items } = await chrome.runtime.sendMessage({ type: "tdl-get", tabId });
  render(items || []);
}

function render(items) {
  const list = $("#list");
  list.innerHTML = "";
  $("#empty").style.display = items.length ? "none" : "block";
  for (const it of items) {
    const div = document.createElement("div");
    div.className = "item";
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = '<span class="tag"></span><span class="size">…</span>';
    meta.querySelector(".tag").textContent = it.kind;
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = it.name;
    name.title = it.url;
    const btns = document.createElement("div");
    btns.className = "btns";
    const b1 = document.createElement("button");
    b1.className = "dl"; b1.textContent = "Descargar con TurboDL";
    b1.addEventListener("click", async () => {
      const r = await chrome.runtime.sendMessage({ type: "tdl-send", tabId, item: it });
      status(r && r.ok ? "Enviado a TurboDL: " + it.name : "TurboDL no responde: abre el programa primero.");
    });
    const b2 = document.createElement("button");
    b2.className = "nv"; b2.textContent = "Navegador";
    b2.addEventListener("click", () => {
      chrome.downloads.download({ url: it.url });
      status("Descargando con el navegador: " + it.name);
    });
    btns.append(b1, b2);
    div.append(name, meta, btns);
    list.appendChild(div);
    chrome.runtime.sendMessage({ type: "tdl-size", url: it.url }).then((r) => {
      meta.querySelector(".size").textContent = fmtSize(r && r.size) || "tamaño desconocido";
    }).catch(() => { meta.querySelector(".size").textContent = ""; });
  }
}

$("#sendPage").addEventListener("click", async () => {
  const r = await chrome.runtime.sendMessage({ type: "tdl-send-page", tabId });
  status(r && r.ok ? "Página enviada: TurboDL la analiza y descarga el video." : "TurboDL no responde: abre el programa primero.");
});

$("#clear").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "tdl-clear", tabId });
  render([]);
  status("Lista limpia.");
});

init();
