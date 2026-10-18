/* Light / dark mode. Loaded in <head> so the right theme is set before the page paints (no flash).
   Remembers the choice on this device; if never chosen, follows the phone's system setting. */
(function(){
  var K="pv_theme",root=document.documentElement,mq=window.matchMedia?matchMedia("(prefers-color-scheme: dark)"):null;
  function saved(){try{var s=localStorage.getItem(K);return s==="dark"||s==="light"?s:null}catch(e){return null}}
  function apply(t){root.setAttribute("data-theme",t);var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content",t==="dark"?"#050d21":"#0a1a3f")}
  function icons(){var i=window.themeIcon();document.querySelectorAll("[data-theme-btn]").forEach(function(b){b.textContent=i})}
  window.themeIcon=function(){return root.getAttribute("data-theme")==="dark"?"☀️":"🌙"};
  window.toggleTheme=function(){var t=root.getAttribute("data-theme")==="dark"?"light":"dark";try{localStorage.setItem(K,t)}catch(e){}apply(t);icons()};
  apply(saved()||(mq&&mq.matches?"dark":"light"));
  if(mq&&mq.addEventListener)mq.addEventListener("change",function(e){if(!saved()){apply(e.matches?"dark":"light");icons()}});
  document.addEventListener("DOMContentLoaded",icons);
})();
