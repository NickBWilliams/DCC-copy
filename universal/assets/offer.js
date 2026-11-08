/* DCC Universal: company-branded, mobile-first contractor offer flow.
   Reads only the one-time token URL; no staff session or outside libraries. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const demoMode=location.pathname==='/demo/route-invitation';
  const token=demoMode?'':location.pathname.split('/').pop();
  const acceptedMode=location.pathname.startsWith('/accepted/');
  const endpoint=(acceptedMode?'/api/accepted/':'/api/offers/')+encodeURIComponent(token);
  let details=null,submitting=false;
  const usd=value=>{
    const number=Number(value);
    return Number.isFinite(number)
      ?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(number)
      :'—';
  };
  function displayDate(value){
    if(!value)return 'Not specified';
    const date=new Date(value+'T12:00:00');
    if(!Number.isFinite(date.getTime()))return 'Not specified';
    return date.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});
  }
  function setContent(id,value){el(id).textContent=String(value??'')}
  async function api(path,method='GET',data){
    const response=await fetch(path,{
      method,headers:{'Content-Type':'application/json'},credentials:'omit',
      cache:'no-store',body:data?JSON.stringify(data):undefined,
    });
    const body=await response.json().catch(()=>({}));
    if(!response.ok){
      const detail=body.detail;
      throw Error(typeof detail==='string'?detail:'Unable to load route offer ('+response.status+')');
    }
    return body;
  }
  function hideAll(){
    ['actionPicker','acceptFlow','counterFlow','declineFlow','receipt'].forEach(id=>el(id).hidden=true);
    setContent('flowError','');
  }
  function choose(flow){
    hideAll();
    const section=el(flow);
    const submit=section.querySelector('button[id^="submit"]');
    if(submit)section.insertBefore(el('flowError'),submit);
    section.hidden=false;
    section.scrollIntoView({behavior:'smooth',block:'center'});
  }
  function resetFlow(){
    hideAll();el('actionPicker').hidden=false;
    el('actionPicker').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function showError(message){
    el('loading').hidden=true;el('offerView').hidden=true;
    el('offerError').hidden=false;setContent('offerError',message);
  }
  function addText(tag,className,value){
    const node=document.createElement(tag);
    node.className=className;node.textContent=String(value??'');
    return node;
  }
  function renderTasks(itinerary,declaredTasks){
    const counts=new Map();
    for(const stop of itinerary){
      for(const task of stop.tasks||[]){
        const type=task.task_type||'Service';
        counts.set(type,(counts.get(type)||0)+1);
      }
    }
    const area=el('taskBreakdown');area.replaceChildren();
    if(!counts.size){
      area.append(addText('p','form-hint',declaredTasks+' tasks recorded; detailed breakdown not provided.'));
      return;
    }
    for(const [type,count] of [...counts.entries()].sort()){
      const row=addText('div','type-row','');
      row.append(addText('span','',type),addText('strong','',count));
      area.append(row);
    }
  }
  function renderStops(itinerary){
    const area=el('stops');area.replaceChildren();
    for(const [index,stop] of itinerary.entries()){
      const li=addText('li','stop-card','');
      const number=addText('span','stop-num',stop.position||index+1);
      const body=addText('div','stop-content','');
      if(stop.day)body.append(addText('div','form-hint','Day '+stop.day+' · Stop '+stop.day_position));
      body.append(addText('div','stop-name',stop.venue||'Location '+(index+1)));
      body.append(addText('div','stop-address',stop.address||'Address unavailable'));
      for(const task of stop.tasks||[]){
        body.append(addText('span','stop-task',task.task_type||'Service'));
      }
      li.append(number,body);area.append(li);
    }
    if(!itinerary.length){
      area.append(addText('li','stop-card','Stop details are not available on this offer.'));
    }
  }
  function render(data){
    details=data;
    document.title=(data.work_order_label||data.title)+' · Route offer';
    setContent('companyName',data.company_name||'DCC Universal');
    setContent('routeTitle',data.work_order_label||data.title);
    setContent('payLarge',usd(data.price));
    const contractorName=el('contractorName');
    contractorName.hidden=!data.contractor;
    if(data.contractor)setContent('contractorName','Offered to '+data.contractor);
    setContent('offerMetaExpiry',(data.read_only?'Accepted ':'Expires ')+new Date(data.read_only?data.accepted_at:data.expires_at).toLocaleDateString(
      undefined,{month:'short',day:'numeric',year:'numeric'}));
    const demoBanner=el('demoBanner');
    demoBanner.hidden=!data.demo;
    if(data.demo){
      el('demoOne').classList.toggle('active',data.scenario==='single');
      el('demoMulti').classList.toggle('active',data.scenario==='multi');
      el('demoDays').classList.toggle('active',data.scenario==='days');
      document.body.classList.add('demo-mode');
    }
    if(data.work_order_label&&data.work_order_label!==data.title){
      setContent('routeSubtitle',data.title);
    }else setContent('routeSubtitle','Review the full route before responding.');
    setContent('pay',usd(data.price));
    setContent('stopCount',data.stops);
    setContent('dueSummary',data.due_date?displayDate(data.due_date):'Not set');
    const estimated=Number(data.total_hours??data.driving_hours);
    setContent('timeLabel',data.total_hours!=null?'Est. total work time':'Est. driving');
    setContent('roundTrip',data.driving_hours!=null&&Number.isFinite(estimated)
      ?estimated.toLocaleString(undefined,{maximumFractionDigits:1})+' hr'+(estimated===1?'':'s')
      :'—');
    const miles=Number(data.road_miles);
    el('roadMiles').hidden=!(data.road_miles!=null&&Number.isFinite(miles));
    if(!el('roadMiles').hidden){
      setContent('roadMiles',miles.toLocaleString(undefined,{maximumFractionDigits:1})+(data.demo?' illustrative round-trip miles':' estimated round-trip road miles'));
    }
    setContent('taskCount',data.tasks);
    setContent('stopSummary',data.stops+' '+(data.stops===1?'stop':'stops')+', '+data.tasks+' '+(data.tasks===1?'task':'tasks'));
    setContent('expires',data.expires_at?new Date(data.expires_at).toLocaleString():'');
    setContent('acknowledgmentText',data.acknowledgment_text);
    const itinerary=Array.isArray(data.itinerary)?data.itinerary:[];
    const days=Array.isArray(data.days)?data.days:[];
    el('dailySection').hidden=!days.length;
    const daily=el('dailyRoutes');daily.replaceChildren();
    setContent('dailyTitle',days.length+' planned '+(days.length===1?'day':'days'));
    for(const day of days){
      const card=addText('article','daily-route','');
      card.append(addText('h3','','Day '+day.day+' · '+day.stops+' stops'));
      card.append(addText('strong','',day.total_hours+' hours total'+(day.price!=null?' · '+usd(day.price):'')));
      card.append(addText('p','form-hint',day.road_miles+' round-trip miles · '+day.driving_hours+' hours driving · '+day.service_hours+' hours onsite · '+Math.round(day.break_hours*60)+' min break'+(day.waiting_hours?' · '+day.waiting_hours+' hours waiting':'')));
      if(day.above_target)card.append(addText('p','form-hint','Above the daily target; within the maximum.'));
      daily.append(card);
    }
    renderTasks(itinerary,data.tasks);
    renderStops(itinerary);
    el('loading').hidden=true;el('offerView').hidden=false;
    el('actionPicker').hidden=!!data.read_only;
    if(data.read_only){
      document.querySelector('.response-column').hidden=true;
      document.body.classList.add('read-only-route');
      el('acceptedSummary').hidden=false;
      setContent('acceptedSummaryText','Accepted by '+data.accepted_by+' · '+new Date(data.accepted_at).toLocaleString());
      el('acceptedPdf').href=data.pdf_path;
      setContent('routeSubtitle','Confirmed route details · view only');
      for(const id of ['actionPicker','acceptFlow','counterFlow','declineFlow'])el(id).replaceChildren();
    }
    window.dispatchEvent(new CustomEvent('dcc-offer-loaded',{detail:data}));
  }
  async function load(){
    try{
      const url=demoMode
        ?'/api/demo/route-invitation'+(location.search||'')
        :endpoint;
      const data=await api(url);
      render(data);
    }catch(err){
      showError(err.message||'This offer is unavailable. Please contact the person who sent it.');
    }
  }
  async function respond(action){
    if(submitting||!details||acceptedMode||details.read_only)return;
    let body={action,amount:null,notes:null};
    if(action==='accept'){
      const name=el('legalName').value.trim();
      if(name.length<2){setContent('flowError','Enter your full legal name.');return}
      if(!el('certified').checked){setContent('flowError','Please read and check the acknowledgment.');return}
      body={...body,legal_name:name,certified:true,
        acknowledgment_version:details.acknowledgment_version||1,
        notes:el('acceptNotes').value.trim()||null};
    }else if(action==='counter'){
      const entered=el('counterAmount').value.trim();
      const money=Number(entered);
      if(!entered||!Number.isFinite(money)||money<=0){
        setContent('flowError','Enter a valid positive counteroffer amount.');return}
      body={...body,amount:money,notes:el('counterNotes').value.trim()||null};
    }else{
      body.notes=el('declineNotes').value.trim()||null;
    }
    if(demoMode){
      hideAll();
      el('receipt').hidden=false;
      const heading={accept:'Demo: route accepted',counter:'Demo: counteroffer proposed',decline:'Demo: route declined'};
      const message={
        accept:'You completed the acceptance flow. In a real invitation, your signed confirmation would be sent securely to the company.',
        counter:'You completed a counteroffer. A real invitation would send your proposed total rate to dispatch.',
        decline:'You completed the decline flow. A real invitation would notify dispatch of your choice.'
      };
      setContent('receiptTitle',heading[action]);
      setContent('receiptMessage',message[action]);
      setContent('receiptRecorded','Nothing was sent. No route, contractor, or account was changed.');
      setContent('receiptFineprint','This is a fictional interactive demonstration. Select the 1-stop or 6-stop demo above to reset the response flow.');
      el('receipt').scrollIntoView({behavior:'smooth',block:'center'});
      return;
    }
    submitting=true;
    const buttons=[...document.querySelectorAll('button')];
    for(const button of buttons)button.disabled=true;
    setContent('flowError','');
    try{
      const result=await api(endpoint+'/respond','POST',body);
      hideAll();
      el('receipt').hidden=false;
      const heading={accept:'Route accepted',counter:'Counteroffer submitted',decline:'Route declined'};
      const desc={
        accept:'Your acceptance has been recorded. The route owner can now review the confirmed assignment.',
        counter:'Your proposed rate has been sent for the route owner to review.',
        decline:'Your response has been recorded. The route owner can now make another assignment.',
      };
      setContent('receiptTitle',heading[action]);
      setContent('receiptMessage',desc[action]);
      if(result.accepted_path){
        const link=document.createElement('a');link.href=result.accepted_path;
        link.textContent='View accepted route, map and PDF';link.className='action';el('receipt').append(link);
        setContent('receiptFineprint',result.confirmation_email_status==='pending'
          ?'Your confirmation email is queued. Your confirmed route is available through the link below.'
          :'Your acceptance is saved. Email delivery is not configured or needs dispatch attention; your route is available below.');
      }
      setContent('receiptRecorded','Recorded '+new Date(result.recorded_at||Date.now()).toLocaleString()
        +(result.accepted_by?' · Accepted by '+result.accepted_by:''));
      el('offerView').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){
      setContent('flowError',error.message);
      // A revoked or already used link must not silently allow another action.
      if(/no longer active|withdrawn|expired|terms have changed/i.test(error.message)){
        el('offerView').hidden=true;showError(error.message);
      }
    }finally{
      submitting=false;
      for(const button of buttons)button.disabled=false;
    }
  }
  function wire(){
    el('accept').onclick=()=>choose('acceptFlow');
    el('counter').onclick=()=>choose('counterFlow');
    el('decline').onclick=()=>choose('declineFlow');
    for(const item of document.querySelectorAll('[data-back]'))item.onclick=resetFlow;
    el('submitAccept').onclick=()=>respond('accept');
    el('submitCounter').onclick=()=>respond('counter');
    el('submitDecline').onclick=()=>respond('decline');
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',wire,{once:true});
  else wire();
})();
