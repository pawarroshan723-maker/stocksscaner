"use strict";
const $ = (id) => document.getElementById(id);
const state = {mode: "demo", tf: "DAY", rows: [], selected: null, meta: null, scanning: false, lastScanError: ""};
const moneyFmt = new Intl.NumberFormat("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const money = (n) => typeof n === "number" && Number.isFinite(n) && n > 0 ? `₹${moneyFmt.format(n)}` : "—";
const num = (n, suffix = "") => typeof n === "number" && Number.isFinite(n) ? `${n}${suffix}` : "—";
const label = (s) => ({BREAKOUT:"Bullish", BREAKDOWN:"Bearish", SIDEWAYS:"Sideways", NONE:"No signal"}[s] || "No signal");
const cssSignal = (s) => ({BREAKOUT:"bo", BREAKDOWN:"bd", SIDEWAYS:"sw"}[s] || "");
let toastTimer;
function notify(text) { $("toast").textContent = text; $("toast").classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").classList.remove("show"), 3800); }
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}
const jsonPost = (body) => ({method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
function query(extra = {}) { return new URLSearchParams({mode:state.mode, tf:state.tf, ...extra}).toString(); }
function selectedRow() { return state.rows.find(r => r.symbol === state.selected); }
function setText(id, value) { $(id).textContent = value == null || value === "" ? "—" : String(value); }
function setSignal(el, value) { el.className = `${el.id === "chartSignal" ? "chart-signal" : "signal-badge"} ${cssSignal(value)}`; el.textContent = label(value); }

function updateBanner() {
  const isDemo = state.mode === "demo";
  const banner = $("sourceBanner");
  banner.classList.toggle("demo-banner", isDemo); banner.classList.toggle("real-banner", !isDemo);
  setText("bannerTitle", isDemo ? "You’re exploring demo data" : "You’re viewing stored snapshots");
  setText("bannerText", isDemo ? "These prices are fictional examples, not live market data." : "Not a live feed. Check the candle date and scan time before using any setup.");
  $("bannerAction").firstChild.textContent = isDemo ? "Use my data " : "Read the guide ";
  setText("chartMode", isDemo ? "FICTIONAL DEMO" : "STORED SNAPSHOT");
  $("sourceSelect").value = state.mode;
}
function updateStats(counts) {
  setText("statTotal", state.rows.length); setText("statBull", counts.BREAKOUT || 0);
  setText("statBear", counts.BREAKDOWN || 0); setText("statSide", (counts.SIDEWAYS || 0) + (counts.NONE || 0));
}
function renderDetail() {
  const r = selectedRow();
  const ids = ["chartPrice","chartMeta","detailSymbol","detailSector","detailTf","signalReason","breakoutLevel","pullbackLevel","breakoutWhen","pullbackWhen","targetOne","targetTwo","targetStop","riskReward","candleDate","scanDate"];
  $("exportButton").disabled = !r; $("favoriteButton").disabled = !r;
  if (!r) {
    setText("chartTitle", "Select a snapshot"); setText("detailLogo", "—"); setText("chartSignal", "—"); setText("detailSignal", "—");
    $("chartSignal").className="chart-signal"; $("detailSignal").className="signal-badge"; $("favoriteButton").classList.remove("on");
    setText("breakoutLabel", "Breakout / breakdown"); setText("pullbackLabel", "Pullback / retest");
    ids.forEach(id => setText(id, "—")); drawChart([]); return;
  }
  setText("chartTitle", r.symbol); setText("chartMeta", `${r.sector} · ${state.tf} · ${r.source}`);
  setText("chartPrice", money(r.price)); setSignal($("chartSignal"), r.signal); setSignal($("detailSignal"), r.signal);
  setText("detailLogo", r.symbol.replace(/^DEMO-/, "").slice(0, 1));
  setText("detailSymbol", r.symbol); setText("detailSector", r.sector.toLowerCase()); setText("detailTf", state.tf);
  setText("signalReason", r.reason || "Not available for this snapshot.");
  const b = r.setups.breakout, p = r.setups.pullback;
  setText("breakoutLabel", b.label); setText("pullbackLabel", p.label);
  setText("breakoutLevel", b.level == null ? "Unavailable" : money(b.level));
  setText("pullbackLevel", p.level == null ? "Unavailable" : money(p.level));
  setText("breakoutWhen", b.when); setText("pullbackWhen", p.when);
  setText("targetOne", money(r.targets.target1)); setText("targetTwo", money(r.targets.target2));
  setText("targetStop", money(r.targets.stop)); setText("riskReward", r.risk_reward > 0 ? `${r.risk_reward}:1` : "—");
  setText("candleDate", r.candle_date || "Unavailable"); setText("scanDate", r.updated ? `${r.updated} IST` : "Unavailable");
  $("favoriteButton").classList.toggle("on", r.favorite);
  $("favoriteButton").setAttribute("aria-label", r.favorite ? "Remove from watchlist" : "Add to watchlist");
  drawChart(Array.isArray(r.candles) ? r.candles : []);
}
function cell(text, cls="") { const td=document.createElement("td"); td.textContent=text; if(cls) td.className=cls; return td; }
function renderTable() {
  const term = $("searchInput").value.trim().toLowerCase();
  const filter = $("signalFilter").value;
  const rows = state.rows.filter(r => (r.symbol.toLowerCase().includes(term) || r.sector.toLowerCase().includes(term)) &&
    (filter === "ALL" || (filter === "FAVORITES" ? r.favorite : r.signal === filter)));
  const body = $("resultsBody"); body.replaceChildren();
  for (const r of rows) {
    const tr=document.createElement("tr"); tr.tabIndex=0; tr.classList.toggle("selected", r.symbol === state.selected);
    tr.setAttribute("aria-label", `Inspect ${r.symbol}`);
    const symbolCell=document.createElement("td"), wrap=document.createElement("div"), logo=document.createElement("span"), meta=document.createElement("span"), name=document.createElement("strong"), sector=document.createElement("small");
    wrap.className="table-symbol"; logo.className="mini-logo"; logo.textContent=r.symbol.replace(/^DEMO-/, "").slice(0,1); name.textContent=r.symbol; sector.textContent=r.sector; meta.append(name,sector); wrap.append(logo,meta); symbolCell.append(wrap); tr.append(symbolCell);
    tr.append(cell(money(r.price), "price-cell"));
    const signalCell=document.createElement("td"), badge=document.createElement("span"); badge.className=`signal-badge ${cssSignal(r.signal)}`; badge.textContent=label(r.signal); signalCell.append(badge); tr.append(signalCell);
    const scoreCell=cell(r.score == null ? "—" : `${r.score}/100`), track=document.createElement("div"), fill=document.createElement("div"); track.className="score-track"; fill.className="score-fill"; fill.style.width=`${Math.max(0,Math.min(100, r.score || 0))}%`; track.append(fill); scoreCell.append(track); tr.append(scoreCell);
    tr.append(cell(num(r.rsi))); tr.append(cell(r.risk_reward > 0 ? `${r.risk_reward}:1` : "—"));
    const starCell=document.createElement("td"), star=document.createElement("button"); star.className=`row-star${r.favorite?" on":""}`; star.setAttribute("aria-label", `${r.favorite?"Remove":"Add"} ${r.symbol} ${r.favorite?"from":"to"} watchlist`); star.textContent=r.favorite?"★":"☆"; star.addEventListener("click", e=>{e.stopPropagation();toggleFavorite(r);}); starCell.append(star);tr.append(starCell);
    const select=()=>{state.selected=r.symbol;renderDetail();renderTable();}; tr.addEventListener("click",select);tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}}); body.append(tr);
  }
  setText("resultCount", rows.length); $("tableEmpty").hidden=rows.length>0;
  $("emptyText").textContent = !state.rows.length && state.mode === "real" ? "No saved snapshots for this timeframe. Run a scan or import your existing database." : "Try a different search, signal, or timeframe.";
  $("importButton").hidden = !(state.mode === "real" && !state.rows.length && state.meta?.legacy_available);
  setText("tableFooter", `Showing ${rows.length} of ${state.rows.length} snapshots`);
}
function drawChart(candles) {
  const svg=$("priceChart"), NS="http://www.w3.org/2000/svg"; svg.replaceChildren();
  const data=candles.filter(x=>x && typeof x.close==="number" && Number.isFinite(x.close) && x.close>0).slice(-60);
  $("chartEmpty").hidden=data.length>=2;
  if(data.length<2){setText("chartStart","—");setText("chartEnd","—");setText("chartFoot","No chart data available");return;}
  const node=(name, attrs={})=>{const n=document.createElementNS(NS,name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  const defs=node("defs"), gradient=node("linearGradient",{id:"chartFill",x1:"0",y1:"0",x2:"0",y2:"1"});
  gradient.append(node("stop",{offset:"0%","stop-color":"#83c89b","stop-opacity":".25"}),node("stop",{offset:"100%","stop-color":"#83c89b","stop-opacity":"0"}));defs.append(gradient);svg.append(defs);
  for(let i=0;i<4;i++) svg.append(node("line",{x1:0,y1:16+i*53,x2:700,y2:16+i*53,stroke:"#edf2ed","stroke-width":1}));
  const vals=data.map(x=>x.close), lo=Math.min(...vals), hi=Math.max(...vals), pad=Math.max((hi-lo)*.2,hi*.002), min=lo-pad, max=hi+pad;
  const points=vals.map((v,i)=>[i/(vals.length-1)*694+3,185-(v-min)/(max-min)*165]);
  const line=points.map((p,i)=>`${i?"L":"M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(" ");
  svg.append(node("path",{d:`${line} L697,204 L3,204 Z`,fill:"url(#chartFill)"}), node("path",{d:line,fill:"none",stroke:"#58a879","stroke-width":2.4,"stroke-linejoin":"round","stroke-linecap":"round"}));
  const last=points[points.length-1];svg.append(node("circle",{cx:last[0],cy:last[1],r:5,fill:"#fff",stroke:"#58a879","stroke-width":2}));
  setText("chartStart",data[0].time); setText("chartEnd",data[data.length-1].time);
  setText("chartFoot",state.mode==="demo"?"Illustrative sample series":"Stored candles · not live");
}
let loadVersion = 0;
async function load() {
  const version=++loadVersion;
  // Never leave another timeframe's prices visible under a newly selected label.
  state.rows=[];updateBanner();updateStats({});renderTable();renderDetail();
  $("emptyText").textContent="Loading snapshots…";$("importButton").hidden=true;
  try {
    const data=await api(`/api/snapshots?${query()}`);
    if(version!==loadVersion)return;
    state.rows=data.rows;
    if(!state.rows.some(r=>r.symbol===state.selected)) state.selected=state.rows[0]?.symbol || null;
    updateStats(data.counts);renderTable();renderDetail();
  } catch(e){if(version===loadVersion){$("emptyText").textContent="Could not load snapshots. Try refreshing.";notify(e.message);}}
}
async function toggleFavorite(row) {
  try { await api("/api/favorites",jsonPost({mode:state.mode,symbol:row.symbol,favorite:!row.favorite}));row.favorite=!row.favorite;renderTable();renderDetail();notify(row.favorite?"Added to watchlist":"Removed from watchlist"); }
  catch(e){notify(e.message);}
}
async function refreshMeta() { state.meta=await api("/api/meta"); }
function openScan() {
  if(state.scanning){if(!$("scanDialog").open)$("scanDialog").showModal();return;}
  const select=$("scanSymbol");select.replaceChildren();
  const all=document.createElement("option");all.value="ALL";all.textContent=`All ${state.meta.symbols.length} configured symbols`;select.append(all);
  for(const item of state.meta.symbols){const o=document.createElement("option");o.value=item.symbol;o.textContent=`${item.symbol} · ${item.sector}`;select.append(o);}
  select.value=state.meta.symbols.some(s=>s.symbol===state.selected) ? state.selected : state.meta.symbols[0]?.symbol;
  $("scanTf").value=state.tf;
  $("connectionNote").textContent=state.lastScanError || (state.meta.token_configured ? "Upstox token found locally. A scan may use cached candles; always check the candle date." : "Upstox is not configured. Set UPSTOX_ACCESS_TOKEN or add the existing local upstox_token.json, then restart or refresh.");
  $("connectionNote").classList.toggle("warn",!state.meta.token_configured || !!state.lastScanError);$("submitScan").disabled=!state.meta.token_configured;
  $("scanDialog").showModal();
}
$("sourceSelect").addEventListener("change",e=>{state.mode=e.target.value;state.selected=null;load();});
$("bannerAction").addEventListener("click",()=>{if(state.mode==="demo"){state.mode="real";state.selected=null;load();}else $("guideDialog").showModal();});
$("timeframes").addEventListener("click",e=>{const button=e.target.closest("button[data-tf]");if(!button)return;state.tf=button.dataset.tf;document.querySelectorAll("#timeframes button").forEach(b=>b.classList.toggle("selected",b===button));load();});
$("searchInput").addEventListener("input",renderTable);$("signalFilter").addEventListener("change",renderTable);
$("favoriteButton").addEventListener("click",()=>{const r=selectedRow();if(r)toggleFavorite(r);});
$("exportButton").addEventListener("click",()=>{if(state.selected)window.location.href=`/api/report?${query({symbol:state.selected})}`;});
$("refreshButton").addEventListener("click",async()=>{try{await refreshMeta();await load();notify("Snapshot list refreshed");}catch(e){notify(e.message);}});
$("scanButton").addEventListener("click",async()=>{try{await refreshMeta();openScan();}catch(e){notify(e.message);}});
$("guideButton").addEventListener("click",()=>{$("guideDialog").showModal();$("sidebar").classList.remove("open");$("sideOverlay").classList.remove("open");});
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>$(button.dataset.close).close()));
$("menuToggle").addEventListener("click",()=>{$("sidebar").classList.toggle("open");$("sideOverlay").classList.toggle("open");});
$("sideOverlay").addEventListener("click",()=>{$("sidebar").classList.remove("open");$("sideOverlay").classList.remove("open");});
document.querySelectorAll(".side-nav a").forEach(a=>a.addEventListener("click",()=>{$("sidebar").classList.remove("open");$("sideOverlay").classList.remove("open");}));
async function importLegacy(){try{const r=await api("/api/import-legacy",jsonPost({}));await refreshMeta();state.mode="real";if(!state.meta.available_timeframes.includes(state.tf)&&state.meta.available_timeframes.length)state.tf=state.meta.available_timeframes[0];document.querySelectorAll("#timeframes button").forEach(b=>b.classList.toggle("selected",b.dataset.tf===state.tf));state.selected=null;await load();notify(`Imported ${r.imported} snapshot(s); ${r.skipped} skipped.`);}catch(e){notify(e.message);}}
$("importButton").addEventListener("click",importLegacy);
$("importNav").addEventListener("click",()=>{$("sidebar").classList.remove("open");$("sideOverlay").classList.remove("open");importLegacy();});
$("scanForm").addEventListener("submit",async e=>{e.preventDefault();if(state.scanning)return;state.scanning=true;state.lastScanError="";const button=$("submitScan");button.disabled=true;button.textContent="Scanning…";try{
  const job=await api("/api/scans",jsonPost({symbol:$("scanSymbol").value,tf:$("scanTf").value}));
  while(true){await new Promise(resolve=>setTimeout(resolve,1200));const status=await api(`/api/scans/${encodeURIComponent(job.id)}`);
    if(status.status==="running"){button.textContent=`Scanning ${status.processed}/${status.total}…`;$("connectionNote").textContent=`Saved ${status.saved} · Failed ${status.failed} · Current: ${status.current || "starting"}`;continue;}
    const detail=status.message+(status.failure_symbols?.length ? ` Failed: ${status.failure_symbols.join(", ")}.` : "");
    if(status.status==="failed")throw new Error(detail);
    state.mode="real";state.tf=status.tf;state.selected=status.symbol;document.querySelectorAll("#timeframes button").forEach(b=>b.classList.toggle("selected",b.dataset.tf===state.tf));await refreshMeta();await load();
    if(status.status==="partial"){state.lastScanError=detail;$("connectionNote").textContent=detail;$("connectionNote").classList.add("warn");notify("Scan partially complete. Review the details.");}
    else{if($("scanDialog").open)$("scanDialog").close();notify("Scan complete. Check the candle dates before using them.");}break;
  }
}catch(error){state.lastScanError=error.message;$("connectionNote").textContent=error.message;$("connectionNote").classList.add("warn");if(!$("scanDialog").open)notify(error.message);}finally{state.scanning=false;button.disabled=!state.meta?.token_configured;button.textContent="Start scan";}});
(async()=>{try{await refreshMeta();state.mode=state.meta.real_count?"real":"demo";if(state.mode==="real"&&!state.meta.available_timeframes.includes("DAY")){state.tf=state.meta.available_timeframes[0];document.querySelectorAll("#timeframes button").forEach(b=>b.classList.toggle("selected",b.dataset.tf===state.tf));}await load();}catch(e){notify(e.message);}})();
