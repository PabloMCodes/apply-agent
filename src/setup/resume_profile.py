"""Conservative local extraction. Never infer demographic or work-eligibility answers."""
import re

SKILLS = ['Python','JavaScript','TypeScript','Java','C++','C#','Go','Rust','SQL','HTML','CSS','React','Node.js','Django','Flask','FastAPI','Spring','PostgreSQL','MySQL','MongoDB','Redis','Docker','Kubernetes','AWS','Azure','Git','Linux','Swift','Kotlin','TensorFlow','PyTorch']


def suggest(text):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    header='\n'.join(lines[:8]); result={}; evidence={}; application={}
    def put(key,value,line):
        if value:
            result[key]=value.strip(); evidence[key]=line.strip()[:500]
    email=re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',header)
    if email: put('email',email.group(),email.group())
    phone=re.search(r'(?<!\w)(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)',header)
    if phone: put('phone',phone.group(),phone.group())
    for key,pattern in [('linkedin',r'(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+/?'),('github',r'(?:https?://)?(?:www\.)?github\.com/[\w-]+/?')]:
        match=re.search(pattern,header,re.I)
        if match: put(key,match.group() if match.group().startswith('http') else 'https://'+match.group(),match.group())
    for line in lines[:3]:
        name=re.sub(r'^(?:name|full name)\s*:\s*','',line,flags=re.I).split('|')[0].strip()
        parts=name.split()
        if 2<=len(parts)<=4 and all(re.fullmatch(r"[^\W\d_][^\W\d_'.’-]*\.?",p) for p in parts) and not re.search(r'engineer|developer|resume|curriculum|university|college|science|skills|experience',name,re.I):
            put('name',name,line);put('first_name',parts[0],line);put('last_name',' '.join(parts[1:]),line);break
    location=re.search(r'(?:Location\s*:\s*)?\b([A-Z][A-Za-z .\'-]+,\s*[A-Z]{2}(?:\s+\d{5})?)\b',header)
    if location: put('location',location.group(1),location.group())
    website=re.search(r'(?:website|portfolio)\s*:\s*((?:https?://)?[\w.-]+\.[A-Za-z]{2,}(?:/[^\s|]*)?)',header,re.I)
    if website: put('website',website.group(1) if website.group(1).startswith('http') else 'https://'+website.group(1),website.group())
    skills=[skill for skill in SKILLS if re.search(r'(?<![\w+#])'+re.escape(skill)+r'(?![\w+#])',text,re.I)]
    if skills: result['skills']=skills; evidence['skills']='Explicit technology names in resume text'
    # Education is extracted only from a named education section.
    section=re.search(r'(?im)^\s*education\s*:?\s*$([\s\S]*?)(?=^\s*(?:experience|employment|projects|skills|certifications|activities)\s*:?\s*$|\Z)',text)
    if section:
        edu=section.group(1)
        school=next((l.strip() for l in edu.splitlines() if re.search(r'\b(university|college|institute of technology)\b',l,re.I)),None)
        if school: application['school']=re.split(r'\s*[|•]\s*|\s{2,}',school)[0];evidence['school']=school
        degree=re.search(r'\b(?:Bachelor(?:[’\']s)?(?: of (?:Science|Arts|Engineering))?|Master(?:[’\']s)?(?: of (?:Science|Arts|Engineering))?|Ph\.?D\.?|B\.?S\.?|M\.?S\.?)\b',edu,re.I)
        if degree: application['degree']=degree.group();evidence['degree']=degree.group()
        major=re.search(r'(?:\bin\s+|Major\s*:\s*)([A-Za-z][A-Za-z &/-]+?)(?=\s*[|,•\n]|\s+\d|$)',edu,re.I)
        if major: application['discipline']=major.group(1).strip();evidence['discipline']=major.group()
        gpa=re.search(r'\bGPA\s*:?\s*(\d(?:\.\d+)?(?:\s*/\s*\d(?:\.\d+)?)?)',edu,re.I)
        if gpa: application['gpa']=gpa.group(1);evidence['gpa']=gpa.group()
        graduation=re.search(r'(?:expected|graduat(?:ion|ing|ed))[^\n]*?\b((?:19|20)\d{2})\b',edu,re.I)
        if graduation: application['graduation_year']=graduation.group(1);evidence['graduation_year']=graduation.group()
    for key,label in [('current_company','Current (?:company|employer)'),('current_title','Current (?:job )?title')]:
        match=re.search(r'(?im)^'+label+r'\s*:\s*(.+)$',text)
        if match: application[key]=match.group(1).strip();evidence[key]=match.group()
    result['application_answers']=application
    return {'values':result,'evidence':evidence,'message':'Extracted locally. Review the filled fields and save your profile. Existing answers are kept.'}
