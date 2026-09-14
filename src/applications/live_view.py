"""Latest Chromium viewport only, in memory. Readers never touch Playwright objects."""
from threading import Lock
from src.applications.takeover import clean_url


class LiveView:
    def __init__(self):
        self.lock=Lock()
        self.frames={}
        self.clients={}
        self.pages={}

    def attach(self,app_id,page):
        if self.pages.get(app_id) is page:return
        self.close(app_id)
        self.pages[app_id]=page
        client=page.context.new_cdp_session(page)
        self.clients.setdefault(app_id,[]).append(client)
        def frame(event):
            with self.lock:
                self.frames[app_id]={'image':event['data'],'url':clean_url(page.url),
                                     'width':1100,'height':850}
            try:client.send('Page.screencastFrameAck',{'sessionId':event['sessionId']})
            except Exception:pass  # The tab may close while its last frame arrives.
        client.on('Page.screencastFrame',frame)
        client.send('Page.enable')
        client.send('Page.startScreencast',{'format':'jpeg','quality':65,'maxWidth':1100,'maxHeight':850,'everyNthFrame':1})

    def read(self,app_id):
        with self.lock:return self.frames.get(app_id)

    def close(self,app_id):
        self.pages.pop(app_id,None)
        for client in self.clients.pop(app_id,[]):
            try:client.detach()
            except Exception:pass
        with self.lock:self.frames.pop(app_id,None)
