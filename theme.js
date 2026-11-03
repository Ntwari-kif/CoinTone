/* Light / dark mode. Loaded in <head> so the right theme is set before the page paints (no flash).
   Remembers the choice on this device; if never chosen, follows the phone's system setting. */
(function(){
  var K="pv_theme",root=document.documentElement,mq=window.matchMedia?matchMedia("(prefers-color-scheme: dark)"):null;
  function saved(){try{var s=localStorage.getItem(K);return s==="dark"||s==="light"?s:null}catch(e){return null}}
  function apply(t){root.setAttribute("data-theme",t);var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content",t==="dark"?"#050d21":"#0a1a3f")}
  function icons(){var i=window.themeIcon();document.querySelectorAll("[data-theme-btn]").forEach(function(b){b.innerHTML=i})}
  var A='<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  var SUN=A+'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
      MOON=A+'<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  window.themeIcon=function(){return root.getAttribute("data-theme")==="dark"?SUN:MOON};
  window.toggleTheme=function(){var t=root.getAttribute("data-theme")==="dark"?"light":"dark";try{localStorage.setItem(K,t)}catch(e){}apply(t);icons()};
  apply(saved()||(mq&&mq.matches?"dark":"light"));
  if(mq&&mq.addEventListener)mq.addEventListener("change",function(e){if(!saved()){apply(e.matches?"dark":"light");icons()}});
  document.addEventListener("DOMContentLoaded",icons);
})();
