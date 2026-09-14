"""PDF/TXT storage and text extraction. Profile facts still need user confirmation."""

from io import BytesIO
from pathlib import Path
import uuid
from pypdf import PdfReader

MAX_SIZE = 5 * 1024 * 1024


def extract(filename, content):
    suffix = Path(filename).suffix.lower()
    if len(content) > MAX_SIZE:
        raise ValueError('Resume must be 5 MB or smaller.')
    if suffix == '.txt':
        try:
            return content.decode('utf-8')[:100000], '.txt'
        except UnicodeDecodeError:
            raise ValueError('Text resumes must use UTF-8 encoding.') from None
    if suffix != '.pdf' or not content.startswith(b'%PDF-'):
        raise ValueError('Choose a PDF or UTF-8 text file.')
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or len(reader.pages) > 30:
            raise ValueError('Use an unencrypted resume with at most 30 pages.')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)[:100000]
        return text, '.pdf'
    except ValueError:
        raise
    except Exception:
        raise ValueError('Unable to read this PDF. Try exporting it again or paste resume text.') from None


def save_file(db_path, content, suffix):
    directory = db_path.parent / 'resumes'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.uuid4().hex + suffix)
    target.write_bytes(content)
    target.chmod(0o600)
    return target.name



def list_resumes(path):
    from src.db.database import connect
    from src.setup import store
    # Preserve the original single resume on upgrade, without moving its file.
    old = store.get(path, 'resume')
    with connect(path) as conn:
        if old:
            conn.execute('INSERT OR IGNORE INTO resumes(title,filename,original_name,text,size) VALUES (?,?,?,?,?)',
                         (Path(old['original_name']).stem, old['filename'], old['original_name'], old['text'], old['size']))
        return [dict(r) for r in conn.execute('SELECT * FROM resumes ORDER BY id')]


def choose(path, application_id, job_title):
    import re
    from src.db.database import connect
    items = list_resumes(path)
    with connect(path) as conn:
        chosen = conn.execute('SELECT resume_id FROM application_resume WHERE application_id=?', (application_id,)).fetchone()
    if chosen:
        result = next((r for r in items if r['id'] == chosen[0]), None)
        if not result:
            raise ValueError('The selected resume was removed. Choose another resume before retrying.')
        return result, 'Selected by you'
    if len(items) <= 1:
        return (items[0], 'Only saved resume') if items else (None, 'No resume saved')
    def words(text):
        return set(re.findall(r'[a-z0-9]+', text.casefold())) - {'resume','the','and','for','my'}
    ranked = sorted(((len(words(job_title) & words(r['title']+' '+r['roles'])), r['id'], r) for r in items), reverse=True)
    if ranked[0][0] == 0 or ranked[0][0] == ranked[1][0]:
        raise ValueError('Multiple resumes fit equally, or none matches the job title. Choose a resume before preparing.')
    return ranked[0][2], 'Unique best title / role keyword match — review the selected file'
