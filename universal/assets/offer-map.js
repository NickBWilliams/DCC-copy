/* Contractor route map and turn-by-turn navigation links.
   Loads third-party map tiles only after a deliberate contractor click.
   Lines are schematics; actual road mileage comes from server route data. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
  let route=null,leafletPromise=null,map=null;
  const valid=p=>p && Number.isFinite(Number(p.lat)) &&
    Number.isFinite(Number(p.lon)) &&
    Math.abs(Number(p.lat))<=90 && Math.abs(Number(p.lon))<=180 &&
    !(Number(p.lat)===0 && Number(p.lon)===0);
  const coordinate=p=>Number(p.lat).toFixed(6)+','+Number(p.lon).toFixed(6);
  const validStops=d=>(Array.isArray(d.itinerary)?d.itinerary:[]).filter(valid);
  const note=s=>{if(el('mapNote'))el('mapNote').textContent=s};

  function addNavigation(data){
    const target=el('mapLinks');
    if(!target)return;
    target.replaceChildren();
    const row=document.createElement('div');row.className='route-copy-row';
    const url=new URL(location.href);url.hash='mapSection';
    const input=document.createElement('input');input.readOnly=true;input.value=url.href;
    input.className='route-copy-field';input.setAttribute('aria-label','Complete private route link');
    const copy=document.createElement('button');copy.className='action';copy.textContent='Copy full route link';
    copy.onclick=async()=>{
      try{await navigator.clipboard.writeText(input.value);copy.textContent='Copied'}
      catch(_){input.focus();input.select();copy.textContent='Press Ctrl+C to copy'}
    };
    row.append(input,copy);target.append(row);
    const hint=document.createElement('p');hint.className='form-hint';
    hint.textContent=data.read_only?'One private, read-only link contains every day and stop. Keep it for your own use.':'One private link contains every day and stop. Keep it private; it also opens your invitation and expires with it.';
    target.append(hint);
    // Navigation is one destination at a time; the full itinerary stays here.
    const label=document.createElement('label');label.textContent='Navigate to a stop or return home';
    const select=document.createElement('select');select.setAttribute('aria-label','Navigation destination');
    const destinations=[...validStops(data),...(valid(data.home)?[{...data.home,venue:'Return to start'}]:[])];
    destinations.forEach((stop,i)=>{
      const option=document.createElement('option');option.value=String(i);
      option.textContent=(stop.day?'Day '+stop.day+' · ':'')+(stop.position?'Stop '+stop.position+' · ':'')+stop.venue;
      select.append(option);
    });
    const go=document.createElement('a');go.textContent='Navigate to selected stop ↗';go.target='_blank';go.rel='noopener noreferrer';
    const provider=document.createElement('select');provider.setAttribute('aria-label','Navigation app');
    for(const [value,name] of [['apple','Apple Maps'],['google','Google Maps'],['waze','Waze']]){
      const option=document.createElement('option');option.value=value;option.textContent=name;provider.append(option);
    }
    const change=()=>{
      const coords=coordinate(destinations[Number(select.value)]);
      go.href=provider.value==='apple'?'https://maps.apple.com/?'+new URLSearchParams({daddr:coords,dirflg:'d'}):
        provider.value==='waze'?'https://waze.com/ul?'+new URLSearchParams({ll:coords,navigate:'yes'}):
        'https://www.google.com/maps/dir/?'+new URLSearchParams({api:'1',destination:coords,travelmode:'driving'});
    };
    provider.onchange=change;
    select.onchange=change;if(destinations.length)change();
    label.append(select);target.append(label,provider,go);
  }

  function dailyChains(data){
    const groups=data.days?.length?data.days.map(d=>d.itinerary):[validStops(data)];
    return groups.map(stops=>[...(valid(data.home)?[data.home]:[]),...stops.filter(valid),...(valid(data.home)?[data.home]:[])]);
  }

  function loadLeaflet(){
    if(window.L)return Promise.resolve(window.L);
    if(leafletPromise)return leafletPromise;
    leafletPromise=new Promise((resolve,reject)=>{
      const link=document.createElement('link');
      link.rel='stylesheet';link.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.append(link);
      const script=document.createElement('script');
      script.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      script.async=true;
      script.onload=()=>window.L?resolve(window.L):reject(new Error('Map library did not start'));
      script.onerror=()=>reject(new Error('Could not load interactive map library'));
      document.head.append(script);
    });
    return leafletPromise;
  }

  async function openMap(){
    if(!route)return;
    const stops=validStops(route);
    if(!stops.length){
      note('No valid stop coordinates were saved for this route.');
      return;
    }
    if(!route.map_tiles?.url){
      note('An interactive map provider is not configured. Use the driving directions links below.');
      return;
    }
    const button=el('mapOpen');button.disabled=true;button.textContent='Opening map…';
    try{
      const L=await loadLeaflet();
      if(map){map.invalidateSize();return}
      el('routeMap').hidden=false;el('mapPreview').hidden=true;
      map=L.map(el('routeMap'),{scrollWheelZoom:false});
      L.tileLayer(route.map_tiles.url,{
        attribution:route.map_tiles.attribution||'© OpenStreetMap contributors',
        maxZoom:19,detectRetina:false
      }).addTo(map);
      const points=[];
      if(valid(route.home)){
        points.push([Number(route.home.lat),Number(route.home.lon)]);
        const icon=L.divIcon({className:'offer-pin offer-home',html:'⌂',iconSize:[30,30],iconAnchor:[15,15]});
        L.marker(points[0],{icon}).bindPopup('Route start / return').addTo(map);
      }
      stops.forEach((stop,i)=>{
        const position=[Number(stop.lat),Number(stop.lon)];
        points.push(position);
        const icon=L.divIcon({
          className:'offer-pin',html:String(i+1),
          iconSize:[30,30],iconAnchor:[15,15]});
        L.marker(position,{icon})
          .bindPopup('<strong>Stop '+(i+1)+': '+escapeHTML(stop.venue)+'</strong><div>'
            +escapeHTML(stop.address)+'</div>').addTo(map);
      });
      if(valid(route.home))points.push([Number(route.home.lat),Number(route.home.lon)]);
      dailyChains(route).forEach((chain,i)=>L.polyline(chain.map(p=>[Number(p.lat),Number(p.lon)]),{color:['#2375d8','#8b5cf6','#159575'][i%3],weight:3,dashArray:'7 9',opacity:.7}).addTo(map));
      map.fitBounds(L.latLngBounds(points),{padding:[26,26],maxZoom:13});
      window.setTimeout(()=>map.invalidateSize(),100);
      note('Numbered markers show the planned stop order. Dashed lines are not roads. Open driving directions for navigation.');
    }catch(err){
      el('routeMap').hidden=true;el('mapPreview').hidden=false;
      note('Interactive map could not load. You can still use the driving directions links below.');
      button.textContent='Try interactive map again';
    }finally{button.disabled=false}
  }


  function showSchematic(data){
    /* A clean, zoom-to-fit schematic from real stop coordinates; this is
       intentionally NOT a claim about road geometry or turn-by-turn travel. */
    const host=el('routeSchematic');
    if(!host)return;
    host.replaceChildren();
    const stops=validStops(data);
    if(!stops.length)return;
    const home=valid(data.home)?data.home:null;
    const coords=[...(home?[home]:[]),...stops];
    const longitudes=coords.map(p=>Number(p.lon)),latitudes=coords.map(p=>Number(p.lat));
    const west=Math.min(...longitudes),east=Math.max(...longitudes);
    const south=Math.min(...latitudes),north=Math.max(...latitudes);
    const xspan=Math.max(0.004,east-west),yspan=Math.max(0.004,north-south);
    const centerx=(west+east)/2,centery=(south+north)/2;
    const point=p=>[
      75 + ((Number(p.lon)-centerx)/xspan+0.5)*650,
      36 + (0.5-(Number(p.lat)-centery)/yspan)*170
    ];
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 800 280');
    svg.setAttribute('preserveAspectRatio','xMidYMid meet');
    svg.setAttribute('role','img');
    svg.setAttribute('aria-label','Schematic stop order, not road directions');
    const svgEl=(name,attributes)=>{
      const node=document.createElementNS('http://www.w3.org/2000/svg',name);
      Object.entries(attributes||{}).forEach(([key,value])=>node.setAttribute(key,String(value)));
      return node;
    };
    // Reversible depot-to-depot connector remains a schematic ONLY.
    const plotted=coords.map(point);
    dailyChains(data).forEach((chain,dayIndex)=>{
    const waypoints=chain.map(point);
    if(waypoints.length>1){
      const poly=svgEl('polyline',{
        points:waypoints.map(p=>p.map(v=>v.toFixed(2)).join(',')).join(' '),
        fill:'none',stroke:['#2378e7','#8b5cf6','#159575'][dayIndex%3],'stroke-width':'5',
        'stroke-linecap':'round','stroke-linejoin':'round','stroke-dasharray':'10 9',
        opacity:'0.91'
      });
      svg.append(poly);
    }
    });
    if(home){
      const [x,y]=plotted[0];
      svg.append(svgEl('circle',{cx:x,cy:y,r:15,fill:'#119671',stroke:'#fff','stroke-width':4}));
      const title=svgEl('title');title.textContent='Start and return';svg.lastChild.append(title);
    }
    stops.forEach((stop,index)=>{
      const [x,y]=point(stop);
      const marker=svgEl('circle',{cx:x,cy:y,r:16,fill:'#176ee4',stroke:'#fff','stroke-width':4});
      const title=svgEl('title');title.textContent='Stop '+(index+1)+': '+stop.venue;
      marker.append(title);svg.append(marker);
      const number=svgEl('text',{x,y:y+5.5,'text-anchor':'middle',
        'font-size':'15','font-weight':'800',fill:'#fff'});
      number.textContent=String(index+1);svg.append(number);
    });
    host.append(svg);
  }
  function onOffer(data){
    const stops=validStops(data);
    if(!stops.length)return;
    route=data;
    el('mapSection').hidden=false;
    el('mapCount').textContent=stops.length+' '+(stops.length===1?'stop':'stops');
    addNavigation(data);
    showSchematic(data);
    el('mapOpen').onclick=openMap;
    if(!data.map_tiles?.url){
      el('mapOpen').disabled=true;
      el('mapOpen').textContent='Map tiles not configured';
      note('The in-page map requires a licensed map-tile provider. Driving directions remain available below.');
    }
  }
  window.addEventListener('dcc-offer-loaded',event=>onOffer(event.detail));
})();
