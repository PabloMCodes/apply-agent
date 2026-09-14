"""User-operated live viewport. Typed text stays in memory, never browser_commands."""
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit
from src.applications.generic import GenericSession

class NeedsTakeover(ValueError):
    pass


SAFE_BUTTON = re.compile(r'^(next|next step|continue|save and continue|back|previous|previous step|review|review application|sign in|log in|login|verify|verify code|send code|resend code|create account|create an account|create profile|save profile|sign up|register|continue with google|continue with microsoft|continue with linkedin|sign in with google|sign in with microsoft|sign in with linkedin|reject all|accept all cookies|allow essential cookies|close|send verification code|use another account|try another way)$',re.I)
FINAL = re.compile(r'submit|send application|finish|complete application|apply now|confirm application',re.I)


def clean_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('https','http'):return parsed.scheme+':'+parsed.path
    return parsed.scheme+'://'+(parsed.hostname or '')+parsed.path


def login_page(page):
    host=urlsplit(page.url).hostname or ''
    if host in ('accounts.google.com','login.live.com','login.microsoftonline.com'):return True
    return any(frame.locator('input').evaluate_all(r"""els=>els.some(e=>e.getClientRects().length &&
        (e.type==='password' || /^(code|otp|pin|passcode)$/i.test(e.name||e.id) ||
         /password|passcode|one.?time|verification.?code|security.?code|auth.?token|otp/i.test(
             e.name+' '+e.id+' '+e.autocomplete+' '+[...(e.labels||[])].map(l=>l.innerText).join(' '))))""") for frame in page.frames)


def image_frame(entry):
    session=entry['session'];page=session.page
    signature=view_signature(page)
    if entry.get('view_signature')!=signature or not entry.get('control_token'):
        entry['control_token']=secrets.token_urlsafe(24)
    entry['view_signature']=signature
    # The image is never written to disk; login/MFA contents remain transient.
    return {'image':base64.b64encode(page.screenshot(type='jpeg',quality=75,full_page=False)).decode(),
            'token':entry['control_token'],'url':clean_url(page.url),'width':page.viewport_size['width'],'height':page.viewport_size['height'],
            'tabs':[{'index':i,'url':clean_url(p.url)} for i,p in enumerate(entry['context'].pages) if not p.is_closed()]}


def at_point(page,x,y):
    frame=page.main_frame
    for _ in range(8):
        element=frame.evaluate_handle('([x,y])=>document.elementFromPoint(x,y)',[x,y]).as_element()
        if element is None:raise ValueError('No control at this location. Refresh the view.')
        if element.evaluate('e=>e.tagName') in ('IFRAME','FRAME'):
            rect=element.evaluate('e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y}}')
            child=element.content_frame()
            if child is None:raise ValueError('This frame is not ready.')
            x-=rect['x'];y-=rect['y'];frame=child
            continue
        target=element.evaluate_handle("e=>e.closest('button,a,input,textarea,select,label,[role=button],[role=option],[role=checkbox],[role=combobox]')||e").as_element()
        if target.evaluate('e=>e.tagName')=='LABEL':
            control=target.evaluate_handle('e=>e.control').as_element()
            if control is not None:target=control
        return target
    raise ValueError('Nested frame could not be operated.')


def click_target(session,x,y,mark_final=False):
    page=session.page;target=at_point(page,x,y)
    info=target.evaluate("e=>({tag:e.tagName,type:e.type||'',role:e.getAttribute('role')||'',popup:e.getAttribute('aria-haspopup')||'',label:(e.innerText||e.getAttribute('aria-label')||(['submit','button'].includes(e.type)?e.value:'')||'').trim(),href:e.getAttribute('href')})")
    info['label']=' '.join(info['label'].split())
    if mark_final:
        if login_page(page) or info['tag'] not in ('BUTTON','INPUT','A') and info['role']!='button':
            raise ValueError('Select the actual final application button after finishing login.')
        if not isinstance(session,GenericSession):
            raise ValueError('The supported adapter already identifies the final submit button.')
        session.final_target=target
        session.final_description=session.describe(target)
        return 'Final button marked. Resume review to inspect and explicitly submit.'
    if info['tag']=='A' and info['label'].casefold() in ('apply','apply now'):
        from src.applications.discovery import valid_entry_url
        href=target.evaluate('e=>e.href')
        if valid_entry_url(href) and not re.search(r'/submit|/finish|/complete',urlsplit(href).path,re.I):
            page.goto(href,wait_until='domcontentloaded',timeout=20000)
            return 'Application link opened.'
    if FINAL.search(info['label']) or target==getattr(session,'final_target',None):
        raise ValueError('Final submission is blocked in takeover. Mark the final button, then use the reviewed Submit action.')
    is_button=info['tag']=='BUTTON' or info['role']=='button' or info['tag']=='INPUT' and info['type'] in ('submit','button','image')
    if is_button and info['popup']!='listbox' and not SAFE_BUTTON.fullmatch(info['label']):
        raise ValueError('Ambiguous button blocked. If this is the final action, mark it and review before submission.')
    if info['tag']=='A':
        href=target.get_attribute('href') or ''
        if not href or href.startswith(('javascript:','data:')):
            raise ValueError('Unsupported navigation link.')
    if not is_button and info['tag'] not in ('A','INPUT','TEXTAREA','SELECT','LABEL') and info['role'] not in ('option','checkbox','combobox'):
        raise ValueError('This control needs a site adapter; arbitrary page clicks are not executed.')
    # Record application steps using the same adapter history as automatic navigation.
    label=info['label'].lower()
    if not login_page(page) and label in ('next','next step','continue','save and continue','review','review application','back','previous','previous step'):
        session.navigate('back' if label in ('back','previous','previous step') else 'next')
    else:
        target.click(timeout=10000)
    return 'Page action completed. Refresh if the site is still loading.'


def type_text(page,value):
    for frame in page.frames:
        active=frame.evaluate_handle('()=>document.activeElement').as_element()
        if active and active.evaluate("e=>['INPUT','TEXTAREA'].includes(e.tagName) && !['hidden','submit','button','file','checkbox','radio'].includes(e.type) || e.isContentEditable"):
            active.fill(value,timeout=5000)
            return
    raise ValueError('Click an editable field in the live view first.')



def view_signature(page):
    observations=[]
    for frame in page.frames:
        observations.append([frame.url,frame.evaluate("""()=>[...document.querySelectorAll('input,textarea,select,button,a,[role=button],[role=option],[role=checkbox],[role=combobox],iframe')].filter(e=>e.getClientRects().length).map(e=>{
            const r=e.getBoundingClientRect();return [e.tagName,e.type,e.getAttribute('role'),e.getAttribute('aria-label'),e.innerText,e.disabled,Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];
        })""")])
    return hashlib.sha256(json.dumps(observations).encode()).hexdigest()


def insert_text(page,value):
    """Insert literal text at the focused caret; Enter is never interpreted as submit."""
    for frame in page.frames:
        active=frame.evaluate_handle('()=>document.activeElement').as_element()
        if active and active.evaluate("e=>!e.disabled && !e.readOnly && (e.isContentEditable || e.tagName==='TEXTAREA' || e.tagName==='INPUT' && ['text','email','password','search','tel','url','number'].includes(e.type))"):
            page.keyboard.insert_text(value)
            return
    raise ValueError('Click an editable field before typing.')
