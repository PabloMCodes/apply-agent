'use strict';
const root = document.querySelector('#content');
const titles = {overview:'Overview',profile:'Profile & resume',preferences:'Preferences',sources:'Job sources',telegram:'Telegram',jobs:'Discover jobs',applications:'Applications'};
let routeVersion = 0, pollTimer, toastTimer, pendingSubmit = null;
function el(tag, attrs={}, ...children) {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'checked') node.checked = !!value;
    else if (key === 'value') node.value = value ?? '';
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function toast(message, error=false) { const node=document.querySelector('#toast');node.textContent=message;node.className=error?'error':'';node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.hidden=true,6500); }
async function api(path, options={}) {
  const response=await fetch(path, options);
  if (!response.ok) {
    const body=await response.json().catch(()=>({detail:'Request failed.'}));
    const detail=Array.isArray(body.detail)?body.detail.map(v=>v.msg).join('; '):body.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return response.status===204?null:response.json();
}
const send=(path,method,body)=>api(path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
function button(label, action, cls='primary') {
  const node=el('button',{type:'button',class:cls},label);
  node.addEventListener('click',async()=>{node.disabled=true;try{await action();}catch(error){toast(error.message,true);}finally{node.disabled=false;}});
  return node;
}
function link(label,href,cls='secondary') { return el('a',{href,class:cls},label); }
function external(label,url,cls='quiet') {return el('a',{href:url,class:cls,target:'_blank',rel:'noopener noreferrer'},label);}
function field(label,name,value='',type='text',hint='') {
  const input=el(type==='textarea'?'textarea':'input',{name,id:name,type:type==='textarea'?null:type,value,autocomplete:'off'});
  if(type==='textarea')input.textContent=value;
  if(hint)input.setAttribute('aria-describedby',name+'-help');
  return el('div',{class:'field'},el('label',{for:name},label),input,hint?el('small',{id:name+'-help'},hint):null);
}
function selectField(label,name,value,options) {
  const select=el('select',{name,id:name},options.map(option=>el('option',{value:option.value},option.label)));
  select.value=value;
  return el('div',{class:'field'},el('label',{for:name},label),select);
}
function card(title,description,...content) {return el('section',{class:'card'},title?el('h2',{},title):null,description?el('p',{},description):null,...content);}
function head(title,description,eyebrow='MAKE IT YOURS') {return el('div',{class:'page-head'},el('span',{class:'eyebrow'},eyebrow),el('h1',{},title),el('p',{class:'subtitle'},description));}
function badge(text,variant='') {return el('span',{class:`badge ${variant}`},text.replaceAll('_',' '));}
function empty(title,description,action=null) {return el('div',{class:'empty'},el('span',{class:'empty-icon'},'↗'),el('h3',{},title),el('p',{},description),action);}
function notice(text,variant='') {return el('div',{class:`notice ${variant}`},text);}
const csv=value=>value.split(',').map(s=>s.trim()).filter(Boolean);
const date=value=>value?new Date(value*1000).toLocaleString():'Not checked yet';
function formSubmit(form, action) {
  form.addEventListener('submit',async event=>{event.preventDefault();const b=form.querySelector('[type=submit]');if(b)b.disabled=true;try{await action(new FormData(form));}catch(error){toast(error.message,true);}finally{if(b)b.disabled=false;}});
}
function saveButton(text='Save changes') {return el('button',{class:'primary',type:'submit'},text);}
async function render() {
  clearInterval(pollTimer);
  const version=++routeVersion;
  const [page='overview',id] = (location.hash.slice(1)||'overview').split('/');
  document.querySelector('#breadcrumb').textContent=titles[page]||'Overview';
  document.querySelectorAll('[data-page]').forEach(a=>a.setAttribute('aria-current',a.dataset.page===page?'page':'false'));
  root.replaceChildren(el('div',{class:'loading'},'Opening your workspace…'));
  try { const content=await ({overview,profile,preferences,sources,telegram,jobs,applications}[page]||overview)(id);if(version===routeVersion)root.replaceChildren(content); }
  catch(error){if(version===routeVersion)root.replaceChildren(head('Let’s try that again.',error.message),button('Reload',render));}
}
async function overview() {
  const [profileData,prefs,sourceData,telegramData,jobData,runs]=await Promise.all([
    api('/profile').catch(()=>null),api('/settings'),api('/settings/sources'),api('/settings/telegram'),api('/jobs?limit=1'),api('/applications?limit=200')]);
  const complete=!!(profileData?.first_name&&profileData?.last_name&&profileData?.email);
  const hero=el('section',{class:'hero'},el('div',{},el('span',{class:'eyebrow'},'LESS BUSYWORK. MORE POSSIBILITY.'),el('h1',{},'Your next chapter,',el('br'),'with a little help.'),el('p',{},'Set your direction. Let your agent find the opportunities and help with the paperwork. You make the final call.'),link(complete?'Explore your jobs  ↗':'Set up your profile  ↗',complete?'#jobs':'#profile','primary')),el('div',{class:'hero-art','aria-hidden':'true'},el('div',{class:'orbit'},el('span',{},'↗'))));
  const stats=el('div',{class:'stats'},[[jobData.total,'Jobs in your workspace'],[runs.filter(r=>r.status==='ready').length,'Ready for your review'],[sourceData.filter(s=>s.enabled).length,'Sources enabled']].map(([n,t])=>el('div',{class:'stat'},el('span',{class:'number'},n),el('span',{class:'caption'},t))));
  const steps=[['profile','Your story','Add your resume and application details.',complete],['preferences','Your direction','Choose roles and locations, across any industry.',prefs.roles.length>0||prefs.locations.length>0],['sources','Your starting points','Add the company boards you want to follow.',sourceData.length>0],['telegram','Your connection','Get alerts and approve jobs from your phone.',telegramData.connected]];
  const setup=card('A workspace that knows you','A few details now. Less repetition later.',steps.map(([href,title,desc,done],i)=>el('a',{href:'#'+href,class:'setup-step'},el('span',{class:'step-icon'+(done?' done':'')},done?'✓':i+1),el('div',{},el('strong',{},title),el('p',{},desc)),el('span',{class:'arrow'},'↗'))));
  const activity=card('You’re in control','A clear path from discovery to decision.',el('ol',{class:'list-plain'},el('li',{},'New listings arrive from the sources you choose.'),el('li',{},'You approve which applications to prepare.'),el('li',{},'Review the filled form, edit answers, then decide whether to submit.')),notice(prefs.monitoring_enabled?'Monitoring is on. Your enabled sources are checked on your schedule.':'Monitoring is paused. Turn it on in Preferences when you’re ready.'),link('Adjust preferences →','#preferences','quiet'),notice('Your agent detects embedded Greenhouse forms and can try standard application forms on other sites. Complex flows may still need manual completion.'));
  return el('div',{},hero,stats,el('div',{class:'grid'},setup,activity));
}
async function profile() {
  const [data,resumes,ai,applicationFields,savedSessions]=await Promise.all([api('/profile').catch(()=>({})),api('/resumes'),api('/settings/ai'),api('/profile/fields'),api('/browser-sessions')]);
  const form=el('form');
  const fields=el('div',{class:'form-grid'},field('First name','first_name',data.first_name),field('Last name','last_name',data.last_name),field('Email address','email',data.email,'email'),field('Phone number','phone',data.phone,'tel'),field('City / location','location',data.location),field('LinkedIn URL','linkedin',data.linkedin),field('GitHub URL','github',data.github),field('Website or portfolio','website',data.website),field('Skills, separated by commas','skills',(data.skills||[]).join(', ')));
  const answers=el('div');
  let lastSection='';
  for(const item of applicationFields){
    if(item.section!==lastSection){answers.append(el('h3',{},item.section));lastSection=item.section;}
    const value=data.application_answers?.[item.key]||'';
    const control=item.options?selectField(item.label,'application-'+item.key,value,[{value:'',label:'Not answered'},...item.options.map(o=>({value:o,label:o}))]):field(item.label,'application-'+item.key,value);
    answers.append(control);
    if(item.hint)answers.append(el('small',{class:'hint'},item.hint));
  }
  const extraction=el('div');
  form.append(fields,extraction,answers,notice('Self-identification is optional. Blank means unanswered, not “No” or “Prefer not to disclose”. Resume extraction never fills demographic or eligibility answers.'),saveButton('Save profile'));
  formSubmit(form,async()=>{
    const payload={...data};
    for(const key of ['first_name','last_name','email','phone','location','linkedin','github','website'])payload[key]=form.querySelector(`[name=${key}]`).value;
    payload.skills=csv(form.querySelector('[name=skills]').value);payload.name=`${payload.first_name} ${payload.last_name}`.trim();
    payload.application_answers=Object.fromEntries(applicationFields.map(item=>[item.key,form.querySelector(`[name=application-${item.key}]`).value]));
    await send('/profile','PUT',payload);Object.assign(data,payload);toast('Profile saved.');
  });
  function fillFromResume(suggestions){
    let filled=0;
    for(const [key,value] of Object.entries(suggestions.values)){
      if(key==='application_answers')continue;
      const input=form.querySelector(`[name=${key}]`);
      if(input&&!input.value.trim()){input.value=Array.isArray(value)?value.join(', '):value;filled++;}
    }
    for(const [key,value] of Object.entries(suggestions.values.application_answers||{})){
      const input=form.querySelector(`[name=application-${key}]`);
      if(input&&!input.value.trim()){input.value=value;filled++;}
    }
    extraction.replaceChildren(notice(`Filled ${filled} empty profile fields from your resume. Check them, then Save profile.`),el('details',{},el('summary',{},'See extracted fields and source text'),Object.entries(suggestions.evidence).map(([key,value])=>el('p',{},el('strong',{},key.replaceAll('_',' ')+': '),value))));
    toast(`Filled ${filled} profile fields. Review and save your profile.`);
  }
  const resumeList=el('div');
  function drawResumes(){resumeList.replaceChildren();for(const item of resumes){
    const title=field('Resume title','title-'+item.id,item.title),roles=field('Target roles / keywords','roles-'+item.id,item.roles);
    resumeList.append(el('div',{class:'review-field'},title,roles,el('p',{},item.original_name),external('Download resume',`/resumes/${item.id}/file`),button('Fill profile from this resume',async()=>{fillFromResume(await api(`/resumes/${item.id}/profile-suggestions`));},'secondary'),button('Save resume title',async()=>{await send(`/resumes/${item.id}`,'PATCH',{title:title.querySelector('input').value,roles:roles.querySelector('input').value});toast('Resume details saved.');},'secondary'),button('Remove resume',async()=>{await send(`/resumes/${item.id}`,'DELETE');resumes.splice(resumes.indexOf(item),1);drawResumes();},'quiet')));
  }}
  drawResumes();
  const input=el('input',{type:'file',accept:'.pdf,.txt',id:'resume-upload','aria-label':'Choose resume file'});
  const title=field('New resume title','resume-title','','text','Required. For example: Product designer or Data analyst.'),roles=field('New resume target roles','resume-roles');
  const info=el('div');
  const upload=button('Upload resume',async()=>{
    if(!input.files.length||!title.querySelector('input').value.trim())throw new Error('Choose a file and give it a descriptive title.');
    const body=new FormData();body.append('file',input.files[0]);body.append('title',title.querySelector('input').value);body.append('roles',roles.querySelector('input').value);
    const uploaded=await api('/resumes',{method:'POST',body});resumes.push(uploaded);drawResumes();fillFromResume(uploaded.profile_suggestions);
    info.replaceChildren(el('p',{},`Uploaded: ${uploaded.original_name}`));
  },'secondary');
  const aiForm=el('form',{},el('label',{class:'check-field'},el('input',{type:'checkbox',name:'enabled',checked:ai.enabled}),'Enable optional AI suggestions'),el('label',{class:'check-field'},el('input',{type:'checkbox',name:'browser_assistance',checked:ai.browser_assistance}),'Use AI field mapping during preparation (sends field labels and contact/professional facts)'),field('API base URL','base_url',ai.base_url||'','url','A Chat Completions compatible endpoint, including /v1 if needed. HTTP is allowed for localhost models.'),field('Model name','model',ai.model||''),field('API key','api_key','','password',ai.has_key?'A key is saved. Leave blank to keep it.':'Optional for local models.'),saveButton('Save AI connection'));
  formSubmit(aiForm,async f=>{await send('/settings/ai','PUT',{enabled:f.has('enabled'),browser_assistance:f.has('browser_assistance'),base_url:f.get('base_url'),model:f.get('model'),api_key:f.get('api_key')||null});aiForm.querySelector('[name=api_key]').value='';toast('AI connection saved.');});
  const sessionList=el('div',{},el('h3',{},'Remembered site logins'),savedSessions.map(item=>el('div',{class:'review-field'},el('p',{},item.origin),el('small',{},'Expires '+date(item.expires_at)),button('Forget site login',async()=>{await send('/browser-sessions/forget','POST',{origin:item.origin});toast('Saved login removed. Existing open sessions remain active.');render();},'quiet'))));
  return el('div',{},head('Your application profile.','Upload a resume to fill your details, then answer the common SWE application questions.'),el('div',{class:'grid'},card('Application details','Confirm your details before using autofill.',form),el('div',{},card('Your resumes','Titles and role keywords guide automatic selection. Ambiguous matches pause for your choice.',resumeList,title,roles,input,upload,info),card('Optional AI','Suggest answer sends saved skills, education and employment answers, and any previously saved professional notes to this provider. Voluntary self-identification and work eligibility answers are excluded. Drafts need your approval.',aiForm,sessionList,notice('This connection uses a provider API or local model. It does not sign into ChatGPT Plus.')))));
}
async function preferences() {
  const data=await api('/settings');
  const form=el('form',{},field('Live review slots','review_slots',data.review_slots||5,'number','Keep 1–20 browser sessions open. More slots use more memory.'),field('Roles or title keywords','roles',data.roles.join(', '),'text','For example: marketing, designer, nurse. Leave blank to include all roles.'),field('Location keywords','locations',data.locations.join(', '),'text','For example: Remote, New York, London. Leave blank for all locations.'),field('Exclude these keywords','exclude_keywords',data.exclude_keywords.join(', '),'text','For example: senior, director. Matches company, title, and location.'),field('Check sources every (minutes)','poll_minutes',data.poll_minutes,'number'),el('label',{class:'check-field'},el('input',{type:'checkbox',name:'monitoring_enabled',checked:data.monitoring_enabled}),'Monitor my enabled sources automatically'),field('Private review URL (optional)','review_base_url',data.review_base_url,'url','Your installation’s private address, reachable from your phone. Used in Telegram review links.'),saveButton('Save preferences'));
  form.querySelector('[name=poll_minutes]').min=1;form.querySelector('[name=poll_minutes]').max=1440;
  formSubmit(form,async f=>{await send('/settings','PUT',{review_slots:Number(f.get('review_slots')),roles:csv(f.get('roles')),locations:csv(f.get('locations')),exclude_keywords:csv(f.get('exclude_keywords')),poll_minutes:Number(f.get('poll_minutes')),monitoring_enabled:f.has('monitoring_enabled'),review_base_url:f.get('review_base_url')});toast('Preferences saved.');});
  return el('div',{},head('Point your agent in the right direction.','Your search is about your goals, not a particular industry.'),el('div',{class:'grid'},card('Your search preferences','These filters decide which newly discovered jobs trigger alerts.',form),card('Simple, transparent filtering','All discovered jobs are kept in your workspace.',notice('A listing must match at least one role keyword AND one location keyword, when those lists are filled. Excluded keywords always win.'),el('p',{},'These are keyword filters, not AI fit scores. Changing them affects future discoveries; it does not resend old alerts.'),el('h3',{},'Review from your phone'),el('p',{},'Run your installation on an always-on server or an awake computer. Use a private network connection to open the same workspace from your phone.'),notice('Keep this single-user app private. There are no accounts or public-access login screens.'))));
}
async function sources() {
  const [items,builtins]=await Promise.all([api('/settings/sources'),api('/sources')]);
  const form=el('form',{},field('Company or source name','name','','text','For example: Acme careers'),field('Job board or application URL','url','','url','Greenhouse boards and CareerPuck job links can be imported. Other sites are saved as bookmarks; you can add individual jobs in Discover jobs.'),saveButton('Add source'));
  form.querySelectorAll('input').forEach(i=>i.required=true);
  formSubmit(form,async f=>{const added=await send('/settings/sources','POST',Object.fromEntries(f));toast(added.kind==='bookmark'?'Saved as a bookmark. This website is not supported for monitoring yet.':'Source added. Check it now to import the initial listings.');render();});
  const list=el('div');
  for(const item of items){
    const supported=item.kind!=='bookmark';
    list.append(el('div',{class:'source-item'},el('div',{class:'row'},el('h3',{},item.name),badge(supported?(item.enabled?'Monitoring enabled':'Paused'):'Bookmark',supported?'':'warning')),external(item.url,item.url,'source-url'),el('p',{class:'item-meta'},supported?`Last check: ${date(item.last_checked)}`:'Saved for reference. Automatic ingestion and autofill are not enabled.'),item.last_error?notice(item.last_error,'error'):null,el('div',{class:'item-actions'},supported?button('Check now',async()=>{const result=await send(`/settings/sources/${item.id}/check`,'POST');toast(`Imported ${result.found} jobs. ${result.matching_preferences} match your preferences.`);render();},'secondary'):null,supported?button(item.enabled?'Pause':'Enable',async()=>{await send(`/settings/sources/${item.id}`,'PATCH',{enabled:!item.enabled});render();},'quiet'):null,button('Remove',async()=>{await send(`/settings/sources/${item.id}`,'DELETE');render();},'quiet'))));
  }
  if(!items.length)list.append(empty('Start with a company you like.','Add its Greenhouse board to discover opportunities in any department.'));
  const builtinSelect=selectField('Optional software internships / graduate lists','builtin',builtins[0]?.url||'',builtins.map(b=>({value:b.url,label:b.id.replaceAll('_',' ')})));
  const addBuiltin=button('Add GitHub list',async()=>{const url=builtinSelect.querySelector('select').value;await send('/settings/sources','POST',{name:'SpeedyApply '+builtins.find(b=>b.url===url).id,url});toast('GitHub source added.');render();},'secondary');
  return el('div',{},head('Start where you want to work.','Follow company boards, add individual jobs, or keep your favorite sites close.'),el('div',{class:'grid'},card('Add a job source','Import Greenhouse boards, CareerPuck jobs backed by Greenhouse, or SpeedyApply lists.',form),card('Your sources',`${items.length} saved source${items.length===1?'':'s'}`,list)),card('Existing GitHub sources','These lists focus on software jobs. Greenhouse sources can cover any occupation.',builtinSelect,addBuiltin));
}
async function telegram() {
  const data=await api('/settings/telegram');
  const status=el('div',{class:'connection'},el('span',{class:'telegram-icon'},'↗'),el('div',{},el('h3',{},data.connected?'You’re connected.':data.configured?'One more step.':'Your agent, in your pocket.'),el('p',{},data.connected?`@${data.username} · private chat linked`:data.configured?'Open your pairing link in Telegram to finish.':'Get an alert. Make a decision. Get back to your day.')));
  const pairing=el('div');
  const form=el('form',{},field('Bot token','token','','password','Create your own bot with @BotFather, then paste its token here. It is stored only in this installation.'),saveButton(data.configured?'Reconnect bot':'Connect Telegram'));
  form.querySelector('input').required=true;
  formSubmit(form,async f=>{const result=await send('/settings/telegram/connect','POST',{token:f.get('token')});form.reset();pairing.replaceChildren(notice('Pairing link ready. It expires in 10 minutes.'),external('Open Telegram to finish connecting ↗',result.url,'primary'));toast('Bot verified. Tap the link and press Start in Telegram.');});
  const errorNode=el('div',{},data.last_error?notice(data.last_error,'error'):null);
  pollTimer=setInterval(async()=>{try{const next=await api('/settings/telegram');if(next.connected){status.replaceChildren(el('span',{class:'telegram-icon'},'✓'),el('div',{},el('h3',{},'You’re connected.'),el('p',{},`@${next.username} · private chat linked`)));pairing.replaceChildren();}errorNode.replaceChildren(next.last_error?notice(next.last_error,'error'):el('span'));}catch{}},3000);
  return el('div',{},head('A little nudge. When it matters.','Connect your own Telegram bot for new jobs, approvals, and application updates.'),el('div',{class:'grid'},card('Telegram connection','Only your linked private chat can approve applications.',status,errorNode,pairing,form,data.configured?button('Disconnect',async()=>{await send('/settings/telegram','DELETE');toast('Telegram disconnected.');render();},'quiet'):null),card('From opportunity to action','Your computer or server does the work. Your phone keeps you in control.',el('ol',{class:'list-plain'},el('li',{},'Create a bot through Telegram’s official @BotFather.'),el('li',{},'Connect it here, then open the pairing link and press Start.'),el('li',{},'Approve a new job with Queue application, or choose Skip.'),el('li',{},'Open your workspace to review supported prepared applications.')),external('Open @BotFather ↗','https://t.me/BotFather','secondary'),notice('The first import establishes a baseline without a flood of alerts. New listings after that can trigger messages.'))));
}
async function jobs(embedded=false) {
  let offset=0;
  const selected=new Set();
  const resumeOptions=await api('/resumes');
  const resumeChoice=selectField('Resume for selection','batch-resume','', [{value:'',label:'Automatic title / role match'},...resumeOptions.map(r=>({value:String(r.id),label:r.title}))]);
  const bulk=button('Prepare selected (0)',async()=>{
    const ids=[...selected], failures=[];
    for(let i=0;i<ids.length;i+=500){
      const result=await send('/applications/prepare-batch','POST',{job_ids:ids.slice(i,i+500),resume_id:Number(resumeChoice.querySelector('select').value)||null});
      failures.push(...result.items.filter(item=>item.error));
    }
    if(failures.length)toast(failures.map(i=>`Job ${i.job_id}: ${i.error}`).join('; '),true);
    else {toast('Applications queued. They will be prepared in order.');if(location.hash==='#applications')render();else location.hash='applications';}
  });
  bulk.disabled=true;

  const listing=el('div'),pagination=el('div',{class:'pagination'});
  const query=el('input',{type:'search',placeholder:'Search title, company, or location…','aria-label':'Search jobs'});
  const status=el('select',{'aria-label':'Filter by status'},['','new','saved','skipped','ready_for_review','applied'].map(s=>el('option',{value:s},s?s.replaceAll('_',' '):'All statuses')));
  const search=button('Search',async()=>{offset=0;await load();},'secondary');
  async function load(){
    const result=await api(`/jobs?limit=25&offset=${offset}&q=${encodeURIComponent(query.value)}${status.value?'&status='+status.value:''}`);
    listing.replaceChildren();
    for(const item of result.items){
      const supported=item.application_url.startsWith('https://');
      const pick=el('input',{type:'checkbox',checked:selected.has(item.id),'aria-label':`Select ${item.title} at ${item.company}`});
      pick.disabled=!supported||['applied','skipped'].includes(item.status);
      pick.addEventListener('change',()=>{if(pick.checked)selected.add(item.id);else selected.delete(item.id);bulk.textContent=`Prepare selected (${selected.size})`;bulk.disabled=!selected.size;});
      listing.append(el('article',{class:'job-item'},el('label',{class:'check-field'},pick,'Select for preparation'),el('div',{class:'row'},el('div',{},el('h3',{},item.title),el('div',{class:'item-meta'},`${item.company} · ${item.location}`)),badge(item.status)),el('div',{class:'item-actions'},supported?button('Prepare application',async()=>{const run=await send(`/jobs/${item.id}/prepare`,'POST');location.hash=`applications/${run.id}`;},'secondary'):badge('Use an HTTPS application link','warning'),external('Open original ↗',item.application_url),button(item.status==='skipped'?'Unskip':'Skip',async()=>{await send(`/jobs/${item.id}/tracking`,'PUT',{status:item.status==='skipped'?'new':'skipped',notes:item.notes});load();},'quiet'))));
    }
    if(!result.items.length)listing.append(empty('Your next opportunity starts here.','Add a source and check it to discover jobs, or save a job manually.',link('Add a source','#sources')));
    const previous=button('← Previous',async()=>{offset=Math.max(0,offset-25);load();},'secondary');previous.disabled=offset===0;
    const next=button('Next →',async()=>{offset+=25;load();},'secondary');next.disabled=offset+25>=result.total;
    pagination.replaceChildren(previous,el('span',{},`${result.total} jobs · ${result.total?offset+1:0}–${Math.min(offset+25,result.total)}`),next);
  }
  query.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();search.click();}});
  const manual=el('form',{},el('div',{class:'form-grid'},field('Company','company'),field('Job title','title'),field('Location','location'),field('Application URL','application_url','','url')),el('div',{class:'actions'},saveButton('Save job')));
  manual.querySelectorAll('input').forEach(i=>i.required=true);
  formSubmit(manual,async f=>{await send('/jobs','POST',Object.fromEntries(f));manual.reset();toast('Job saved.');load();});
  const selectAll=button('Select all matching jobs',async()=>{
    let cursor=0;
    while(true){const result=await api(`/jobs?limit=200&offset=${cursor}&q=${encodeURIComponent(query.value)}${status.value?'&status='+status.value:''}`);
      for(const item of result.items)if(item.application_url.startsWith('https://')&&!['applied','skipped'].includes(item.status))selected.add(item.id);
      cursor+=result.items.length;if(cursor>=result.total||!result.items.length)break;
    }
    bulk.textContent=`Prepare selected (${selected.size})`;bulk.disabled=!selected.size;await load();
  },'secondary');
  const importField=field('Paste jobs (one per line)','bulk-jobs','','textarea','Company | Title | Location | HTTPS application URL');
  const importButton=button('Import jobs',async()=>{
    const rows=importField.querySelector('textarea').value.split('\n').filter(s=>s.trim()).map(line=>{const parts=line.split('|').map(s=>s.trim());if(parts.length!==4)throw new Error('Each line needs Company | Title | Location | URL.');return {company:parts[0],title:parts[1],location:parts[2],application_url:parts[3]};});
    if(!rows.length)throw new Error('Paste at least one job.');
    for(let i=0;i<rows.length;i+=500)await send('/jobs/bulk','POST',{jobs:rows.slice(i,i+500)});
    toast(`Saved ${rows.length} jobs.`);await load();
  },'secondary');
  await load();
  return el('div',{},embedded?null:head('Room for your next opportunity.','Every occupation is welcome. Browse your imported jobs or save a link yourself.','YOUR WORKSPACE'),el('div',{class:'filter-row'},query,status,search,selectAll,button('Clear selection',async()=>{selected.clear();bulk.textContent='Prepare selected (0)';bulk.disabled=true;await load();},'quiet')),resumeChoice,bulk,card('Your jobs','',listing,pagination),card('Found something elsewhere?','Save any job here. Prepare application will detect its form and try supported fields, without submitting.',manual),card('Import many jobs','Paste hundreds of listings. Nothing is prepared until you select it.',importField,importButton));
}
async function applications(id) {
  if(id)return review(Number(id));
  let offset=0;
  const items=await api('/applications?limit=50');
  // An older running backend may still own an active review during an upgrade.
  const memory=await api('/saved-answers').catch(()=>[]);
  const library=el('div');
  for(const answer of memory)library.append(el('div',{class:'review-field'},el('strong',{},answer.question),el('p',{},String(answer.value)),el('small',{},answer.company?`Only ${answer.company}`:'All companies'),button('Forget answer',async()=>{await send(`/saved-answers/${answer.id}`,'DELETE');render();},'quiet')));
  if(!memory.length)library.append(el('p',{},'Choose Remember this answer while reviewing an application to save it here.'));

  const list=el('div'),pagination=el('div',{class:'pagination'});
  function paint(items){
    list.replaceChildren();
    for(const item of items)list.append(el('article',{class:'application-item'},el('div',{class:'row'},el('div',{},el('h3',{},item.title||`Application #${item.id}`),el('div',{class:'item-meta'},item.company||item.application_url)),badge(item.status)),link(item.status==='ready'?'Review application →':'View application →',`#applications/${item.id}`,'secondary')));
    if(!items.length)list.append(empty('You make the first move.','Select jobs below to fill your queue.'));
    const prev=button('Previous runs',async()=>{offset=Math.max(0,offset-50);await refresh();},'secondary');prev.disabled=offset===0;
    const next=button('More runs',async()=>{offset+=50;await refresh();},'secondary');next.disabled=items.length<50;
    pagination.replaceChildren(prev,el('span',{},`Runs ${offset+1}–${offset+items.length}`),next);
  }
  async function refresh(){paint(await api(`/applications?limit=50&offset=${offset}`));}
  paint(items);
  const chooser=el('details',{},el('summary',{},'Add and select applications'),await jobs(true));
  pollTimer=setInterval(()=>refresh().catch(()=>{}),4000);
  return el('div',{},head('A clear view of what’s next.','Queue any number of jobs. Review completed forms while the worker prepares the next ones.','YOUR WORKSPACE'),card('Your applications','Live review slots are configurable in Preferences. Status updates automatically.',list,pagination),chooser,card('Saved answers','Reused answers require confirmation before submission.',library));
}
async function review(id) {
  let record=await api(`/applications/${id}`);
  const container=el('div');
  let browserOpen=false;
  const liveContainer=el('div');
  function paint(){
    const snapshot=record.snapshot||{},ready=record.status==='ready';
    const banner=el('div',{class:'review-banner'},el('div',{class:'row'},el('h3',{},record.message||'Waiting for the browser worker…'),badge(record.status)),snapshot.url?external('Application form ↗',snapshot.url,'quiet'):null,snapshot.expires_at&&ready?el('p',{},`Live review expires at ${new Date(snapshot.expires_at*1000).toLocaleTimeString()}.`):null);
    const fields=el('div');
    const reviewLabels={previously_confirmed:'Previously confirmed',new_wording:'New wording—check mapping',ai_draft:'AI draft—review required',needs_answer:'Needs your answer'};
    for(const f of snapshot.fields||[]){
      let control;
      if(['checkbox','radio'].includes(f.type))control=el('label',{class:'check-field'},el('input',{type:'checkbox',checked:f.value}),f.label);
      else if(f.type==='select'||(f.type==='combobox'&&f.options.length))control=selectField(f.label+(f.required?' *':''),'review-'+f.id,String(f.value),f.options);
      else control=field(f.label+(f.required?' *':''),'review-'+f.id,String(f.value??''),f.type==='textarea'?'textarea':'text');
      const input=control.querySelector('input,select,textarea');input.disabled=!ready||!f.supported;
      const optionSearch = f.type==='combobox' ? field('Search employer choices','search-'+f.id,'','text','For locations, enter a city. For Yes/No questions, leave blank.') : null;
      const remember=el('input',{type:'checkbox'});
      const scope=el('select',{'aria-label':'Reuse answer scope'},el('option',{value:'company'},'This company only'),el('option',{value:'all'},'All companies'));
      const memoryControl=ready&&f.supported&&!['checkbox','radio','file','custom','password'].includes(f.type)?el('div',{},el('label',{class:'check-field'},remember,'Remember this answer'),scope):null;
      const provenance=f.review||{status:'needs_answer',source:'No matching confirmed information'};
      const draft=provenance.draft?el('div',{},notice('Suggested answer: '+provenance.draft),provenance.evidence?el('pre',{},JSON.stringify(provenance.evidence,null,2)):null,button('Copy draft to answer',()=>{if(f.type==='select'){const choice=f.options.find(o=>o.label===provenance.draft);if(choice)input.value=choice.value;}else input.value=provenance.draft;},'secondary')):null;
      fields.append(el('div',{class:'review-field'},badge(reviewLabels[provenance.status]||'Needs your answer',provenance.pending?'warning':''),el('p',{class:'hint'},provenance.source),draft,control,f.saved_answer_id?notice('Remembered answer — check it, then click Confirm answer.'):null,memoryControl,ready&&f.supported?button('Suggest answer with AI',async()=>{await send(`/applications/${id}/suggest`,'POST',{revision:record.revision,field_id:f.id});toast('Requesting a draft from your configured model…');},'quiet'):null,ready&&f.type==='combobox'?optionSearch:null,ready&&f.type==='combobox'?button('Find choices',async()=>{await send(`/applications/${id}/options`,'POST',{revision:record.revision,field_id:f.id,query:optionSearch.querySelector('input').value});toast('Reading available choices…');},'secondary'):null,ready&&f.supported?button((f.saved_answer_id||f.review?.pending)?'Confirm answer':'Update answer',async()=>{const value=['checkbox','radio'].includes(f.type)?input.checked:input.value;await send(`/applications/${id}/edit`,'POST',{revision:record.revision,field_id:f.id,value,remember:remember.checked,scope:scope.value});toast('Updating the live form…');},'secondary'):f.type==='file'?el('small',{class:'hint'},'This is the file uploaded to the employer form.'):!f.supported?el('small',{class:'hint'},'This custom control must be completed on the employer site.'):null));
    }
    const actions=el('div',{class:'actions'});
    if(ready||record.status==='takeover')actions.append(button('Open live browser',async()=>{browserOpen=true;liveContainer.replaceChildren(await browserPanel(id,()=>{browserOpen=false;render();}));},'secondary'));
    if(record.status==='takeover')actions.append(button('Close without submitting',async()=>{await send(`/applications/${id}/cancel`,'POST');browserOpen=false;render();},'quiet'));
    if(ready){
      if(snapshot.can_back)actions.append(button('Previous page',async()=>{await send(`/applications/${id}/navigate`,'POST',{revision:record.revision,direction:'back'});},'secondary'));
      if(snapshot.can_next)actions.append(button('Next page',async()=>{await send(`/applications/${id}/navigate`,'POST',{revision:record.revision,direction:'next'});},'secondary'));
      const submit=button('Review complete · Submit',async()=>{pendingSubmit={id,revision:record.revision};document.querySelector('#confirm-detail').textContent=`${record.company||''} — ${record.title||record.application_url}`;document.querySelector('#confirm-dialog').showModal();});
      submit.disabled=!snapshot.can_submit;actions.append(submit,button('Close without submitting',async()=>{await send(`/applications/${id}/cancel`,'POST');browserOpen=false;liveContainer.replaceChildren();toast('Closing the browser session…');},'secondary'));
    }else if(['error','expired','needs_attention','cancelled'].includes(record.status))actions.append(button('Prepare again',async()=>{await send(`/applications/${id}/retry`,'POST');toast('Preparing a fresh application…');},'secondary'));
    actions.append(external('Open employer site ↗',record.application_url,'quiet'));
    const reviewBody=(snapshot.fields?.length||ready)?el('div',{class:'review-grid'},card('Your answers','Check every answer, including optional questions and consents.',fields),card('The actual application','Screenshot of the filled browser. Opening the employer URL on another device starts a separate session.',el('div',{class:'screenshot-wrap'},el('img',{class:'screenshot',src:`/applications/${id}/screenshot?v=${record.revision}`,alt:'Screenshot of the current job application form'})))):card('Application preparation','',empty(record.status==='queued'?'In your queue':'Waiting for your next step',record.message||'The browser worker will open a supported application shortly.'));
    const blockers=snapshot.blockers?.length?notice(el('div',{},el('strong',{},'Before submission:'),el('ul',{class:'list-plain'},snapshot.blockers.map(b=>el('li',{},b))))):null;
    const history=el('div');
    for(const page of snapshot.pages||[])history.append(el('details',{},el('summary',{},`Page ${page.page_number} — recorded answers`),page.fields.map(f=>el('div',{class:'review-field'},el('strong',{},f.label),el('p',{},String(f.value)),el('small',{},f.review?.source||'')))));
    container.replaceChildren(...[link('← All applications','#applications','quiet'),head(record.title||`Application #${id}`,record.company||'Review the application before deciding.','YOUR REVIEW'),banner,snapshot.ai_message?notice(snapshot.ai_message):null,snapshot.resume?external('Download the resume used in this application',`/applications/${id}/resume/file`):null,notice(`Page ${snapshot.page_number||1}${snapshot.resume?' · Resume: '+snapshot.resume.title+' · '+snapshot.resume.reason:''}`),blockers,reviewBody,card('Other pages','Recorded values from visited pages. Use Previous page to edit employer fields.',history),actions,liveContainer].filter(node=>node!=null));
  }
  paint();
  pollTimer=setInterval(async()=>{try{const next=await api(`/applications/${id}`);if(next.revision!==record.revision||next.status!==record.status){record=next;if(!['ready','takeover'].includes(record.status)){browserOpen=false;liveContainer.replaceChildren();}if(!browserOpen)paint();}}catch{}},2000);
  return container;
}
document.querySelector('#dismiss-submit').addEventListener('click',()=>{pendingSubmit=null;document.querySelector('#confirm-dialog').close();});
document.querySelector('#confirm-submit').addEventListener('click',async()=>{
  const action=pendingSubmit;if(!action)return;pendingSubmit=null;
  document.querySelector('#confirm-dialog').close();
  try{await send(`/applications/${action.id}/submit`,'POST',{revision:action.revision});toast('Your explicit submission request was sent. Waiting for the employer response.');}catch(error){toast(error.message,true);}
});
window.addEventListener('hashchange',render);
render();
