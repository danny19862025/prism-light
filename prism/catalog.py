# SPDX-License-Identifier: GPL-3.0-only
"""Read-only metadata import from locally supplied recovered plugin source."""
import re
from pathlib import Path
import zipfile

def import_catalog(path):
    path=Path(path);rows=[]
    def add(name,raw):
        if len(raw)>2_000_000:raise ValueError('Plugin exceeds metadata import limit.')
        source=raw.decode('utf-8',errors='replace')
        def returned(key):
            match=re.search(r'export\s+function\s+'+key+r'\s*\([^)]*\)\s*\{\s*return\s+([^;\n]+)',source)
            return match.group(1).strip().strip('"\'')[:200] if match else 'Dynamic / unavailable'
        rows.append({'file':name,'name':returned('Name'),'vendor':returned('VendorId'),'product':returned('ProductId'),'status':'Reference only — requires porting; not a loaded driver'})
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            entries=[i for i in z.infolist() if '/Plugins/' in '/'+i.filename and i.filename.endswith('.js')]
            if len(entries)>5000 or sum(i.file_size for i in entries)>64_000_000:raise ValueError('Catalog archive exceeds import limits.')
            for i in entries:add(i.filename,z.read(i))
    else:
        entries=list(path.rglob('*.js')) if path.is_dir() else [path]
        if len(entries)>5000:raise ValueError('Too many plugin files.')
        for p in entries:add(str(p),p.read_bytes())
    return rows
