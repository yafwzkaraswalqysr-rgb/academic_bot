from __future__ import annotations
import html, ipaddress, os, re, socket
from pathlib import Path
from urllib.parse import urlparse
from config import BASE_DIR, MAX_FILE_SIZE, SUPPORTED_EXTENSIONS

def safe_html_escape(text): return html.escape(str(text or ''), quote=False)

def sanitize_filename(name):
    name=Path(str(name or 'file')).name
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip('. ')
    return name[:180] or 'file'

def get_file_hash(path):
    import hashlib
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def is_safe_path(path, base_dir=BASE_DIR):
    try:
        p=Path(path).resolve(); b=Path(base_dir).resolve()
        return p==b or b in p.parents
    except Exception: return False

def validate_file(path):
    p=Path(path)
    if not is_safe_path(p): return False,'مسار الملف غير مسموح.'
    if not p.is_file(): return False,'الملف غير موجود.'
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS: return False,'نوع الملف غير مدعوم.'
    if p.stat().st_size>MAX_FILE_SIZE: return False,'حجم الملف أكبر من الحد المسموح.'
    return True,'ok'

def _public_ip(host):
    try:
        for info in socket.getaddrinfo(host,None,socket.AF_UNSPEC,socket.SOCK_STREAM):
            ip=ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception: return False

def is_ssrf_safe_url(url):
    try:
        p=urlparse(url)
        if p.scheme not in {'http','https'} or not p.hostname: return False
        if p.username or p.password: return False
        return _public_ip(p.hostname)
    except Exception: return False
