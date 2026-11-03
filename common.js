const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(n,c="")=>(c?c+" ":"")+Number(n||0).toLocaleString(undefined,{maximumFractionDigits:2});
const hm=t=>new Date(t).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
const dstr=t=>t?new Date(t).toLocaleDateString(undefined,{day:"numeric",month:"short",year:"numeric"}):"—";
const dtm=t=>new Date(t).toLocaleString([],{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
const todayStr=()=>{const d=new Date();return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0")};
async function api(url,body){
  const r=await fetch(url,{method:body===undefined?"GET":"POST",headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),credentials:"same-origin"});
  let d={};try{d=await r.json()}catch(e){}
  if(r.status===401&&!url.includes("/api/login")){location.href="/login";throw new Error("signed out")}
  if(!r.ok){const e=new Error(d.error||"Something went wrong");e.status=r.status;throw e}
  return d}
function toast(m){const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",3200)}
async function logout(){try{await api("/api/logout",{})}catch(e){}location.href="/login"}
/* live updates: every panel re-reads the shared database every few seconds */
function live(fn,ms=4000){let busy=false;const tick=async()=>{if(busy||document.hidden)return;busy=true;try{await fn()}catch(e){}busy=false};
  tick();setInterval(tick,ms);document.addEventListener("visibilitychange",tick)}
function topbar(title){return `<div class="top"><div class="brand"><img class="logo" src="/static/icon-192.png" alt="">${esc(title)}<small>CTone</small></div><span class="who" id="who"></span><button class="ico" data-install-btn hidden onclick="installApp()" title="Install app" aria-label="Install app">⬇️</button><button class="ico" data-theme-btn onclick="toggleTheme()" title="Switch light / dark" aria-label="Switch light or dark mode">${themeIcon()}</button><button onclick="pwd()">Password</button><button onclick="logout()">Sign out</button></div>`}
function pwd(){const o=prompt("Current password:");if(!o)return;const n=prompt("New password (6+ characters):");if(!n)return;
  api("/api/password",{old:o,new:n}).then(()=>toast("Password changed")).catch(e=>toast(e.message))}
