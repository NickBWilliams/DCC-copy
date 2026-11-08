/* Company-scoped defaults. Explicit saves; route overrides stay local. */
(() => {
  let version=0;
  const ids={base_fee:'pBase',per_stop:'pPerStop',per_mile:'pPerMile',per_hour:'pPerHour',per_service_hour:'pServiceRate',minimum:'pMin'};
  const days={target_hours:'pTarget',max_hours:'pMaximum',break_minutes:'pBreak',default_service_minutes:'pService'};
  const e=id=>document.getElementById(id);
  async function load(apply=true){
    try{
      const data=await request('/api/planning-settings');version=data.version;
      if(apply){
      e('pHourlyBasis').value=data.pricing.hourly_basis;
      for(const [key,id] of Object.entries(ids))e(id).value=data.pricing[key];
      for(const [key,id] of Object.entries(days))e(id).value=data.workday[key];
      e('pStart').value=String(Math.floor(data.workday.start_minute/60)).padStart(2,'0')+':'+String(data.workday.start_minute%60).padStart(2,'0');
      const bulk={base_fee:'bulkBase',per_stop:'bulkPerStop',per_mile:'bulkPerMile',per_hour:'bulkPerHour',minimum:'bulkMinimum'};
      for(const [key,id] of Object.entries(bulk))if(e(id))e(id).value=data.pricing[key];
      }
      window.dccPlanningDefaults=data;
      e('planningSettingsMessage').textContent='Company defaults loaded. Edit any value for this route.';
    }catch(err){e('planningSettingsMessage').textContent=err.message}
  }
  async function save(){
    try{
      const pricing=Object.fromEntries(Object.entries(ids).map(([key,id])=>[key,Number(e(id).value)]));
      pricing.hourly_basis=e('pHourlyBasis').value;
      const workday=Object.fromEntries(Object.entries(days).map(([key,id])=>[key,Number(e(id).value)]));
      const parts=e('pStart').value.split(':').map(Number);workday.start_minute=parts[0]*60+parts[1];
      const data=await request('/api/planning-settings','PUT',{pricing,workday,version});version=data.version;
      window.dccPlanningDefaults=data;
      e('planningSettingsMessage').textContent='Company defaults saved. Existing routes and accepted pay are unchanged.';
    }catch(err){e('planningSettingsMessage').textContent=err.message}
  }
  e('loadPlanningDefaults').onclick=()=>load();e('savePlanningDefaults').onclick=save;
  // Load after the user enters the builder (authentication may finish later).
  

  window.loadPlanningDefaults=load;
})();
