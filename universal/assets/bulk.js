/* Private bulk planner. Previews use aerial proximity only; road pricing is verified on save. */
(() => {
'use strict';
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let tasks=[],previewData=null,isDemo=false,running=false,stop=false,all=false;
const pause=ms=>new Promise(done=>setTimeout(done,ms));
async function api(path,method='GET',body){
 const r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Server error ('+r.status+')');
 return d;
}
const msg=(id,s)=>$(id).textContent=s;
function invalidate(){
 if(running)return;previewData=null;$('bulkResults').hidden=true;$('bulkCreate').disabled=true;
 msg('bulkPreviewStatus',tasks.length?'Ready to preview. No work has been saved.':'');
}
function csvRows(input){
 const result=[],row=[];let field='',quoted=false;
 const source=input.replace(/^\uFEFF/,'');
 for(let i=0;i<source.length;i++){
  const c=source[i];
  if(quoted){
   if(c==='"'&&source[i+1]==='"'){field+='"';i++}else if(c==='"')quoted=false;else field+=c;
  }else if(c==='"'){if(field)throw Error('Unexpected quote in CSV');quoted=true}
  else if(c===','){row.push(field);field=''}
  else if(c==='\n'||c==='\r'){if(c==='\r'&&source[i+1]==='\n')i++;row.push(field);field='';if(row.some(s=>s.trim()))result.push([...row]);row.length=0}
  else field+=c;
 }
 if(quoted)throw Error('Unclosed quoted CSV field');
 row.push(field);if(row.some(s=>s.trim()))result.push([...row]);return result;
}
function parseCSV(contents){
 const rows=csvRows(contents);
 if(rows.length<2)throw Error('Add a CSV header and at least one task');
 if(rows.length>2001)throw Error('Maximum 2,000 task lines per batch');
 const names=rows[0].map(s=>s.trim().toLowerCase().replace(/[ -]+/g,'_'));
 const aliases={
  external_task_id:['external_task_id','task_id','job_id','ticket_id','work_order_id','task','id'],
  venue:['venue','venue_name','site','site_name','location_name','business_name'],
  address:['address','full_address','street_address','service_address','location_address'],
  lat:['latitude','lat','site_lat','task_lat'],lon:['longitude','lon','lng','long','site_lon','task_lon'],
  task_type:['task_type','service','job_type','work_type','type']
 };
 const idx={};for(const [k,opts] of Object.entries(aliases)){
  idx[k]=names.findIndex(n=>opts.includes(n));if(idx[k]<0&&k!=='task_type')throw Error('Missing CSV column: '+k);
 }
 const value=(row,key)=>idx[key]<0?'':String(row[idx[key]]||'').trim(),used=new Set();
 return rows.slice(1).map((row,i)=>{
  const external_task_id=value(row,'external_task_id'),venue=value(row,'venue'),address=value(row,'address');
  const lat=value(row,'lat'),lon=value(row,'lon');
  if(!external_task_id||!venue||!address||!lat||!lon)throw Error('Missing fields on row '+(i+2));
  if(used.has(external_task_id.toLowerCase()))throw Error('Duplicate task ID on row '+(i+2));
  used.add(external_task_id.toLowerCase());
  const latitude=Number(lat),longitude=Number(lon);
  if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||Math.abs(latitude)>90||Math.abs(longitude)>180||(latitude===0&&longitude===0))
   throw Error('Invalid coordinates on row '+(i+2));
  return{external_task_id,venue,address,lat:latitude,lon:longitude,task_type:value(row,'task_type')||'Service'};
 });
}
async function onFile(){
 if(running)return;invalidate();tasks=[];isDemo=false;
 const file=$('bulkFile').files?.[0];
 if(!file){msg('bulkInputCount','No tasks loaded yet.');return}
 try{
  if(file.size>2000000)throw Error('File exceeds 2 MB');
  tasks=parseCSV(await file.text());
  msg('bulkInputCount',tasks.length+' tasks loaded. No work has been saved.');
 }catch(e){msg('bulkInputCount','CSV issue: '+e.message)}
}
async function demo(){
 if(running)return;
 if(!confirm('Load 100 fictional contractors into this private company roster and 300 fictional task locations across 50 states? No actual dispatch or offers.'))return;
 $('bulkDemo').disabled=true;
 try{
  const added=await api('/api/contractors/demo-seed','POST');
  const fake=await api('/api/dispatch/bulk/demo');
  tasks=fake.tasks;isDemo=true;$('bulkFile').value='';$('bulkMaxStops').value='2';invalidate();
  msg('bulkInputCount',tasks.length+' fictional tasks loaded. '+added.created+' demo contractors added; '+added.already_present+' already present.');
 }catch(e){msg('bulkInputCount','Demo unavailable: '+e.message)}
 finally{$('bulkDemo').disabled=false}
}
function payload(){
 const limit=Number($('bulkMaxStops').value);
 if(!Number.isInteger(limit)||limit<1||limit>30)throw Error('Stops per route must be 1–30');
 return{tasks,max_stops_per_route:limit,include_training:$('bulkIncludeTraining').checked,include_demo:true};
}
function statistic(label,num){
 const box=document.createElement('div');box.className='bulk-summary-item';
 const strong=document.createElement('strong');strong.textContent=num;
 const small=document.createElement('small');small.textContent=label;
 box.append(strong,small);return box;
}
function draw(){
 const p=previewData;if(!p)return;
 $('bulkSummary').replaceChildren(
  statistic('Matched tasks',p.matched_tasks),statistic('Proposed routes',p.proposed_routes),
  statistic('Unmatched tasks',p.unmatched_tasks),statistic('Already reserved',p.reserved_tasks));
 msg('bulkResultsTitle',p.proposed_routes+' route groups ready for review');
 msg('bulkResultsNotice',p.notice);
 $('bulkRouteList').replaceChildren();
 for(const route of(all?p.routes:p.routes.slice(0,12))){
  const row=document.createElement('div');row.className='bulk-route-row';
  row.innerHTML='<div class="bulk-route-title"><strong>'+esc(route.title)+'</strong>'
    +'<span class="source-badge">'+(route.is_demo?'DEMO ONLY':'Company contractor')+'</span>'
    +'<small>'+esc(route.contractor_external_id)+' · '+esc(route.contractor_source)+'</small></div>'
    +'<div class="bulk-route-count"><strong>'+route.stop_count+'</strong><small>stops</small></div>'
    +'<div class="bulk-route-count"><strong>'+route.task_count+'</strong><small>tasks</small></div>'
    +'<div class="bulk-route-count"><strong>'+route.aerial_to_cluster_miles+'</strong><small>aerial mi*</small></div>';
  $('bulkRouteList').append(row);
 }
 $('bulkShowAll').hidden=p.routes.length<=12;
 $('bulkShowAll').textContent=all?'Show fewer routes':'Show all '+p.routes.length+' routes';
 const issues=[...p.unmatched,...p.reserved];
 msg('bulkIssuesTitle',issues.length?'Review '+issues.length+' unmatched or reserved tasks':'No unmatched or reserved tasks');
 const area=$('bulkIssues');area.replaceChildren();
 issues.slice(0,60).forEach(item=>{const line=document.createElement('div');line.className='bulk-issue';line.textContent=item.external_task_id+' · '+item.venue+' — '+item.reason;area.append(line)});
 if(issues.length>60){const line=document.createElement('p');line.textContent='Showing the first 60 issues of '+issues.length;area.append(line)}
 $('bulkResults').hidden=false;$('bulkCreate').disabled=!p.proposed_routes;
}
async function provider(){
 try{
  const state=await api('/api/routing/status');
  if(!state.ready){msg('bulkProviderStatus','Real road routing not connected yet. Preview is available, but route creation requires your private HeiGIT key.');return false}
  msg('bulkProviderStatus','Road routing configured: '+(state.provider==='heigit'?'HeiGIT':state.provider)+' · '+state.optimization+'. Mileage and payouts will be calculated when saved.');
  return true;
 }catch(e){msg('bulkProviderStatus','Routing unavailable: '+e.message);return false}
}
async function preview(){
 if(running)return;invalidate();
 if(!tasks.length){msg('bulkPreviewStatus','Load a CSV or the fictional demo first.');return}
 $('bulkPreview').disabled=true;msg('bulkPreviewStatus','Matching physical stops and contractors…');
 try{
  previewData=await api('/api/dispatch/bulk/preview','POST',payload());
  all=false;draw();await provider();
  msg('bulkPreviewStatus',previewData.proposed_routes+' route groups proposed. No routing credits used or routes saved.');
 }catch(e){msg('bulkPreviewStatus','Preview failed: '+e.message)}
 finally{$('bulkPreview').disabled=false}
}
function pricing(){
 const fields={base_fee:'bulkBase',per_stop:'bulkPerStop',per_mile:'bulkPerMile',per_hour:'bulkPerHour',minimum:'bulkMinimum'};
 const result={};
 for(const [name,id] of Object.entries(fields)){
  const raw=$(id).value.trim(),n=Number(raw);
  if(!raw||!Number.isFinite(n)||n<0||!/^\d+(\.\d{1,2})?$/.test(raw))throw Error('Pricing requires a nonnegative dollar amount with up to two decimals: '+name);
  result[name]=n;
 }
 return result;
}
async function create(){
 if(running||!previewData?.routes?.length)return;
 let policy;try{if(!window.dccPlanningDefaults)await window.loadPlanningDefaults(false);policy=pricing();policy.per_service_hour=window.dccPlanningDefaults?.pricing.per_service_hour||0;policy.hourly_basis=window.dccPlanningDefaults?.pricing.hourly_basis||'driving'}catch(e){msg('bulkProgress',e.message);return}
 if(!await provider()){msg('bulkProgress','Connect personal HeiGIT before creating routes. Nothing saved.');return}
 const cap=Number($('bulkCreateLimit').value);
 if(!Number.isInteger(cap)||cap<1||cap>500){msg('bulkProgress','Choose between 1 and 500 routes for this run');return}
 const drafts=previewData.routes.slice(0,cap);
 const notice='Calculate and create '+drafts.length+' of '+previewData.routes.length+' routes in this company? \n'
  +(isDemo?'FICTIONAL locations: no real client work. \n':'')
  +'Pricing: $'+policy.base_fee+' daily base + $'+policy.per_stop+' / stop + $'+policy.per_mile+' / road mile + $'+policy.per_hour+' / '+(policy.hourly_basis==='total_work'?'total work hour':'driving hour')+'; daily minimum $'+policy.minimum+'.\n'
  +'Task IDs will be reserved, but NO contractor offers or emails will be sent. Provider quota will be used. Keep this tab open.';
 if(!confirm(notice))return;
 running=true;stop=false;
 $('bulkCreate').disabled=true;$('bulkStop').hidden=false;$('bulkPreview').disabled=true;
 $('bulkDemo').disabled=true;$('bulkFile').disabled=true;
 let saved=0,failed=0;const errors=[];
 try{
  for(let i=0;i<drafts.length;i++){
   if(stop)break;
   const route=drafts[i];
   msg('bulkProgress','Calculating '+(i+1)+'/'+drafts.length+' · '+saved+' saved, '+failed+' errors · '+route.title);
   try{
    const output=await api('/api/dispatch/routes','POST',{
     title:route.title,contractor:route.contractor,home:route.home,tasks:route.tasks,pricing:policy,workday:window.dccPlanningDefaults?.workday
    });
    if(!output?.route?.id)throw Error('No saved route ID returned');
    saved++;
   }catch(e){
    failed++;errors.push(route.title+': '+e.message);
    if(/quota|rate.limit|too many requests/i.test(e.message)){stop=true;errors.push('Stopped to protect routing provider quota')}
   }
   // With <=30 stops per route, allow <34 requests/min against documented
   // HeiGIT Standard cap of 40 Directions requests/min. Other API traffic
   // can still consume quota, so errors stop the run if rate limited.
   if(!stop&&i<drafts.length-1)await new Promise(done=>setTimeout(done,1850));
  }
 }finally{
  running=false;$('bulkCreate').disabled=false;$('bulkStop').hidden=true;
  $('bulkPreview').disabled=false;$('bulkDemo').disabled=false;$('bulkFile').disabled=false;
  msg('bulkProgress',(stop?'Stopped. ':'Finished. ')+saved+' routes created; '+failed+' errors. '
   +(errors.length?errors.slice(0,7).join(' | '):'No errors.')+
   ' Refresh preview to exclude saved tasks before retrying. No contractor offers sent.');
  if(typeof window.refresh==='function')await window.refresh();
 }
}
function wire(){
 if(!$('bulkOpen'))return;
 $('bulkOpen').onclick=()=>window.dccUI?.setView('bulk');
 $('bulkBack').onclick=()=>window.dccUI?.setView('routes');
 $('bulkFile').onchange=onFile;$('bulkDemo').onclick=demo;
 $('bulkPreview').onclick=preview;$('bulkRepreview').onclick=preview;
 $('bulkShowAll').onclick=()=>{all=!all;draw()};
 $('bulkMaxStops').onchange=invalidate;$('bulkIncludeTraining').onchange=invalidate;
 $('bulkCreate').onclick=create;
 $('bulkStop').onclick=()=>{stop=true;msg('bulkProgress','Stopping after the current route finishes…')};
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',wire,{once:true});else wire();
})();
