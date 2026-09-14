"""Local desktop browser launch and focus; never copy a prepared form to a new tab."""
import os
import subprocess
import sys


def desktop_available():
    return sys.platform in ('darwin','win32') or bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def mode():
    value=os.environ.get('BROWSER_MODE','stream').lower()
    if value not in ('native','stream'):
        raise ValueError('BROWSER_MODE must be native or stream.')
    if value=='native' and not desktop_available():
        raise ValueError('Native browser mode needs a desktop display. Use BROWSER_MODE=stream on a server.')
    return value


def focus(page):
    page.bring_to_front()
    if sys.platform!='darwin':return True
    # Target this automation browser's PID, not another Chrome profile or window.
    client=page.context.browser.new_browser_cdp_session()
    try:
        processes=client.send('SystemInfo.getProcessInfo')['processInfo']
        pid=next(int(p['id']) for p in processes if p['type']=='browser')
        script=f"ObjC.import('AppKit'); $.NSRunningApplication.runningApplicationWithProcessIdentifier({pid}).activateWithOptions(3);"
        result=subprocess.run(['osascript','-l','JavaScript','-e',script],capture_output=True,text=True,timeout=3)
        return result.returncode==0 and result.stdout.strip()=='true'
    except (OSError,subprocess.TimeoutExpired,StopIteration):
        return False
    finally:client.detach()
