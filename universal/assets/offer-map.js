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

  function googleSegments(data){
    const stops=validStops(data);
    if(!stops.length)return [];
    const home=valid(data.home)?data.home:null;
    const chain=[home||stops[0],...stops,home||stops[stops.length-1]];
    // Three intermediate waypoints per link for broad mobile compatibility.
    const chunks=[];
    for(let i=0;i<chain.length-1;i+=4){
      const segment=chain.slice(i,Math.min(i+5,chain.length));
      const query=new URLSearchParams({
        api:'1',origin:coordinate(segment[0]),
        destination:coordinate(segment[segment.length-1]),travelmode:'driving'
      });
      const middle=segment.slice(1,-1);
      if(middle.length)query.set('waypoints',middle.map(coordinate).join('|'));
      chunks.push('https://www.google.com/maps/dir/?'+query.toString());
    }
    return chunks;
  }

  function addNavigation(data){
    const target=el('mapLinks');
    if(!target)return;
    target.replaceChildren();
    const parts=googleSegments(data);
    if(!parts.length)return;
    const makeLink=(url,label)=>{
      const a=document.createElement('a');a.href=url;
      a.rel='noopener noreferrer';a.target='_blank';a.textContent=label;
      a.setAttribute('aria-label',label+' (opens Google Maps)');
      return a;
    };
    if(parts.length===1){
      target.append(makeLink(parts[0],'Open driving directions ↗'));
    }else{
      const details=document.createElement('details');
      details.className='navigation-parts';
      const summary=document.createElement('summary');
      summary.textContent='Open driving directions in '+parts.length+' parts';
      details.append(summary);
      const nav=document.createElement('div');
      nav.className='navigation-parts-links';
      parts.forEach((url,i)=>nav.append(makeLink(url,'Part '+(i+1)+' of '+parts.length+' ↗')));
      details.append(nav);target.append(details);
    }
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
      if(points.length>1)L.polyline(points,{
        color:'#2375d8',weight:3,dashArray:'7 9',opacity:.7
      }).addTo(map);
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
    const waypoints=home?[...plotted,plotted[0]]:plotted;
    if(waypoints.length>1){
      const poly=svgEl('polyline',{
        points:waypoints.map(p=>p.map(v=>v.toFixed(2)).join(',')).join(' '),
        fill:'none',stroke:'#2378e7','stroke-width':'5',
        'stroke-linecap':'round','stroke-linejoin':'round','stroke-dasharray':'10 9',
        opacity:'0.91'
      });
      svg.append(poly);
    }
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
