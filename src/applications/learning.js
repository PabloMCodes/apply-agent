(() => {
  if (window.__applyAgentLearner) return;
  window.__applyAgentLearner = true;
  window.__applyAgentRecording = false;
  window.__applyAgentLearningReady().then(enabled => { window.__applyAgentRecording = enabled; }).catch(() => {});
  const excluded = /password|passcode|one.?time|verification.?code|security.?code|auth.?token|\botp\b|\bpin\b|signature|sign your|social security|\bssn\b|credit card|bank account|routing number/i;
  const label = el => [...(el.labels || [])].map(l => {
    const copy=l.cloneNode(true);copy.querySelectorAll('input,textarea,select,button').forEach(n=>n.remove());return copy.textContent;
  }).join(' ').trim() || (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ').trim() || el.getAttribute('aria-label') || '';
  const pending = new Map(), dirty = new Set();
  window.__applyAgentSetRecording=enabled=>{for(const timer of pending.values())clearTimeout(timer);pending.clear();dirty.clear();window.__applyAgentRecording=enabled;};
  let combo=null;
  function save(el){
    if(!dirty.has(el) || !window.__applyAgentRecording || !el?.isConnected || !el.getClientRects().length)return;
    dirty.delete(el);
    if([...document.querySelectorAll('input[type=password]')].some(e=>e.getClientRects().length))return;
    let question=label(el), type=el.tagName==='SELECT'?'select':el.type||'text', value=el.value;
    if(['password','hidden','file','checkbox','submit','button','reset'].includes(type))return;
    if(excluded.test(question+' '+el.name+' '+el.id+' '+el.autocomplete))return;
    if(type==='radio'){
      if(!el.checked)return;
      value=question;
      const group=el.closest('fieldset,[role=radiogroup]');
      question=group?.querySelector('legend')?.textContent || group?.getAttribute('aria-label') || '';
    } else if(el.tagName==='SELECT'){
      if(el.multiple)return;
      value=el.value?el.selectedOptions[0]?.textContent||'':'';
    } else if(el.getAttribute('role')==='combobox'){
      const wrapper=el.closest('.select');
      if(!wrapper || wrapper.querySelector('.select__multi-value'))return;
      type='combobox';value=wrapper.querySelector('.select__single-value')?.textContent||'';
    }
    if(!question.trim() || excluded.test(question))return;
    window.__applyAgentLearn({question:question.trim().slice(0,600),type,value:String(value??'').slice(0,20000)}).catch(()=>{});
  }
  function schedule(el){
    if(!window.__applyAgentRecording)return;
    dirty.add(el);
    clearTimeout(pending.get(el));
    pending.set(el,setTimeout(()=>{pending.delete(el);save(el);},600));
  }
  function flush(){for(const [el,timer] of pending){clearTimeout(timer);save(el);}pending.clear();}
  document.addEventListener('focusin',e=>{if(e.isTrusted&&e.target.getAttribute?.('role')==='combobox')combo=e.target;},true);
  document.addEventListener('input',e=>{if(e.isTrusted)schedule(e.target);},true);
  document.addEventListener('change',e=>{if(e.isTrusted&&window.__applyAgentRecording&&(dirty.has(e.target)||e.target.tagName==='SELECT'||e.target.type==='radio')){dirty.add(e.target);clearTimeout(pending.get(e.target));pending.delete(e.target);save(e.target);}},true);
  document.addEventListener('blur',e=>{if(e.isTrusted){clearTimeout(pending.get(e.target));pending.delete(e.target);save(e.target);}},true);
  document.addEventListener('click',e=>{
    if(!e.isTrusted||!window.__applyAgentRecording)return;
    flush();
    if(combo && e.target.closest?.('[role=option]')){
      const selected=combo;dirty.add(selected);setTimeout(()=>save(selected),50);
    }
  },true);
  window.addEventListener('pagehide',flush);
})();
