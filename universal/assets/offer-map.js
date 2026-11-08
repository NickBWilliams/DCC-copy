/* Whole-route map inside the invitation and read-only accepted view.
   Road paths use saved provider geometry; no invented straight-line routes. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const colors=['#2563eb','#7c3aed','#059669','#ea580c','#db2777','#087e8b'];
  const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const valid=p=>p&&Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&Math.abs(p.lat)<=90&&Math.abs(p.lon)<=180;
  const validGeometry=g=>g?.type==='LineString'&&Array.isArray(g.coordinates)&&g.coordinates.length>1&&g.coordinates.every(p=>Array.isArray(p)&&p.length>=2&&Number.isFinite(p[0])&&Number.isFinite(p[1])&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90);
  let route=null,days=[],selected=null,map=null,layers=null,leafletPromise=null,visiblePoints=[];
  const markers=new Map();
  const note=message=>el('mapNote').textContent=message;
  const format=v=>Number(v||0).toLocaleString(undefined,{maximumFractionDigits:1});
  function loadLeaflet(){
    if(window.L)return Promise.resolve(window.L);
    if(leafletPromise)return leafletPromise;
    leafletPromise=new Promise((resolve,reject)=>{
      if(!document.querySelector('[data-route-leaflet]')){
        const css=document.createElement('link');css.rel='stylesheet';css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.dataset.routeLeaflet='true';document.head.append(css);
      }
      const script=document.createElement('script');script.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      const timer=setTimeout(()=>{script.remove();leafletPromise=null;reject(Error('Map loading timed out'))},15000);
      script.onload=()=>{clearTimeout(timer);if(window.L)resolve(window.L);else{leafletPromise=null;reject(Error('Map unavailable'))}};
      script.onerror=()=>{clearTimeout(timer);script.remove();leafletPromise=null;reject(Error('Map unavailable'))};
      document.head.append(script);
    });return leafletPromise;
  }
  function fit(){
    if(map&&visiblePoints.length){map.invalidateSize();map.fitBounds(window.L.latLngBounds(visiblePoints),{padding:[35,35],maxZoom:15})}
  }
  function showDay(day){
    selected=day;
    for(const button of el('mapDays').querySelectorAll('button')){
      const active=button.dataset.day===(day==null?'all':String(day));
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    }
    const shown=days.filter(d=>day==null||d.day===day);
    const stops=shown.reduce((n,d)=>n+d.itinerary.length,0);
    const miles=shown.reduce((n,d)=>n+Number(d.road_miles||0),0);
    const hours=shown.reduce((n,d)=>n+Number(d.total_hours??d.driving_hours??0),0);
    el('mapDaySummary').textContent=(day==null?'Complete route':'Day '+day)+' · '+stops+' stops · '+format(miles)+' round-trip miles · '+format(hours)+' estimated hours';
    el('mapCount').textContent=stops+' stops';
    el('mapFit').textContent=day==null?'Fit full route':'Fit Day '+day;
    window.dispatchEvent(new CustomEvent('dcc-route-day-selected',{detail:{day}}));
    if(map)draw(shown);
  }
  function draw(shown){
    const L=window.L;layers.clearLayers();markers.clear();visiblePoints=[];
    if(valid(route.home)){
      const point=[route.home.lat,route.home.lon];visiblePoints.push(point);
      L.marker(point,{icon:L.divIcon({className:'offer-pin offer-home',html:'⌂',iconSize:[32,32],iconAnchor:[16,16]})})
        .bindPopup('Starting location · return here at the end of each day').addTo(layers);
    }
    let missing=0;
    for(const day of shown){
      const color=colors[(day.day-1)%colors.length];
      if(validGeometry(day.geometry)){
        const path=day.geometry.coordinates.map(p=>[p[1],p[0]]);visiblePoints.push(...path);
        L.polyline(path,{color,weight:selected==null?4:5,opacity:.85}).addTo(layers);
      }else missing++;
      day.itinerary.filter(valid).forEach(stop=>{
        const point=[stop.lat,stop.lon];visiblePoints.push(point);
        const number=Number(stop.position);
        const marker=L.marker(point,{icon:L.divIcon({className:'offer-day-pin',html:'<span style="background:'+color+'">'+number+'</span>',iconSize:[32,32],iconAnchor:[16,16]})});
        marker.bindPopup('<strong>Day '+day.day+' · Stop '+number+'</strong><br>'+escapeHTML(stop.venue)+'<br>'+escapeHTML(stop.address));
        marker.on('click',()=>{
          const card=[...document.querySelectorAll('#stops .stop-card')].find(node=>Number(node.dataset.position)===number);
          document.querySelectorAll('#stops .stop-card').forEach(node=>node.classList.toggle('map-selected-stop',node===card));
        });
        marker.addTo(layers);markers.set(number,marker);
      });
    }
    fit();
    note(missing?'All saved stops are shown. Road paths are unavailable for '+missing+' '+(missing===1?'day':'days')+'; no straight-line paths are substituted.':
      (route.demo?'Demo locations and estimates are fictional. ':'')+'Road paths show the saved stop order and return trip. Select a day or a numbered stop to explore.');
  }
  async function openMap(){
    if(!route)return;
    el('mapRetry').hidden=true;el('mapFit').disabled=true;
    if(!route.map_tiles?.url){note('Map provider is not configured. Your full itinerary remains below.');return}
    note('Loading your complete route map…');
    try{
      const L=await loadLeaflet();
      if(map)map.remove();
      map=L.map(el('routeMap'),{scrollWheelZoom:false});
      L.tileLayer(route.map_tiles.url,{attribution:route.map_tiles.attribution||'© OpenStreetMap contributors',maxZoom:19})
        .on('tileerror',()=>note('Some map tiles could not load. Saved route paths and stop details are still available.')).addTo(map);
      layers=L.layerGroup().addTo(map);el('mapFit').disabled=false;showDay(selected);
    }catch(_){note('Map could not load. Retry here; your complete stop list remains below.');el('mapRetry').hidden=false}
  }
  window.addEventListener('dcc-route-stop-selected',event=>{
    const number=Number(event.detail.position),marker=markers.get(number);
    if(map&&marker){map.setView(marker.getLatLng(),Math.max(map.getZoom(),14));marker.openPopup();el('mapSection').scrollIntoView({behavior:'smooth',block:'start'})}
  });
  window.addEventListener('dcc-offer-loaded',event=>{
    route=event.detail;selected=null;
    const all=(route.itinerary||[]).filter(valid);
    if(!all.length)return;
    days=route.days?.length?route.days.map(d=>({...d,itinerary:d.itinerary||[]})):[{day:1,itinerary:all,road_miles:route.road_miles,driving_hours:route.driving_hours,total_hours:route.total_hours,geometry:route.geometry}];
    el('mapSection').hidden=false;
    el('mapDays').replaceChildren();
    for(const day of [null,...(days.length>1?days.map(d=>d.day):[])]){
      const button=document.createElement('button');button.type='button';button.dataset.day=day==null?'all':String(day);
      button.textContent=day==null?(days.length>1?'All days':'Full route'):'Day '+day;button.style.setProperty('--day-color',day==null?'#19365b':colors[(day-1)%colors.length]);
      button.onclick=()=>showDay(day);el('mapDays').append(button);
    }
    el('mapFit').onclick=fit;el('mapRetry').onclick=openMap;showDay(null);openMap();
  });
})();
