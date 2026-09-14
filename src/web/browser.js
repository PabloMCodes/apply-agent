'use strict';
// Inputs run in order. Background refresh never overlaps an input request.
async function browserPanel(id,onResume,watching=false) {
  const browser=await api('/browser/status');
  if(browser.mode==='native')return nativeBrowserPanel(id,onResume,watching);
  let current=null,busy=false,pending=0,chain=Promise.resolve(),generation=0,attached=false,attachAttempts=0,autoControl=watching;
  const image=el('img',{alt:'Interactive server browser',class:'live-browser',tabindex:0,draggable:false});
  const url=el('p',{class:'source-url'}),message=el('div'),tabs=el('select',{'aria-label':'Browser tab'});
  const keyboard=el('textarea',{'aria-label':'Browser keyboard input',class:'browser-keyboard',autocomplete:'off',autocapitalize:'off',spellcheck:'false'});
  const mark=el('input',{type:'checkbox'}),upload=el('input',{type:'checkbox'});
  const zoom=el('input',{type:'checkbox',checked:window.innerWidth<700});
  const viewport=el('div',{class:'live-viewport'},image,keyboard);
  image.classList.toggle('zoomed',zoom.checked);
  zoom.addEventListener('change',()=>image.classList.toggle('zoomed',zoom.checked));
  const panel=card('Live browser','Watch preparation live. Take control to click, type, and scroll directly in the page. Submission still requires your final review.',url,tabs,message,el('label',{class:'check-field'},zoom,'Zoom browser to full size (scroll to reach controls)'),viewport);
  panel.classList.add('live-panel');
  const controls=el('div');
  const takeControl=button('Take control',()=>enqueue('start'),'secondary');
  const review=button('Review answers & submit',()=>enqueue('review'),'secondary');
  function mode(){panel.dataset.mode=watching?'watch':'control';controls.hidden=watching;tabs.hidden=watching;takeControl.hidden=!watching;takeControl.disabled=watching&&busy;}
  function draw(result){
    if(result.image)image.src='data:image/jpeg;base64,'+result.image;
    if(result.url)url.textContent=result.url;
    if(result.token)current=result;
    if(result.tabs){
      const selected=tabs.value;tabs.replaceChildren(...result.tabs.map(t=>el('option',{value:String(t.index)},`${t.index+1}: ${t.url}`)));
      if([...tabs.options].some(o=>o.value===selected))tabs.value=selected;
    }
  }
  async function action(operation,extra={}){
    busy=true;
    try{
      const result=await send(`/applications/${id}/browser`,'POST',{operation,token:current?.token||'',...extra});
      if(result.resumed&&operation==='review'){generation++;onResume();return true;}
      if(result.resumed){watching=true;mode();message.replaceChildren(notice(result.message));return true;}
      if(operation==='start'){watching=false;mode();}
      draw(result);
      if(operation!=='refresh')message.replaceChildren(...(result.message?[notice(result.message)]:[]));
      return true;
    }catch(error){
      if(operation==='resume'){watching=false;mode();}
      generation++;message.replaceChildren(notice(error.message+' Pending input was stopped; check the page before continuing.','error'));
      return false;
    }finally{busy=false;}
  }
  function enqueue(operation,extra={}){
    if(pending>=100){message.replaceChildren(notice('Input is catching up. Pause typing until the page responds.','error'));return Promise.resolve(false);}
    const version=generation;pending++;
    const task=chain.then(()=>version===generation?action(operation,extra):false).finally(()=>{pending--;extra.text='';});
    chain=task.catch(()=>{});return task;
  }
  image.addEventListener('click',event=>{
    if(watching||!current)return;
    const box=image.getBoundingClientRect();
    keyboard.focus({preventScroll:true});
    enqueue(mark.checked?'mark_final':upload.checked?'upload_resume':'click',{token:current.token,x:(event.clientX-box.left)*current.width/box.width,y:(event.clientY-box.top)*current.height/box.height});
    mark.checked=false;upload.checked=false;
  });
  const keys=new Set(['Tab','Escape','Backspace','Delete','ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End']);
  keyboard.addEventListener('keydown',event=>{
    if(watching)return;
    if(event.key==='Enter'){event.preventDefault();message.replaceChildren(notice('Use the page’s Next button to continue. Final submission is available in review.'));return;}
    if(keys.has(event.key)){event.preventDefault();enqueue('key',{key:event.key==='Tab'&&event.shiftKey?'Shift+Tab':event.key});}
    else if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='a'){event.preventDefault();enqueue('key',{key:'ControlOrMeta+A'});}
  });
  keyboard.addEventListener('beforeinput',event=>{
    if(watching||event.isComposing)return;
    event.preventDefault();
    if(event.inputType.startsWith('delete'))enqueue('key',{key:event.inputType.includes('Forward')?'Delete':'Backspace'});
    else if(event.data)enqueue('insert',{text:event.data});
  });
  keyboard.addEventListener('compositionend',event=>{if(!watching&&event.data)enqueue('insert',{text:event.data});keyboard.value='';});
  keyboard.addEventListener('input',()=>{keyboard.value='';});
  keyboard.addEventListener('paste',event=>{event.preventDefault();if(!watching)enqueue('insert',{text:event.clipboardData.getData('text/plain').slice(0,20000)});});
  let wheel=0,wheelTimer,scrollPoint={x:550,y:425},touchY=null,touchMoved=false;
  function scroll(delta,clientX,clientY){
    const box=image.getBoundingClientRect();
    scrollPoint={x:(clientX-box.left)*1100/box.width,y:(clientY-box.top)*850/box.height};
    wheel+=delta;
    if(wheelTimer)return;
    wheelTimer=setTimeout(()=>{wheelTimer=null;const delta=Math.max(-2000,Math.min(2000,Math.round(wheel)));wheel=0;if(delta)enqueue('scroll',{delta,...scrollPoint});},40);
  }
  image.addEventListener('wheel',event=>{if(watching)return;event.preventDefault();scroll(event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?850:1),event.clientX,event.clientY);},{passive:false});
  image.addEventListener('touchstart',event=>{touchMoved=false;touchY=event.touches.length===1?event.touches[0].clientY:null;},{passive:true});
  image.addEventListener('touchmove',event=>{if(watching||touchY===null||event.touches.length!==1)return;const y=event.touches[0].clientY,delta=touchY-y;if(Math.abs(delta)>2){touchMoved=true;event.preventDefault();scroll(delta,event.touches[0].clientX,y);touchY=y;}},{passive:false});
  image.addEventListener('click',event=>{if(touchMoved){event.stopImmediatePropagation();touchMoved=false;}},true);
  tabs.addEventListener('change',()=>enqueue('tab',{tab:Number(tabs.value)}));
  controls.append(el('details',{},el('summary',{},'Resume upload and final-button controls'),
    el('label',{class:'check-field'},upload,'Upload the selected resume on my next file-control click'),
    el('label',{class:'check-field'},mark,'Mark final submit button on my next click (does not click it)')),
    el('div',{class:'actions'},button('Remember this site login',()=>enqueue('save_session'),'secondary'),
      button('Resume worker / review',()=>{watching=true;mode();return enqueue('resume');}),
      button('Use AI on this application page',()=>enqueue('ai'),'secondary')));
  panel.append(controls,el('div',{class:'actions'},takeControl,review),notice('AI assistance uses visible field labels and saved contact/professional facts. Typed text and live frames stay in memory.'));
  mode();
  if(!watching)await action('start');
  async function tick(){
    if(panel.isConnected)attached=true;
    else if(attached||attachAttempts++>20){generation++;clearTimeout(wheelTimer);return;}
    if(!document.hidden){
      if(watching){
        try{const result=await api(`/applications/${id}/browser/frame`,{cache:'no-store'});draw(result);
          if(autoControl&&!busy&&!pending&&['ready','takeover'].includes(result.status)){autoControl=false;await enqueue('start');}
          takeControl.disabled=busy||result.status==='preparing';message.replaceChildren(notice(result.status==='preparing'?'Worker is filling the application…':'Preparation paused or ready. Take control or return to review.'));}catch(error){message.replaceChildren(notice(error.message));}
      }else if(!busy&&!pending)await enqueue('refresh');
    }
    setTimeout(tick,watching?150:250);
  }
  setTimeout(tick,150);
  return panel;
}


// Desktop input stays in Chromium. The website only manages the queue and memory.
async function nativeBrowserPanel(id,onReview,preparing){
  let busy=false,attached=false,attempts=0,ended=false;
  const message=el('div'),learning=el('p',{},'Checking answer memory…');
  const open=button('Open application tab',()=>action('focus'));
  const panel=card('Your application tab','Your selected applications open as tabs in one Chromium window. The worker fills them in order; you can finish and review completed tabs while it works on the next.',message,open,learning);
  async function action(operation){
    if(busy)return;
    busy=true;
    try{
      const result=await send(`/applications/${id}/browser`,'POST',{operation});
      if(result.submitted){ended=true;toast(result.message);location.hash='applications';return;}
      if(result.resumed&&operation==='review'){ended=true;onReview();return;}
      if(result.message)message.replaceChildren(notice(result.message));
    }catch(error){message.replaceChildren(notice(error.message,'error'));}
    finally{busy=false;}
  }
  panel.append(el('div',{class:'actions'},
    button('Review saved application details',()=>action('review'),'secondary'),
    button('Resume filling this tab',()=>action('resume'),'secondary'),
    button('Remember site login',()=>action('save_session'),'secondary'),
    button('I submitted this application',()=>action('native_submitted'),'secondary')),
    link('Manage saved answers','#profile','quiet'),
    notice('Review before submitting in the employer tab. Then mark it submitted here to update tracking and close only that tab. Passwords, verification codes, signatures, and consent checkboxes are not saved as reusable answers.'));
  open.disabled=preparing;
  if(!preparing)await action('focus');
  async function tick(){
    if(ended)return;
    if(panel.isConnected)attached=true;else if(attached||attempts++>15)return;
    if(!document.hidden&&!busy){
      try{
        const result=await api(`/applications/${id}/learning`);
        learning.textContent=`${result.changes_saved} answer changes saved automatically. Learning ${result.enabled?'on':'off'} (Preferences).`;
        open.disabled=result.status==='preparing';
      }catch(error){learning.textContent=error.message;}
    }
    setTimeout(tick,1500);
  }
  setTimeout(tick,100);
  return panel;
}
