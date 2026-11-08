// TurboDL — contenido: encuentra los <video> y pone el botón flotante.
// Corre en TODOS los marcos (all_frames en el manifest): los reproductores
// de sitios como Cuevana viven en un iframe de otro dominio y el marco
// principal de la página no tiene ningún <video> — por eso el botón no salía.
(function () {
  let panel = null;
  let lastSrc = "";

  function bestVideo() {
    let best = null, bestArea = 0;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      const area = r.width * r.height;
      if (area > bestArea) { best = v; bestArea = area; }
    }
    return { v: best, area: bestArea };
  }

  function currentVideoSrc() {
    const { v } = bestVideo();
    if (!v) return "";
    const s = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src) || "";
    return s && !s.startsWith("blob:") ? s : "";
  }

  function report() {
    const s = currentVideoSrc();
    if (s && s !== lastSrc) {
      lastSrc = s;
      chrome.runtime.sendMessage({ type: "tdl-video", src: s, title: document.title }).catch(() => {});
    }
    const { v, area } = bestVideo();
    // Solo el reproductor de verdad: nada de botones dentro de iframes
    // publicitarios con videítos pequeños.
    if (v && area >= 300 * 170) ensurePanel();
  }

  function ensurePanel() {
    if (panel || !document.body) return;
    panel = document.createElement("div");
    panel.id = "tdl-panel";
    panel.innerHTML = '<span class="tdl-arrow">&#8595;</span> Descargar con <b>TurboDL</b>';
    const top = Math.min(74, Math.max(8, window.innerHeight - 64));
    const st = panel.style;
    st.cssText = "position:fixed;top:" + top + "px;right:14px;z-index:2147483647;background:linear-gradient(135deg,#2b2b30,#17171a);" +
      "color:#f5f5f5;font:600 13px/1.2 system-ui,sans-serif;padding:9px 13px;border-radius:999px;cursor:pointer;" +
      "box-shadow:0 6px 22px rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.14);user-select:none";
    panel.querySelector(".tdl-arrow").style.cssText = "color:#ff922b;font-weight:900;margin-right:2px";
    panel.addEventListener("click", async () => {
      const old = panel.innerHTML;
      panel.innerHTML = "Enviando a TurboDL…";
      try {
        const r = await chrome.runtime.sendMessage({ type: "tdl-main", src: currentVideoSrc(), title: document.title });
        if (r && r.ok) panel.innerHTML = "Enviado: revisa TurboDL";
        else if (r && r.error === "sin-stream") panel.innerHTML = "Dale play al video y reintenta";
        else panel.innerHTML = "TurboDL no responde: ábrelo primero";
      } catch { panel.innerHTML = "TurboDL no responde: ábrelo primero"; }
      setTimeout(() => { if (panel) panel.innerHTML = old; }, 3400);
    });
    document.body.appendChild(panel);
  }

  report();
  const obs = new MutationObserver(() => report());
  obs.observe(document.documentElement, { childList: true, subtree: true });
  let n = 0;
  const iv = setInterval(() => { report(); if (++n > 40) clearInterval(iv); }, 1500);
})();
