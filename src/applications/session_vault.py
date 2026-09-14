"""Opt-in encrypted browser state, scoped to the application's exact origin."""
import hashlib
import json
import os
import time
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken


def origin(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.port not in (None,443):
        raise ValueError('Saved sessions require a public HTTPS application origin.')
    return 'https://'+parsed.hostname.lower()


class SessionVault:
    def __init__(self,path):
        self.directory=path.parent/'browser-sessions'

    def cipher(self):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory.chmod(0o700)
        key=self.directory/'key'
        try:
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as file:file.write(Fernet.generate_key())
        except FileExistsError:
            pass
        key.chmod(0o600)
        return Fernet(key.read_bytes())

    def target(self,url):
        return self.directory/(hashlib.sha256(origin(url).encode()).hexdigest()+'.enc')

    def save(self,url,state):
        payload={'origin':origin(url),'saved_at':time.time(),'state':state}
        raw=json.dumps(payload).encode()
        if len(raw)>20*1024*1024:raise ValueError('Browser state is too large to save.')
        encrypted=self.cipher().encrypt(raw)
        target=self.target(url);temp=target.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'wb') as file:file.write(encrypted)
        temp.replace(target)

    def load(self,url):
        target=self.target(url)
        if not target.exists():return None
        try:
            data=json.loads(self.cipher().decrypt(target.read_bytes()))
            if data['origin']!=origin(url) or time.time()-data['saved_at']>7*86400:return None
            return data['state']
        except (InvalidToken,ValueError,KeyError):
            return None

    def list(self):
        if not self.directory.exists():return []
        result=[]
        for file in self.directory.glob('*.enc'):
            try:
                data=json.loads(self.cipher().decrypt(file.read_bytes()))
                result.append({'origin':data['origin'],'saved_at':data['saved_at'],'expires_at':data['saved_at']+7*86400})
            except (InvalidToken,ValueError,KeyError):continue
        return result

    def forget(self,url):
        self.target(url).unlink(missing_ok=True)
