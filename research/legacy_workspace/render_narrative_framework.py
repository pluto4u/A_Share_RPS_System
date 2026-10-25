from pathlib import Path
import html
import re

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'docs/narrative_factor_framework_20261011.md'
target = ROOT / 'output/a_share_analysis_20261011_narrative_factors.html'

def inline(s):
    s = html.escape(s)
    s = re.sub(r'\[([^\]]+)\]\((https://[^)]+)\)', r'<a href="\2">\1</a>', s)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)

parts, nav = [], []
for block in source.read_text(encoding='utf-8').split('\n\n'):
    block = block.strip()
    if not block:
        continue
    if block.startswith('#'):
        level = len(block) - len(block.lstrip('#'))
        title = block[level:].strip()
        anchor = 'section-' + str(len(parts))
        parts.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
        if level == 2:
            nav.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
    elif re.match(r'^\*\*N\d{2} ', block):
        title, rest = block[2:].split('**', 1)
        parts.append(f'<details><summary>{html.escape(title)}</summary><p>{inline(rest.lstrip("｜"))}</p></details>')
    elif block.startswith('- '):
        parts.append('<ul>' + ''.join('<li>' + inline(x[2:]) + '</li>' for x in block.splitlines()) + '</ul>')
    elif re.match(r'^\d+\. ', block):
        parts.append('<ol>' + ''.join('<li>' + inline(re.sub(r'^\d+\. ', '', x)) + '</li>' for x in block.splitlines()) + '</ol>')
    else:
        parts.append('<p>' + inline(block) + '</p>')

page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>叙事经济学｜量化因子研究框架</title><style>
:root{color-scheme:light;--ink:#203229;--muted:#627168;--line:#d9e2d9;--paper:#fbfcf8;--accent:#235b43}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.85 system-ui,"Microsoft YaHei",sans-serif}header{background:#193d2e;color:white;padding:34px max(24px,calc((100vw - 1120px)/2))}header b{font-size:25px}header p{margin:8px 0 0;color:#d2e1d6}.layout{max-width:1180px;margin:auto;display:grid;grid-template-columns:235px minmax(0,1fr);gap:38px;padding:36px 24px}nav{position:sticky;top:20px;align-self:start;font-size:13px}nav a{display:block;margin-bottom:12px;text-decoration:none}a{color:var(--accent)}h1{font-size:27px;line-height:1.4}h2{font-size:22px;border-top:1px solid var(--line);padding-top:28px;margin-top:40px}h3{font-size:18px;margin-top:30px}p{margin:14px 0}li{margin:12px 0}details{border-bottom:1px solid var(--line);padding:12px 0}summary{cursor:pointer;font-weight:650}details p{font-size:14px;color:var(--muted)}.note{font-size:13px;color:var(--muted);border-top:1px solid var(--line);margin-top:40px;padding-top:16px}button{background:transparent;border:1px solid var(--line);padding:8px 12px;border-radius:4px;color:var(--accent);cursor:pointer;margin-bottom:18px}@media(max-width:760px){.layout{display:block;padding:20px}nav{position:static;border-bottom:1px solid var(--line);margin-bottom:25px}nav a{display:inline-block;margin-right:14px}header{padding:24px}h1{font-size:23px}}@media print{nav,button{display:none}.layout{display:block}details{break-inside:avoid}}
</style><header><b>从叙事到可检验的预期变化</b><p>8个维度 · 24个研究因子 · 6项优先工作 · 尚未回测，不改生产门控</p></header><div class="layout"><nav>'''
page += ''.join(nav) + '</nav><main><button onclick="document.querySelectorAll(\'details\').forEach(x=>x.open=true)">展开全部因子</button> '
page += '<button onclick="document.querySelectorAll(\'details\').forEach(x=>x.open=false)">收起因子</button>' + '\n'.join(parts)
page += '<p class="note">研究日期：2026-10-11。页码为原PDF物理页；候选因子不代表已验证预测能力。源文档：docs/narrative_factor_framework_20261011.md。</p></main></div></html>'
target.write_text(page, encoding='utf-8')
assert len(re.findall(r'<summary>N\d{2} ', page)) == 24
assert len(nav) == 10
print(f'HTML written: {target}; 24 factor entries; {len(nav)} section links')
