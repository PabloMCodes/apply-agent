'use strict';
// One live viewport, backed by the worker's existing browser context.
async function browserPanel(id,onResume) {
  let current=null,busy=false;
  const image=el('img',{alt:'Interactive server browser',class:'live-browser',tabindex:0});
  const url=el('p',{class:'source-url'}),message=el('div'),tabs=el('select',{'aria-label':'Browser tab'});
  const typing=el('input',{type:'password','aria-label':'Text to type in browser',placeholder:'Click a field, then type here',autocomplete:'off'});
  const mark=el('input',{type:'checkbox'}),upload=el('input',{type:'checkbox'});
  const zoom=el('input',{type:'checkbox',checked:window.innerWidth<700});
  const viewport=el('div',{class:'live-viewport'},image);
  image.classList.toggle('zoomed',zoom.checked);
  zoom.addEventListener('change',()=>image.classList.toggle('zoomed',zoom.checked));
  const panel=card('Live browser','Operate the same browser session the worker uses. Sign in yourself, then resume. Final submission is blocked here.',url,tabs,message,el('label',{class:'check-field'},zoom,'Zoom browser to full size (scroll to reach controls)'),viewport);
  async function action(operation,extra={}){
    if(busy)return;
    busy=true;
    panel.querySelectorAll('button,select').forEach(control=>control.disabled=true);
    image.classList.add('busy');
    try{
      const result=await send(`/applications/${id}/browser`,'POST',{operation,token:current?.token||'',...extra});
      if(result.resumed){toast(result.message);onResume();return;}
      current=result;image.src='data:image/jpeg;base64,'+result.image;url.textContent=result.url;
      const selected=tabs.value;tabs.replaceChildren(...result.tabs.map(t=>el('option',{value:String(t.index)},`${t.index+1}: ${t.url}`)));
      if([...tabs.options].some(o=>o.value===selected))tabs.value=selected;
      message.replaceChildren(...(result.message?[notice(result.message)]:[]));
    }catch(error){message.replaceChildren(notice(error.message,'error'));}
    finally{busy=false;panel.querySelectorAll('button,select').forEach(control=>control.disabled=false);image.classList.remove('busy');}
  }
  image.addEventListener('click',async event=>{
    if(!current)return;
    const box=image.getBoundingClientRect();
    await action(mark.checked?'mark_final':upload.checked?'upload_resume':'click',{x:(event.clientX-box.left)*current.width/box.width,y:(event.clientY-box.top)*current.height/box.height});mark.checked=false;upload.checked=false;
  });
  tabs.addEventListener('change',()=>action('tab',{tab:Number(tabs.value)}));
  panel.append(el('div',{class:'actions'},button('Refresh browser',()=>action('refresh'),'secondary'),button('Scroll up',()=>action('scroll',{delta:-550}),'secondary'),button('Scroll down',()=>action('scroll',{delta:550}),'secondary')),
    typing,button('Type into selected field',async()=>{const text=typing.value;typing.value='';await action('type',{text});},'secondary'),
    el('div',{class:'actions'},...['Tab','Escape','Backspace','ArrowDown','ArrowUp'].map(key=>button(key,()=>action('key',{key}),'quiet'))),
    el('label',{class:'check-field'},upload,'Upload the selected resume on my next file-control click'),
    el('label',{class:'check-field'},mark,'Mark final submit button on my next click (does not click it)'),
    el('div',{class:'actions'},button('Remember this site login',()=>action('save_session'),'secondary'),
      button('Resume worker / review',()=>action('resume')),
      button('Use AI on this application page',()=>action('ai'),'secondary')),
    notice('AI page assistance sends visible field labels and saved contact/professional facts to your configured model. It cannot sign in, infer sensitive answers, or submit. Saving a login stores encrypted browser state locally for up to seven days.'));
  await action('start');
  return panel;
}
