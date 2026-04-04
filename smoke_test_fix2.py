"""Smoke test for Fix Prompt 2 changes."""
import ast, sys, re

errors = []
ok = []

# 1. Python syntax
for f in ['app.py', 'video_processor.py']:
    try:
        with open(f, encoding='utf-8') as fh:
            ast.parse(fh.read())
        ok.append(f + ' [syntax OK]')
    except SyntaxError as e:
        errors.append(f + f': SyntaxError at line {e.lineno}: {e.msg}')

# 2. app.py checks
txt = open('app.py', encoding='utf-8').read()
app_checks = [
    'generate_srt_content',
    'generate_vtt_content',
    'export/srt',
    'export/vtt',
    'srt_url',
    'vtt_url',
    '_job.segments = json.dumps',
    'export_srt',
    'export_vtt',
]
for item in app_checks:
    if item in txt:
        ok.append('app.py has: ' + item)
    else:
        errors.append('app.py MISSING: ' + item)

# 3. index.html checks
html = open('templates/index.html', encoding='utf-8').read()
html_checks = [
    'start-time',
    'end-time',
    'set-current-start',
    'set-current-end',
    'subtitle-input-area',
    'undo-bar',
    'subtitle-list',
    'export-actions',
]
for needle in html_checks:
    if needle in html:
        ok.append('index.html has: ' + needle)
    else:
        errors.append('index.html MISSING: ' + needle)

# Stray div check: after search-wrap close, should be subtitle-input-area, not another </div>
if re.search(r'class="search-wrap"[^<]*<input[^>]*>[^<]*</div>\s*</div>', html):
    errors.append('index.html: stray </div> still after search-wrap')
else:
    ok.append('index.html: no stray </div> after search-wrap')

# No double </div> at 0-indent before Floating Styles Panel
# (main-workspace close at 4-spaces + app-container close at 0-spaces is correct; stray was 2nd 0-indent div)
if re.search(r'\n</div>\n</div>\n', html):
    errors.append('index.html: extra stray 0-indent closing div still present')
else:
    ok.append('index.html: no extra stray div at bottom')

# 4. style.css checks
css = open('static/css/style.css', encoding='utf-8').read()
css_checks = [
    'color-bg-1',
    'flex-start',
    'calc(100vh - 460px)',
    'min-height: 120px',
    'object-fit: contain',
    'export-panel',
    'status-msg',
    'flex-shrink: 0',
]
for needle in css_checks:
    if needle in css:
        ok.append('style.css has: ' + needle)
    else:
        errors.append('style.css MISSING: ' + needle)

if 'max-width: 500px' in css:
    errors.append('style.css: max-width: 500px still in editor-section (should be removed)')
else:
    ok.append('style.css: 500px cap removed from editor-section')

# 5. script.js check
js = open('static/js/script.js', encoding='utf-8').read()
if '_showExportActions(data.srt_url' in js:
    ok.append('script.js: legacy burn calls _showExportActions')
else:
    errors.append('script.js MISSING: _showExportActions in legacy burn path')

if 'Burn complete! Downloading' in js:
    ok.append('script.js: updated burn complete toast message')
else:
    errors.append('script.js MISSING: updated burn complete message')

print('OK (%d):' % len(ok))
for x in ok:
    print('  [v]', x)

if errors:
    print()
    print('ERRORS (%d):' % len(errors))
    for x in errors:
        print('  [X]', x)
    sys.exit(1)
else:
    print()
    print('ALL CHECKS PASSED')
