"""Human-confirmed fallback page; final submission must be explicitly designated."""
from src.applications.greenhouse import GreenhouseSession


class GenericSession(GreenhouseSession):
    def __init__(self,page):
        super().__init__(page)
        self.final_target=None
        self.final_description=None

    def accepted_frame(self,frame,index):
        return True

    def submit_button(self):
        if self.final_target:
            try:
                if self.final_description != self.describe(self.final_target):return None
                return self.final_target if self.final_target.is_visible() and self.final_target.is_enabled() else None
            except Exception:return None
        return super().submit_button()


    @staticmethod
    def describe(target):
        return target.evaluate("e=>[e.tagName,e.type,e.id,e.getAttribute('role'),e.getAttribute('href'),e.innerText||e.getAttribute('aria-label')||e.value||'']")
