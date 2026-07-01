# webapp.py
"""Local web UI to browse scraped models and trigger scraping.

Zero extra dependencies — Python standard library + the project's SQLAlchemy
models and scraper registry. Run:

    python webapp.py                 # http://127.0.0.1:8000
    python webapp.py --port 9000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from db.database import fetch_history, init_db
from scraper import SCRAPERS
from scraper.logging_setup import setup_logging
from services import clear_models, fetch_models, filter_rows, models_to_csv

log = logging.getLogger("webapp")

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(PROJECT_ROOT, "Logo_TPP.png")

# --------------------------------------------------------------------------- #
# Background scrape job (one at a time)
# --------------------------------------------------------------------------- #
JOB: dict = {
    "running": False,
    "source": None,
    "limit": 0,
    "lines": [],
    "scraped": 0,
    "error": None,
    "finished": False,
}


class _ListHandler(logging.Handler):
    """Logging handler that mirrors scraper logs into JOB['lines']."""

    def __init__(self, sink: list) -> None:
        super().__init__()
        self.sink = sink
        self.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.append(self.format(record))
            del self.sink[:-250]  # keep only the most recent lines
        except Exception:
            pass


def _run_job(source: str, limit: int, headful: bool) -> None:
    JOB.update(
        running=True, source=source, limit=limit, lines=[],
        scraped=0, error=None, finished=False,
    )
    handler = _ListHandler(JOB["lines"])
    scraper_log = logging.getLogger("scraper")
    scraper_log.addHandler(handler)
    try:
        scraper = SCRAPERS[source](headless=not headful)
        models = asyncio.run(scraper.run(limit=limit))
        JOB["scraped"] = len(models)
    except Exception:  # noqa: BLE001
        JOB["error"] = "scrape failed — see server logs"
        log.exception("Scrape job failed")
    finally:
        scraper_log.removeHandler(handler)
        JOB["running"] = False
        JOB["finished"] = True


# --------------------------------------------------------------------------- #
# HTML page
# --------------------------------------------------------------------------- #
PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Printed Parser — Модели</title>
<style>
  :root {
    --bg:#0f1115; --panel:#181b22; --panel2:#1f232c; --line:#2a2f3a;
    --text:#e6e9ef; --muted:#9aa3b2; --accent:#ff7a45; --accent2:#4f9dff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--panel);
    border-bottom:1px solid var(--line); padding:14px 24px;
    display:flex; align-items:center; justify-content:center; }
  .logo { height:84px; width:auto; display:block; }
  .stats { color:var(--muted); font-size:13px; }
  .spacer { flex:1; }
  label.fld { color:var(--muted); font-size:12px; display:flex; flex-direction:column; gap:3px; }
  select, input[type=search], input[type=number] {
    background:var(--panel2); border:1px solid var(--line); color:var(--text);
    border-radius:8px; padding:8px 10px; outline:none; }
  input[type=search]{ min-width:240px; }
  input[type=number]{ width:80px; }
  select:focus, input:focus { border-color:var(--accent2); }
  button { background:var(--panel2); border:1px solid var(--line); color:var(--text);
    border-radius:8px; padding:9px 14px; cursor:pointer; font-weight:600; }
  button:hover { border-color:var(--accent2); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#1a1206; }
  button.primary:disabled { opacity:.5; cursor:not-allowed; }
  .toolbar { display:flex; gap:12px; align-items:flex-end; padding:14px 24px;
    background:var(--panel); border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .toolbar.view { align-items:center; }
  .toolbar .group { display:flex; gap:10px; align-items:flex-end; }
  .checkbox { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:13px; }
  button.danger { border-color:#7a3b3f; color:#ff9b9b; }
  button.danger:hover { border-color:#ff6b6b; color:#ff6b6b; }
  /* pretty notifications (replace raw status/log lines) */
  #notice { display:none; align-items:center; gap:12px; padding:10px 14px; border-radius:12px;
    border:1px solid var(--line); background:var(--panel2); min-width:300px; max-width:600px;
    animation:slideIn .22s ease; }
  #notice .ni { width:24px; height:24px; flex:0 0 24px; display:grid; place-items:center; font-size:16px; }
  #notice .nt { display:flex; flex-direction:column; line-height:1.3; min-width:0; }
  #notice .nt b { font-size:13px; }
  #notice .nt span { font-size:12px; max-width:520px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #notice .nx { margin-left:auto; background:transparent; border:none; color:var(--muted);
    font-size:20px; line-height:1; cursor:pointer; padding:0 2px; }
  #notice .nx:hover { color:var(--text); }
  #notice.info    { border-color:#3b6fb0; background:rgba(79,157,255,.10); }
  #notice.running { border-color:var(--accent); background:rgba(255,122,69,.10); }
  #notice.success { border-color:#3a9d5b; background:rgba(90,209,122,.12); }
  #notice.error   { border-color:#c0494f; background:rgba(255,107,107,.12); }
  #notice.info .ni { color:var(--accent2); } #notice.success .ni { color:#5ad17a; } #notice.error .ni { color:#ff6b6b; }
  .spin { width:18px; height:18px; border:2px solid rgba(255,122,69,.3);
    border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @keyframes slideIn { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:none; } }
  .wrap { padding:16px 24px 64px; }
  table { width:100%; border-collapse:collapse; }
  thead th { text-align:left; padding:10px 12px; color:var(--muted); font-weight:600;
    border-bottom:1px solid var(--line); position:sticky; top:113px; background:var(--bg);
    cursor:pointer; white-space:nowrap; user-select:none; }
  thead th:hover { color:var(--text); }
  thead th .arrow { color:var(--accent); margin-left:4px; }
  tbody td { padding:12px; border-bottom:1px solid var(--line); vertical-align:middle; }
  tbody tr:hover { background:var(--panel); }
  /* previews >2x larger than before (was 84x64) */
  .thumb { width:200px; height:150px; object-fit:cover; border-radius:10px;
    background:var(--panel2); border:1px solid var(--line); display:block; }
  .thumb.noimg { display:grid; place-items:center; color:var(--muted); font-size:13px;
    text-align:center; line-height:1.2; }
  .title a { color:var(--text); text-decoration:none; font-weight:600; font-size:15px; }
  .title a:hover { color:var(--accent2); text-decoration:underline; }
  .desc { color:var(--muted); font-size:12px; max-width:480px; margin-top:4px;
    overflow:hidden; text-overflow:ellipsis; display:-webkit-box;
    -webkit-line-clamp:2; -webkit-box-orient:vertical; }
  .badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px;
    background:rgba(79,157,255,.12); color:var(--accent2);
    border:1px solid rgba(79,157,255,.3); text-transform:capitalize; }
  .num { font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }
  .hype { display:inline-flex; align-items:center; gap:6px; font-weight:700;
    font-variant-numeric:tabular-nums; }
  .hbar { width:54px; height:7px; border-radius:4px; background:var(--panel2); overflow:hidden; }
  .hbar > i { display:block; height:100%; }
  .empty { text-align:center; color:var(--muted); padding:64px 0; }
  .muted { color:var(--muted); }
  code { background:var(--panel2); padding:1px 6px; border-radius:5px; }
  /* metric history sparkline */
  .histbtn { background:transparent; border:1px solid var(--line); color:var(--muted);
    border-radius:6px; padding:1px 7px; cursor:pointer; font-size:13px; margin-left:8px; }
  .histbtn:hover, .histbtn.active { border-color:var(--accent); color:var(--accent); }
  .histrow td { background:#0b0d11; }
  .histwrap { padding:10px 6px; color:var(--muted); font-size:13px; }
  .hist .legend { display:flex; gap:18px; font-size:12px; color:var(--muted); margin-bottom:6px; }
  .hist .legend i { display:inline-block; width:10px; height:10px; border-radius:2px;
    margin-right:5px; vertical-align:middle; }
  .hist .dates { display:flex; justify-content:space-between; font-size:11px;
    color:var(--muted); margin-top:4px; }
  .hist svg { background:var(--panel2); border:1px solid var(--line); border-radius:8px; }
</style>
</head>
<body>
<header>
  <img src="/Logo_TPP.png" alt="The Printed Parser" class="logo">
</header>

<div class="toolbar view">
  <div class="stats" id="stats">загрузка…</div>
  <div class="spacer"></div>
  <input id="q" type="search" placeholder="Поиск по названию…" autocomplete="off">
  <label class="fld">Источник
    <select id="filterSource"><option value="">Все</option></select>
  </label>
  <button id="reload">⟳ Обновить</button>
  <button id="exportBtn" title="Скачать отфильтрованное в CSV">⬇ CSV</button>
</div>

<div class="toolbar">
  <div class="group">
    <label class="fld">Парсить с площадки
      <select id="scrapeSource"></select>
    </label>
    <label class="fld">Сколько
      <input id="scrapeLimit" type="number" min="1" max="500" value="100">
    </label>
    <label class="checkbox"><input id="headful" type="checkbox" checked> показывать браузер</label>
    <button id="scrapeBtn" class="primary">▶ Запустить парсинг</button>
    <button id="clearBtn" class="danger">🗑 Очистить</button>
  </div>
  <div class="spacer"></div>
  <div id="notice">
    <span class="ni" id="noticeIcon"></span>
    <div class="nt"><b id="noticeTitle"></b><span id="noticeSub"></span></div>
    <button class="nx" id="noticeX" title="закрыть">×</button>
  </div>
</div>

<div class="wrap">
  <table>
    <thead>
      <tr>
        <th style="cursor:default">Превью</th>
        <th data-key="title">Название</th>
        <th data-key="source">Источник</th>
        <th data-key="hype" class="num" title="Индекс хайпа: загрузки + лайки + свежесть">🔥 Хайп</th>
        <th data-key="downloads_count" class="num">Загрузки</th>
        <th data-key="likes_count" class="num">Лайки</th>
        <th data-key="published_at">Опубликовано</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="empty" class="empty" style="display:none">
    Пока ничего не запарсилось. Нажмите «▶ Запустить парсинг» выше или выполните
    <code>python main.py printables --headful</code>.
  </div>
</div>

<script>
let DATA = [];
let sortKey = "hype";
let sortDir = -1;
let polling = null;

function imgError(el){ el.onerror=null; const d=document.createElement("div");
  d.className="thumb noimg"; d.textContent="нет фото"; el.replaceWith(d); }

// ---- metric history sparkline ----
async function toggleHistory(id, btn){
  const tr=btn.closest("tr");
  const next=tr.nextElementSibling;
  if(next && next.classList.contains("histrow")){ next.remove(); btn.classList.remove("active"); return; }
  // close any other open chart
  document.querySelectorAll("tr.histrow").forEach(r=>r.remove());
  document.querySelectorAll(".histbtn.active").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  const row=document.createElement("tr"); row.className="histrow";
  row.innerHTML=`<td colspan="7"><div class="histwrap">загрузка истории…</div></td>`;
  tr.after(row);
  try{
    const data=await (await fetch("/api/history?id="+id)).json();
    row.querySelector(".histwrap").innerHTML=renderSparkline(data);
  }catch(e){ row.querySelector(".histwrap").textContent="ошибка: "+e; }
}

function renderSparkline(data){
  if(!data || data.length<2){
    const n=data?data.length:0;
    return `<div class="muted">📈 История появится после нескольких парсингов
      (сейчас точек: ${n}). Каждый запуск добавляет точку, когда меняются загрузки/лайки.</div>`;
  }
  const W=640,H=150,P=26;
  const t=data.map(d=>new Date(d.captured_at).getTime());
  const tmin=Math.min(...t), tr=(Math.max(...t)-tmin)||1;
  const series=[["downloads_count","#ff7a45"],["likes_count","#4f9dff"]];
  let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">`;
  for(const [key,color] of series){
    const vals=data.map(d=>d[key]||0);
    const vmin=Math.min(...vals), vr=(Math.max(...vals)-vmin)||1;
    const pts=data.map((d,i)=>{
      const x=P+(t[i]-tmin)/tr*(W-2*P);
      const y=H-P-((d[key]||0)-vmin)/vr*(H-2*P);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const last=pts.split(" ").pop().split(",");
    svg+=`<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${pts}"/>`;
    svg+=`<circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${color}"/>`;
  }
  svg+=`</svg>`;
  const fmtD=ms=>new Date(ms).toLocaleDateString("ru-RU");
  const legend=`<div class="legend">
    <span><i style="background:#ff7a45"></i>Загрузки</span>
    <span><i style="background:#4f9dff"></i>Лайки</span>
    <span class="muted">точек: ${data.length}</span></div>`;
  const dates=`<div class="dates"><span>${fmtD(tmin)}</span><span>${fmtD(Math.max(...t))}</span></div>`;
  return `<div class="hist">${legend}${svg}${dates}</div>`;
}

const fmt = n => (n ?? 0).toLocaleString("ru-RU");
const esc = s => (s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// Only allow http(s) or same-origin relative URLs in href/src — blocks
// javascript:/data: schemes from scraped data (defense in depth).
const safeUrl = u => (typeof u === "string" && /^(https?:\/\/|\/)/i.test(u)) ? u : "#";
const date = s => { if(!s) return '<span class="muted">—</span>'; const d=new Date(s); return isNaN(d)?'<span class="muted">—</span>':d.toLocaleDateString("ru-RU"); };

// "Хайп" — индекс популярности из доступных сигналов (загрузки, лайки, свежесть).
function hypeRaw(m){
  const dl=m.downloads_count||0, lk=m.likes_count||0;
  let s = Math.log10(dl+1)*1.0 + Math.log10(lk+1)*1.4;
  if(m.published_at){
    const days=(Date.now()-new Date(m.published_at))/86400000;
    if(!isNaN(days)) s += Math.max(0, 2.5 - Math.log10(days+2)); // новее => горячее
  }
  return s;
}
function hypeColor(v){ // 0..100 -> сине-оранжевый градиент
  const h = 210 - (v/100)*190; return `hsl(${h} 90% 55%)`;
}

function computeHype(rows){
  let max=0;
  rows.forEach(m => { m._h = hypeRaw(m); if(m._h>max) max=m._h; });
  rows.forEach(m => { m.hype = max>0 ? Math.round(m._h/max*100) : 0; });
}

function render(){
  const q=document.getElementById("q").value.trim().toLowerCase();
  const src=document.getElementById("filterSource").value;
  let rows=DATA.filter(m =>
    (!q || (m.title||"").toLowerCase().includes(q)) &&
    (!src || m.source===src)
  );
  computeHype(rows);
  rows.sort((a,b)=>{
    let va=a[sortKey], vb=b[sortKey];
    if(typeof va==="string"){ va=va.toLowerCase(); vb=(vb||"").toLowerCase(); }
    if(va==null) va=""; if(vb==null) vb="";
    return va<vb?-sortDir:va>vb?sortDir:0;
  });

  document.getElementById("stats").textContent = `${rows.length} из ${DATA.length} моделей`;
  document.getElementById("empty").style.display = DATA.length?"none":"block";

  const img = m => {
    const src=m.remote_image_url || (m.local_image_path?("/local/"+encodeURIComponent(m.local_image_path)):"");
    // referrerpolicy=no-referrer is essential: image CDNs (bblmw, printables,
    // cults) block hot-linking by Referer; without it many previews 404.
    return src ? `<img class="thumb" loading="lazy" referrerpolicy="no-referrer"
                    src="${esc(safeUrl(src))}" onerror="imgError(this)">`
               : `<div class="thumb noimg">нет фото</div>`;
  };

  document.getElementById("rows").innerHTML = rows.map(m => `
    <tr>
      <td>${img(m)}</td>
      <td class="title">
        <a href="${esc(safeUrl(m.source_url))}" target="_blank" rel="noopener">${esc(m.title)}</a>
        <button class="histbtn" onclick="toggleHistory(${m.id}, this)" title="История метрик">📈</button>
        ${m.description?`<div class="desc">${esc(m.description)}</div>`:""}
      </td>
      <td><span class="badge">${esc(m.source)}</span></td>
      <td class="num"><span class="hype" style="color:${hypeColor(m.hype)}">
        ${m.hype}<span class="hbar"><i style="width:${m.hype}%;background:${hypeColor(m.hype)}"></i></span>
      </span></td>
      <td class="num">${fmt(m.downloads_count)}</td>
      <td class="num">${fmt(m.likes_count)}</td>
      <td>${date(m.published_at)}</td>
    </tr>`).join("");

  document.querySelectorAll("thead th").forEach(th=>{
    const a=th.querySelector(".arrow"); if(a) a.remove();
    if(th.dataset.key===sortKey){
      const s=document.createElement("span"); s.className="arrow";
      s.textContent=sortDir<0?"▼":"▲"; th.appendChild(s);
    }
  });
}

document.querySelectorAll("thead th").forEach(th=>{
  if(!th.dataset.key) return;
  th.addEventListener("click",()=>{
    const k=th.dataset.key;
    if(k===sortKey) sortDir*=-1; else { sortKey=k; sortDir=-1; }
    render();
  });
});
document.getElementById("q").addEventListener("input", render);
document.getElementById("filterSource").addEventListener("change", render);
document.getElementById("reload").addEventListener("click", load);
document.getElementById("exportBtn").addEventListener("click", ()=>{
  const p=new URLSearchParams();
  const src=document.getElementById("filterSource").value;
  const q=document.getElementById("q").value.trim();
  if(src) p.set("source", src);
  if(q) p.set("q", q);
  const a=document.createElement("a");
  a.href="/api/export.csv"+(p.toString()?("?"+p.toString()):"");
  a.click();
});

async function loadSources(){
  const res=await fetch("/api/sources");
  const sources=await res.json();
  const fs=document.getElementById("filterSource"), ss=document.getElementById("scrapeSource");
  sources.forEach(s=>{
    fs.insertAdjacentHTML("beforeend",`<option value="${s}">${s}</option>`);
    ss.insertAdjacentHTML("beforeend",`<option value="${s}">${s}</option>`);
  });
}

async function load(){
  document.getElementById("stats").textContent="загрузка…";
  try{
    const res=await fetch("/api/models");
    DATA=await res.json();
    render();
  }catch(e){ document.getElementById("stats").textContent="ошибка загрузки: "+e; }
}

// ---- pretty notifications ----
const notice=document.getElementById("notice");
let noticeTimer=null;
function notify(type, title, sub, autohideMs){
  notice.className=type; notice.style.display="flex";
  const icons={info:"ℹ️", success:"✓", error:"⚠️"};
  document.getElementById("noticeIcon").innerHTML =
    type==="running" ? '<span class="spin"></span>' : (icons[type]||"");
  document.getElementById("noticeTitle").textContent=title||"";
  const sb=document.getElementById("noticeSub");
  sb.textContent=sub||""; sb.style.display=sub?"block":"none";
  if(noticeTimer){ clearTimeout(noticeTimer); noticeTimer=null; }
  if(autohideMs){ noticeTimer=setTimeout(()=>{ notice.style.display="none"; }, autohideMs); }
}
document.getElementById("noticeX").addEventListener("click",()=>{ notice.style.display="none"; });

// ---- scraping control ----
const btn=document.getElementById("scrapeBtn");

btn.addEventListener("click", async ()=>{
  const source=document.getElementById("scrapeSource").value;
  const limit=parseInt(document.getElementById("scrapeLimit").value)||50;
  const headful=document.getElementById("headful").checked;
  btn.disabled=true;
  notify("running","Запуск парсинга…","площадка: "+source);
  try{
    const res=await fetch("/api/scrape",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({source,limit,headful})});
    const j=await res.json();
    if(j.error){ notify("error","Не удалось запустить", j.error); btn.disabled=false; return; }
    pollStatus();
  }catch(e){ notify("error","Ошибка сети", String(e)); btn.disabled=false; }
});

function pollStatus(){
  if(polling) clearInterval(polling);
  polling=setInterval(async ()=>{
    try{
      const s=await (await fetch("/api/status")).json();
      const last=(s.lines||[]).slice(-1)[0]||"";
      if(s.running){
        notify("running","Парсинг "+(s.source||"")+"…", last);
      }else{
        clearInterval(polling); polling=null; btn.disabled=false;
        if(s.error) notify("error","Ошибка парсинга", s.error);
        else notify("success","Готово", (s.scraped||0)+" моделей с "+(s.source||""), 6000);
        if(s.finished) load();
      }
    }catch(e){ /* keep polling */ }
  }, 1500);
}

// ---- clear table ----
document.getElementById("clearBtn").addEventListener("click", async ()=>{
  const src=document.getElementById("filterSource").value;
  const what = src ? ("источник «"+src+"»") : "ВСЕ модели";
  if(!confirm("Очистить "+what+"? Записи будут удалены из базы безвозвратно.")) return;
  try{
    const res=await fetch("/api/clear",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({source:src||null})});
    const j=await res.json();
    if(j.error){ notify("error","Не удалось очистить", j.error); return; }
    notify("success","Таблица очищена","удалено записей: "+j.deleted, 5000);
    await load();
  }catch(e){ notify("error","Ошибка сети", String(e)); }
});

(async ()=>{ await loadSources(); await load();
  // resume status view if a job is already running (e.g. page refresh)
  try{ const s=await (await fetch("/api/status")).json(); if(s.running){ btn.disabled=true; pollStatus(); } }catch(e){}
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _export(self, as_csv: bool) -> None:
        qs = parse_qs(urlparse(self.path).query)
        source = (qs.get("source") or [""])[0] or None
        q = (qs.get("q") or [""])[0] or None
        rows = filter_rows(fetch_models(), source, q)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")

        if as_csv:
            # Prepend a BOM so Excel opens UTF-8 (Cyrillic) correctly.
            body = ("﻿" + models_to_csv(rows)).encode("utf-8")
            ctype, ext = "text/csv; charset=utf-8", "csv"
        else:
            body = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
            ctype, ext = "application/json; charset=utf-8", "json"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="tpp_models_{stamp}.{ext}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Only these hostnames may reach the server. Blocks DNS-rebinding: a
    # malicious site can't drive requests to the local dashboard via a rebound
    # hostname, because the Host header won't be one of these.
    _ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in self._ALLOWED_HOSTS

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send(b"Forbidden", "text/plain; charset=utf-8", 403)
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path in ("/Logo_TPP.png", "/logo.png", "/favicon.ico"):
            try:
                with open(LOGO_PATH, "rb") as f:
                    self._send(f.read(), "image/png")
            except OSError:
                self._send(b"", "image/png", 404)
        elif path == "/api/models":
            try:
                self._json(fetch_models())
            except Exception:  # noqa: BLE001
                log.exception("API error")
                self._json({"error": "internal error"}, 500)
        elif path == "/api/sources":
            self._json(sorted(SCRAPERS))
        elif path == "/api/history":
            qs = parse_qs(urlparse(self.path).query)
            try:
                model_id = int((qs.get("id") or ["0"])[0])
            except ValueError:
                model_id = 0
            self._json(fetch_history(model_id))
        elif path in ("/api/export.csv", "/api/export.json"):
            self._export(path.endswith(".csv"))
        elif path == "/api/status":
            self._json(JOB)
        else:
            self._send(b"Not found", "text/plain; charset=utf-8", 404)

    def do_POST(self) -> None:  # noqa: N802
        # CSRF defense: reject non-local hosts, and require a JSON content type.
        # A cross-site <form> POST cannot set application/json without triggering
        # a CORS preflight, so this blocks drive-by requests to /api/clear etc.
        if not self._host_allowed():
            self._send(b"Forbidden", "text/plain; charset=utf-8", 403)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._json({"error": "Content-Type must be application/json"}, 415)
            return
        path = urlparse(self.path).path
        # Cap the request body to avoid unbounded memory use.
        length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        try:
            data = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            data = {}

        if path == "/api/scrape":
            source = data.get("source")
            limit = max(1, min(500, int(data.get("limit") or 100)))
            headful = bool(data.get("headful", True))
            if source not in SCRAPERS:
                self._json({"error": f"неизвестный источник: {source}"}, 400)
                return
            if JOB["running"]:
                self._json({"error": f"уже идёт парсинг: {JOB['source']}"}, 409)
                return
            threading.Thread(
                target=_run_job, args=(source, limit, headful), daemon=True
            ).start()
            self._json({"started": True, "source": source, "limit": limit})

        elif path == "/api/clear":
            source = data.get("source") or None
            if source is not None and source not in SCRAPERS:
                self._json({"error": f"неизвестный источник: {source}"}, 400)
                return
            try:
                deleted = clear_models(source)
                self._json({"deleted": deleted, "source": source})
            except Exception:  # noqa: BLE001
                log.exception("Clear failed")
                self._json({"error": "internal error"}, 500)

        else:
            self._send(b"Not found", "text/plain; charset=utf-8", 404)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web UI for scraped models.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    setup_logging()
    init_db()  # ensure schema (incl. metric_snapshots) exists
    url = f"http://{args.host}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Web UI running at %s  (Ctrl+C to stop)", url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
