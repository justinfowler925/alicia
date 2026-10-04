"""Session-scoped document uploads. Extracted text is source material, never authority."""
import json
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from fastapi import HTTPException

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 60000
TEXT_TYPES = {'.txt', '.md', '.csv', '.json', '.log', '.xml', '.yaml', '.yml', '.py', '.js', '.sql', '.html'}


def extract(data: bytes, name: str) -> str:
    suffix = Path(name).suffix.lower()
    try:
        if suffix in TEXT_TYPES:
            text = data.decode('utf-8-sig')
        elif suffix == '.docx':
            with zipfile.ZipFile(__import__('io').BytesIO(data)) as archive:
                info = archive.getinfo('word/document.xml')
                if info.file_size > MAX_BYTES:
                    raise ValueError('Document expands beyond the size limit')
                root = ElementTree.fromstring(archive.read(info))
                text = '\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
        elif suffix == '.pdf':
            binary = shutil.which('pdftotext') or ('/opt/homebrew/bin/pdftotext' if Path('/opt/homebrew/bin/pdftotext').exists() else None)
            if not binary:
                raise ValueError('PDF extraction unavailable. Attach a text or Word document.')
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory)/'input.pdf'; target = Path(directory)/'output.txt'
                source.write_bytes(data)
                subprocess.run([binary, '-layout', str(source), str(target)], check=True, timeout=20, capture_output=True)
                if target.stat().st_size > MAX_TEXT * 4:
                    raise ValueError('Document is too long. Attach a smaller section.')
                text = target.read_text()
        else:
            raise ValueError('Supported: PDF, DOCX, and UTF-8 text documents. Images are not supported here yet.')
    except (UnicodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError, subprocess.SubprocessError, OSError) as exc:
        raise ValueError('Could not read this document. Try a text export.') from exc
    if not text.strip() or '\x00' in text:
        raise ValueError('No readable text found. Scanned PDFs need a text export.')
    if len(text) > MAX_TEXT:
        raise ValueError('Document is too long. Attach a smaller section (60,000 characters maximum).')
    return text


def directory(store, session_id):
    if not store.get_session(session_id):
        raise HTTPException(404, 'Unknown session')
    # Session IDs must never become arbitrary paths, including legacy ids.
    import hashlib
    return store.path.parent / 'chat-attachments' / hashlib.sha256(session_id.encode()).hexdigest()


def save(store, session_id, name, data):
    root = directory(store, session_id)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, 'Choose a non-empty file under 10 MB.')
    name = Path(name.replace('\\', '/')).name
    if not name or len(name) > 255:
        raise HTTPException(422, 'Invalid filename')
    try:
        content = extract(data, name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    attachment = {'id': uuid.uuid4().hex, 'name': name, 'size': len(data), 'content': content}
    root.mkdir(parents=True, exist_ok=True)
    (root / (attachment['id']+'.json')).write_text(json.dumps(attachment))
    return {k:v for k,v in attachment.items() if k != 'content'}


def load(store, session_id, ids):
    root = directory(store, session_id)
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Each attachment may only be included once.")
    out = []
    for identifier in ids:
        try:
            if uuid.UUID(identifier).hex != identifier: raise ValueError()
            out.append(json.loads((root / (identifier+'.json')).read_text()))
        except (ValueError, OSError) as exc:
            raise HTTPException(404, 'Attachment not found in this conversation. Attach it again.') from exc
    if sum(len(a['content']) for a in out) > MAX_TEXT:
        raise HTTPException(422, 'Combined attachments exceed 60,000 characters. Send fewer files.')
    return out
