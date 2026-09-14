"""Best-effort common HTML application forms; advanced widgets stay manual."""

import re
from src.applications.greenhouse import GreenhouseSession, SCAN

STANDARD_SCAN = SCAN.replace("document.querySelectorAll('input,textarea,select')",
    "(document.querySelector('[data-apply-agent-form]') || document.createElement('form')).querySelectorAll('input,textarea,select')")


class StandardFormSession(GreenhouseSession):
    scan_script = STANDARD_SCAN

    def accepted_frame(self, frame, index):
        return index == 0

    def submit_button(self):
        root = self.page.locator('[data-apply-agent-form]')
        matches = root.get_by_role('button', name=re.compile(r'^Submit(?: application)?$', re.I))
        buttons = [button for button in matches.all() if button.is_visible()]
        return buttons[0] if len(buttons) == 1 else None


    def refresh_root(self):
        # After leaving a verified application, follow only a unique visible form.
        self.page.evaluate("""() => {
            const forms=[...document.querySelectorAll('form')].filter(f=>f.getClientRects().length && !f.querySelector('input[type=password]'));
            document.querySelectorAll('[data-apply-agent-form]').forEach(f=>f.removeAttribute('data-apply-agent-form'));
            if(forms.length===1)forms[0].setAttribute('data-apply-agent-form','');
        }""")

    def navigation_button(self, direction):
        pattern = r'^(?:next|next step|continue|save and continue|review|review application)$' if direction == 'next' else r'^(?:back|previous|previous step)$'
        matches = self.page.locator('[data-apply-agent-form]').get_by_role('button',name=re.compile(pattern,re.I))
        buttons=[b for b in matches.all() if b.is_visible() and b.is_enabled()]
        return buttons[0] if len(buttons)==1 else None
