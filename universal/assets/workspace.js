/* DCC Universal • workspace interface
   Presents real company-scoped API data. No invented demo routes, payouts,
   maps or external integrations. Existing workflow handlers stay intact. */
(function(){
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
  const icon=(id,cls='icon')=>'<svg class="'+cls+'" aria-hidden="true"><use href="#i-'+id+'"></use></svg>';
  const money=value=>{
    const n=Number(value);return Number.isFinite(n)
      ?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n)
      :'—';
  };
  const filters=[
    {id:'queue',label:'Queue',statuses:['unassigned','ready','flagged','declined']},
    {id:'offers',label:'Offers',statuses:['sent','countered']},
    {id:'accepted',label:'Accepted',statuses:['accepted']},
    {id:'active',label:'Active',statuses:['finalized']},
    {id:'completed',label:'Completed',statuses:['completed']},
    {id:'overview',label:'All',statuses:null}
  ];
  const labels={
    unassigned:'Unassigned',ready:'Ready',flagged:'Flagged',sent:'Offer sent',
    countered:'Countered',accepted:'Accepted',declined:'Declined',
    finalized:'In progress',completed:'Completed',cancelled:'Cancelled'
  };
  let view='dashboard',sort='newest',staffProfile=null;
  function inFilter(route,filter){
    return !filter.statuses||filter.statuses.includes(route.status);
  }
  function filterOf(id){
    return filters.find(f=>f.id===id)||filters[0];
  }
  function badge(status){
    return '<span class="status-pill status-'+esc(status)+'">'+esc(labels[status]||status)+'</span>';
  }
  function renderStats(){
    const data=[
      ['Unassigned work',routes.filter(r=>['unassigned','flagged','declined'].includes(r.status)).length,'layers','slate'],
      ['Ready to offer',routes.filter(r=>r.status==='ready').length,'calendar',''],
      ['In progress',routes.filter(r=>r.status==='finalized').length,'play','amber'],
      ['Accepted',routes.filter(r=>r.status==='accepted').length,'user-check','teal'],
      ['Completed',routes.filter(r=>r.status==='completed').length,'check','teal']
    ];
    el('metrics').innerHTML=data.map(([title,num,glyph,color])=>
      '<div class="metric"><div class="metric-icon '+color+'">'+icon(glyph)+'</div><div class="metric-copy"><small>'
      +esc(title)+'</small><strong>'+num+'</strong></div></div>').join('');
  }
  function renderTabs(){
    const container=el('tabs');
    container.replaceChildren(...filters.map(f=>{
      const count=routes.filter(r=>inFilter(r,f)).length;
      const button=document.createElement('button');
      button.type='button';button.className=stage===f.id?'active':'';
      button.setAttribute('aria-pressed',String(stage===f.id));
      button.innerHTML=esc(f.label)+' <span class="tab-count">'+count+'</span>';
      button.onclick=()=>{stage=f.id;render();};
      return button;
    }));
  }
  function selectRoute(id){
    selected=id;render();loadHistory();loadItinerary();
  }
  function renderCards(){
    const text=el('search').value.trim().toLowerCase();
    const current=filterOf(stage);
    const result=routes.filter(r=>inFilter(r,current)&&
      [r.id,r.title,r.contractor,r.status].join(' ').toLowerCase().includes(text))
      .sort((a,b)=>sort==='oldest'?a.id-b.id:b.id-a.id);
    el('heading').textContent=(current.label==='All'?'All':'')+ (current.label==='All'?' routes':current.label+' routes')+' ('+result.length+')';
    const list=el('routes');list.replaceChildren();
    if(!result.length){
      const empty=document.createElement('div');empty.className='empty-state';
      empty.innerHTML='<div class="empty-icon">'+icon('route')+'</div><strong>'
        +(routes.length?'Nothing in this view':'Your workspace is ready')
        +'</strong><p>'+(routes.length?'Try another stage or change your search to find the work you need.':'Create a route to start building and tracking work in one place.')
        +'</p><button type="button" class="primary button-icon">'+icon('plus')+' Create route</button>';
      empty.querySelector('button').onclick=()=>setView('builder');list.append(empty);
      return;
    }
    for(const r of result){
      const row=document.createElement('button');
      row.type='button';row.className='route'+(selected===r.id?' selected':'');
      row.setAttribute('aria-pressed',String(selected===r.id));
      row.setAttribute('aria-label',r.title+', '+r.stops+' stops, '+(labels[r.status]||r.status));
      row.innerHTML='<div class="route-map-icon">'+icon('pin')+'</div>'
        +'<div class="route-main"><strong>'+esc(r.title)+'</strong><small>#'+esc(r.id)
        +' · '+esc(r.contractor||'Contractor not selected')+'</small></div>'
        +'<div class="route-meta"><div class="route-datum">'+Number(r.stops||0)+'<span>Stops</span></div>'
        +'<div class="route-datum">'+money(r.price)+'<span>Est. payout</span></div>'
        +badge(r.status)+'</div>';
      row.onclick=()=>selectRoute(r.id);list.append(row);
    }
  }
  const actionsFor={
    unassigned:[['Prepare route',()=>act('prepare')],['Flag for review',()=>act('flag')],['Cancel route',()=>cancelRoute(routes.find(r=>r.id===selected))]],
    ready:[['Create contractor offer link',()=>issueOffer(routes.find(r=>r.id===selected))],['Flag for review',()=>act('flag')],['Cancel route',()=>cancelRoute(routes.find(r=>r.id===selected))]],
    flagged:[['Return to queue',()=>act('requeue')],['Cancel route',()=>cancelRoute(routes.find(r=>r.id===selected))]],
    sent:[['Revoke offer link',()=>revokeOffer(routes.find(r=>r.id===selected))]],
    countered:[['Approve counteroffer',()=>act('approve_counter')],['Decline counteroffer',()=>act('reject_counter')]],
    accepted:[['Finalize accepted route',()=>act('finalize')]],
    finalized:[['Mark route completed',()=>act('complete')]],
    declined:[['Prepare route again',()=>act('prepare')],['Return to queue',()=>act('requeue')],['Cancel route',()=>cancelRoute(routes.find(r=>r.id===selected))]]
  };
  function renderDetails(){
    const route=routes.find(r=>r.id===selected);
    const area=el('details');
    const events=el('events');
    if(!route){
      area.innerHTML='<div class="detail-blank">'+icon('map')+'<strong>Select a route to review</strong><span>Stops, pricing, status and next actions will appear here.</span></div>';
      events.replaceChildren();return;
    }
    area.innerHTML='<div class="detail-toplabel">Route details</div>'
      +'<div class="detail-heading"><div><h2>'+esc(route.title)+'</h2>'
      +'<div class="subtle-note">Route #'+esc(route.id)+' · '+esc(route.contractor||'No contractor')+'</div></div>'
      +badge(route.status)+'</div>'
      +'<div class="detail-meta">'
      +'<div class="detail-datum">'+Number(route.stops||0)+'<small>Stops</small></div>'
      +'<div class="detail-datum">'+money(route.price)+'<small>Est. payout</small></div>'
      +'<div class="detail-datum" id="detailDistance">—<small>Road miles</small></div>'
      +'</div>'
      +'<div id="itinerary"><div class="subtle-note">Loading stop details…</div></div>'
      +'<div id="actions" class="detail-actions"></div>';
    const menu=actionsFor[route.status]||[];
    const target=el('actions');
    if(!menu.length){
      const p=document.createElement('p');p.className='subtle-note';
      p.textContent=route.status==='completed'?'This route is complete.':route.status==='cancelled'?'This route was cancelled.':'No actions available at this stage.';
      target.append(p);return;
    }
    const [primary,...secondary]=menu;
    const mainAction=document.createElement('button');
    mainAction.type='button';
    mainAction.className=route.status==='sent'?'danger':'primary';
    mainAction.textContent=primary[0];mainAction.onclick=primary[1];
    target.append(mainAction);
    if(secondary.length){
      const dropdown=document.createElement('select');
      dropdown.setAttribute('aria-label','More route actions');
      dropdown.innerHTML='<option value="">More actions…</option>';
      secondary.forEach(([label],index)=>{
        const option=document.createElement('option');
        option.value=String(index);option.textContent=label;
        dropdown.append(option);
      });
      dropdown.onchange=()=>{
        if(dropdown.value==='')return;
        const action=secondary[Number(dropdown.value)];
        dropdown.value='';
        if(action)action[1]();
      };
      const div=document.createElement('div');div.className='route-more';div.append(dropdown);target.append(div);
    }
    if(['ready','sent'].includes(route.status)){
      const p=document.createElement('p');p.className='subtle-note';
      p.textContent=route.status==='ready'
        ?'An offer link will be created for you to share. Email/SMS delivery is not automated yet.'
        :'The contractor can respond through the existing offer link until it is revoked.';
      target.append(p);
    }
  }
  function render(){
    if(!el('metrics')||!el('routes'))return;
    if(!selected&&routes.length)selected=(routes.find(r=>['unassigned','ready','flagged','declined'].includes(r.status))||routes[0]).id;
    renderStats();
    renderTabs();
    renderCards();
    renderDetails();
    renderAnalytics();
  }
  function renderAnalytics(){
    if(!el('analyticsPanel'))return;
    const completed=routes.filter(r=>r.status==='completed');
    const accepted=routes.filter(r=>r.status==='accepted'||r.status==='finalized'||r.status==='completed');
    const paid=completed.reduce((n,r)=>n+Number(r.price||0),0);
    el('analyticsSummary').innerHTML=[
      ['All routes',routes.length],
      ['Accepted or beyond',accepted.length],
      ['Completed route value',money(paid)]
    ].map(([label,value])=>'<div class="analytic-tile"><span class="muted">'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('');
  }
  function plotStops(plan){
    const points=[plan.home,...plan.itinerary].filter(p=>
      p&&Number.isFinite(Number(p.lon))&&Number.isFinite(Number(p.lat)));
    if(points.length<2)return '';
    const west=Math.min(...points.map(p=>Number(p.lon)));
    const east=Math.max(...points.map(p=>Number(p.lon)));
    const south=Math.min(...points.map(p=>Number(p.lat)));
    const north=Math.max(...points.map(p=>Number(p.lat)));
    const lonSpan=Math.max(.001,east-west),latSpan=Math.max(.001,north-south);
    const ratio=(value,low,span)=>(Number(value)-low)/span;
    const coords=points.map(p=>[
      26+ratio(p.lon,west,lonSpan)*546,
      169-ratio(p.lat,south,latSpan)*139
    ]);
    const path=[...coords,coords[0]].map(p=>p.map(v=>v.toFixed(2)).join(',')).join(' ');
    const stops=coords.slice(1).map(([x,y],i)=>
      '<g><circle cx="'+x+'" cy="'+y+'" r="8" fill="#2071dd" stroke="#fff" stroke-width="3"/>'
      +'<text x="'+x+'" y="'+(y-14)+'" text-anchor="middle" font-size="10" fill="#315679" font-weight="700">'+(i+1)+'</text></g>').join('');
    return '<div class="route-viz"><svg viewBox="0 0 600 195" role="img" aria-label="Diagram showing approximate stop sequence, not the street route">'
      +'<defs><pattern id="paper-grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M 28 0 L 0 0 0 28" fill="none" stroke="#dce8f4" stroke-width="1"/></pattern></defs>'
      +'<rect width="600" height="195" fill="url(#paper-grid)"/>'
      +'<polyline points="'+path+'" fill="none" stroke="#267be3" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"/>'
      +stops+'<circle cx="'+coords[0][0]+'" cy="'+coords[0][1]+'" r="10" fill="#0da780" stroke="#fff" stroke-width="3"/>'
      +'</svg><div class="viz-caption">Approximate stop sequence · schematic only, not a road map</div></div>';
  }
  async function loadItinerary(){
    const id=selected;
    if(!id||!el('itinerary'))return;
    try{
      const p=await request('/api/dispatch/routes/'+id);
      if(selected!==id||!el('itinerary'))return;
      const distance=el('detailDistance');
      if(distance&&p.road_miles!==undefined){
        distance.innerHTML=esc(Number(p.road_miles).toLocaleString(undefined,{maximumFractionDigits:1}))+'<small>Road miles</small>';
      }
      const list=(p.itinerary||[]).map(s=>'<div class="stopline"><div class="stopnum">'
        +esc(s.position)+'</div><div><strong>'+esc(s.venue)+'</strong><small>'+esc(s.address)+'</small>'
        +(s.tasks||[]).map(t=>'<small>'+esc(t.external_task_id)+' · '+esc(t.task_type)+'</small>').join('')
        +'</div></div>').join('');
      el('itinerary').innerHTML=plotStops(p)+'<h3 class="detail-section-title">Stops ('+(p.itinerary||[]).length+')</h3>'
        +'<div class="itinerary-list">'+list+'</div>'
        +'<p class="subtle-note" style="margin-top:12px">Estimated driving time: '+esc(p.driving_hours)
        +' hours · Optimized by '+esc(p.optimization||p.provider||'configured provider')+'</p>';
    }catch(e){
      if(selected===id&&el('itinerary')){
        el('itinerary').innerHTML='<div class="page-note">No detailed itinerary was recorded for this route. Legacy routes may only have a stop count.</div>';
      }
    }
  }
  function updateViewHeader(){
    const copy={
      dashboard:['Dispatch Command Center','Plan, assign, and manage your operations from one workspace.'],
      bulk:['Plan routes in bulk','Match hundreds of jobs to nearby contractors, then confirm actual road routes.'],
      routes:['All routes','Review your work from preparation through completion.'],
      builder:['Create a route','Build a complete round trip, find nearby contractors, and calculate pricing.'],
      contractors:['Your contractors','One organized workforce directory for this company.'],
      integrations:['Connected workspace','Bring this company’s authorized systems into one place.'],
      analytics:['Operations at a glance','A live overview of the route data in your workspace.'],
      settings:['Workspace settings','Your account, routing service, and company configuration.']
    };
    const [title,subtitle]=copy[view]||copy.dashboard;
    el('pageTitle').textContent=title;el('pageSubtitle').textContent=subtitle;
    el('create').hidden=['builder','integrations','bulk'].includes(view);
    el('refresh').hidden=!['dashboard','routes'].includes(view);
    el('searchWrap').hidden=!['dashboard','routes'].includes(view);
  }
  function setView(next){
    if(!['dashboard','routes','builder','bulk','contractors','integrations','analytics','settings'].includes(next))return;
    view=next;
    for(const id of ['builder','bulkPanel','rosterPanel','integrationsPanel','analyticsPanel','settingsPanel']){
      const target=el(id);if(target)target.hidden=true;
    }
    el('routesWorkspace').hidden=!['dashboard','routes'].includes(next);
    el('metrics').hidden=!['dashboard','routes'].includes(next);
    if(next==='routes'){stage='overview';render();}
    if(next==='dashboard'){stage='queue';render();}
    const section={
      builder:'builder',bulk:'bulkPanel',contractors:'rosterPanel',integrations:'integrationsPanel',
      analytics:'analyticsPanel',settings:'settingsPanel'
    }[next];
    if(section)el(section).hidden=false;
    if(next==='contractors'){loadRoster();loadSources();}
    if(next==='integrations')loadIntegrations();
    if(next==='settings')loadSettings();
    for(const item of document.querySelectorAll('[data-view]')){
      const active=item.dataset.view===next||(['builder','bulk'].includes(next)&&item.dataset.view==='routes');
      item.classList.toggle('active',active);
      item.setAttribute('aria-current',active?'page':'false');
    }
    updateViewHeader();
    closeMobile();
    window.scrollTo({top:0,behavior:'auto'});
  }
  async function loadSettings(){
    const output=el('settingsInfo');
    if(!output)return;
    const org=staffProfile?.organization||'—';
    const email=staffProfile?.email||'—';
    const role=staffProfile?.role||'—';
    let routing='Not configured yet';
    try{
      const state=await request('/api/routing/status');
      if(state.ready)routing=(state.provider==='heigit'?'HeiGIT':state.provider==='osrm'?'Independent OSRM':'Test routing')+' · '+state.optimization;
    }catch(e){routing='Unavailable'}
    output.innerHTML=[
      ['Current company',org],['Signed in as',email],['Access role',role],
      ['Road routing',routing],['Public access','Private SSH tunnel preview']
    ].map(([name,value])=>'<div class="row"><span class="muted">'+esc(name)+'</span><strong>'+esc(value)+'</strong></div>').join('');
  }
  function sessionReady(profile){
    staffProfile=profile||null;
    const details=el('profileWrap');
    if(details)details.hidden=!profile;
    const avatar=el('avatar');
    if(avatar&&profile){
      avatar.textContent=profile.email.substring(0,2).toUpperCase();
    }
    const identity=el('identity');
    if(identity&&profile){
      identity.textContent=profile.email;
    }
    el('roleText').textContent=profile?.role||'';
    setView(view);
  }
  function enhancePanels(){
    const roster=el('rosterPanel');
    if(roster&&!roster.dataset.enhanced){
      roster.dataset.enhanced='1';
      const form=roster.querySelector('.formgrid');
      const actions=form?.nextElementSibling;
      if(form&&actions&&actions.classList.contains('actions')){
        const detail=document.createElement('details');
        detail.className='disclosure';
        detail.innerHTML='<summary>Add or edit a contractor</summary>';
        form.parentNode.insertBefore(detail,form);
        detail.append(form,actions);
        const save=el('saveContractor');
        if(save)save.addEventListener('click',()=>{if(!detail.open)detail.open=true});
      }
      const sections=[...roster.querySelectorAll('.workspace-tools')];
      for(const [i,section] of sections.entries()){
        const detail=document.createElement('details');
        detail.className='disclosure';
        detail.innerHTML='<summary>'+(i===0?'Demo roster tools':'Import from an existing contractor base')+'</summary>';
        section.parentNode.insertBefore(detail,section);
        detail.append(section);
      }
    }
    const integrations=el('integrationsPanel');
    if(integrations&&!integrations.dataset.enhanced){
      integrations.dataset.enhanced='1';
      const mappingInputs=[...integrations.querySelectorAll('.integration-subhead')];
      if(mappingInputs.length){
        const start=mappingInputs[0];
        const toMove=[];
        let next=start;
        while(next&&next!==integrations.querySelector('.integration-actions')){
          const following=next.nextElementSibling;
          toMove.push(next);
          next=following;
        }
        const details=document.createElement('details');details.className='disclosure';
        details.id='mappingAdvanced';details.innerHTML='<summary>Field mapping and status rules</summary>';
        start.parentNode.insertBefore(details,start);
        toMove.forEach(node=>details.append(node));
        details.open=false;
      }
    }
  }
  function closeMobile(){
    el('sidebar')?.classList.remove('open');
    const scrim=el('mobileScrim');if(scrim)scrim.hidden=true;
  }
  function wire(){
    enhancePanels();
    for(const btn of document.querySelectorAll('[data-view]')){
      btn.onclick=()=>setView(btn.dataset.view);
    }
    el('openIntegrationBanner').onclick=()=>setView('integrations');
    el('mobileMenu').onclick=()=>{
      const expanded=el('sidebar').classList.toggle('open');
      el('mobileScrim').hidden=!expanded;
    };
    el('mobileScrim').onclick=closeMobile;
    el('routeSort').onchange=event=>{sort=event.target.value;renderCards();};
    el('search').oninput=()=>{
      if(!['dashboard','routes'].includes(view))setView('dashboard');
      renderCards();
    };
    window.addEventListener('keydown',e=>{
      if(e.key==='Escape')closeMobile();
      if(e.key==='/'&&!/^(input|textarea|select)$/i.test(document.activeElement?.tagName||'')){
        if(['dashboard','routes'].includes(view)){e.preventDefault();el('search').focus();}
      }
    });
    updateViewHeader();
    render();
    window.dispatchEvent(new Event('dcc-ui-ready'));
  }
  window.dccUI={
    render,loadItinerary,setView,sessionReady,
    currentView:()=>view
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',wire,{once:true});
  else wire();
})();
