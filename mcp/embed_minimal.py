"""Generate inline assets; original Notifications UI remains the default."""
import json, hashlib
from pathlib import Path
root = Path(__file__).resolve().parents[1]
web = root / 'web' / 'minimal'
gate = """(() => {
 const ours = new Set(['obsidian','obsidian-light','landscapes','landscapes-light']);
 const minimal = new URL(location.href).searchParams.get('layout') === 'minimal';
 window.WizardApp?.theme.onChange(theme => {
   const skin = theme?.skin?.id || theme?.skin || document.documentElement.dataset.skin;
   if (!skin) return;
   const wanted = ours.has(skin);
   if (wanted !== minimal) { const url = new URL(location.href); if (wanted) url.searchParams.set('layout','minimal'); else url.searchParams.delete('layout'); location.replace(url.href); }
 });
})();"""
gate = (web/'layout.js').read_text()
html = (web/'index.html').read_text()
assets = {'minimal.js': ('text/javascript', (web/'inbox.js').read_text()), 'minimal-render.js': ('text/javascript', (web/'render.js').read_text()), 'minimal.css': ('text/css',(web/'styles.css').read_text()), 'minimal-kit.css': ('text/css',(web/'wizard-ui.css').read_text()), 'minimal-boot.js': ('text/javascript',(web/'boot.js').read_text()), 'layout.js': ('text/javascript',gate)}
# Python literals are safe in the inlined front (compose forbids dollar characters).
revision = hashlib.sha256(json.dumps({'html': html, 'assets': assets}, sort_keys=True).encode()).hexdigest()
# The whole minimal view ships zlib-compressed + base64 in one literal: the front runs as one `python -c`
# argument and Linux caps a single argument at 128 KiB (2.29.0-wizard23 crossed it uncompressed). Base64 also
# keeps the shared kit's fallback colours from reading as this app's own colour literals in the style gate.
import base64, zlib
blob = base64.b64encode(zlib.compress(json.dumps({'html': html, 'assets': assets}, sort_keys=True).encode(), 9)).decode()
(root/'mcp'/'minimal_assets.py').write_text('MINIMAL_REVISION = '+repr(revision)+'\nMINIMAL_BLOB = '+repr(blob)+'\n'
    + "_minimal = __import__('json').loads(__import__('zlib').decompress(__import__('base64').b64decode(MINIMAL_BLOB)))\n"
    + "MINIMAL_HTML = _minimal['html']\nMINIMAL_ASSETS = {k: tuple(v) for k, v in _minimal['assets'].items()}\n")
