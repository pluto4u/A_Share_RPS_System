"""Create a code-and-reference handoff without production market data or credentials."""
from pathlib import Path
import ast
import hashlib
import json
import re
import zipfile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
files = {}
def add(p, dest=None):
    p = Path(p)
    if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.log'}:
        files[dest or p.relative_to(ROOT).as_posix()] = p

for folder in ['rps_analyzer', 'config', 'skills/a-share-rotation-trader', '.agents/skills/a-share-rotation-trader']:
    for p in (ROOT / folder).rglob('*'):
        if p.suffix in {'.py', '.md', '.html', '.yaml', '.yml', '.json'}:
            add(p)
for name in ['README.md', 'requirements.txt', 'run_right_side_research.py', 'run_tdx_formula_audit.py', 'run_tdx_screener.py', 'run_trend_entry_filter.py', 'rps_system.py']:
    add(ROOT / name)
for p in (ROOT / 'tmp').glob('*.py'):
    if re.search(r'siruipu|right_side|dual_strategy|refresh_rps|bottom_ambush|qfq|gate|review_15_20260924|report_10_20260924|cpu_2026092[123]', p.name):
        add(p)
for p in (ROOT / 'tests').glob('*.py'):
    if re.search(r'right_side|qfq|tdx|trend_entry', p.name):
        add(p)
for p in (ROOT / 'docs').glob('*.md'):
    if re.search(r'RPS|rps|tdx|strategy_', p.name):
        add(p)
# Resolve static local Python imports and literal Python script references recursively.
while True:
    before = len(files)
    for p in list(files.values()):
        if p.suffix != '.py':
            continue
        source = p.read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [x.name for x in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        for name in names:
            rel = Path(*name.split('.'))
            for base in [ROOT, p.parent, ROOT / 'tmp']:
                for q in [base / rel.with_suffix('.py'), base / rel / '__init__.py']:
                    add(q)
        for ref in re.findall(r'[\w./-]+\.py', source):
            for base in [ROOT, p.parent]:
                add(base / ref)
    if before == len(files):
        break

for p in (ROOT / 'output').iterdir():
    if p.suffix not in {'.html', '.json', '.csv'} or p.stat().st_size > 12_000_000:
        continue
    if re.search(r'siruipu|right_side|dual_strategy|qfq_verification|limit_ecology_latest|integrated_decision_20260923|top10_20260924|review_15_20260924|20260924_top10|20260923_cpu|gate_repair|local_tdx_gate|qfq_fix', p.name):
        add(p, '资料快照/output/' + p.name)
for p in (ROOT / '08.tdx_qushi').glob('*.EBK'):
    add(p, '资料快照/08.tdx_qushi/' + p.name)

# Conservative secret check. Never print matching values.
suspects = []
patterns = [r'(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\s*[:=]\s*[\"\x27]([^\"\x27\r\n]{12,})[\"\x27]', r'(?i)Bearer\s+([A-Za-z0-9._-]{25,})', r'(sk-[A-Za-z0-9_-]{24,})']
for dest, p in files.items():
    if p.suffix == '.EBK':
        continue
    text = p.read_text(encoding='utf-8-sig', errors='replace')
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            val = m.group(1)
            if re.fullmatch(r'[A-Za-z0-9._-]+', val) and not any(x in val.lower() for x in ['example', 'placeholder', 'your', 'getenv', 'hithink_api_key', 'environment']):
                suspects.append(dest)
if suspects:
    raise SystemExit('Potential credentials require review in: ' + ', '.join(sorted(set(suspects))))

readme = '''# 右侧趋势选股：代码与资料包

这是现有项目的代码与研究资料快照，不是包含行情的开箱即用软件，也不是盈利保证。未修改生产策略、门控和阈值。

## 核心入口
- tmp/run_daily_dual_strategy.py：每日同步、校验、G0/G1、RPS、策略A/B与唯一日报。
- skills/a-share-rotation-trader/scripts/run_integrated_decision.py：进一步执行六道门控。双策略日报生成候选不等于全部执行门控已通过。
- tmp/screen_siruipu_right_side.py：原始公式；screen_siruipu_right_side_v2.py：修订版；screen_bottom_ambush.py：回踩研究。
- tmp/backtest_siruipu_right_side.py、backtest_siruipu_robustness.py：历史回测与稳健性研究。
- tmp/audit_right_side_0918.py、tests/：数据与口径自检。
- rps_analyzer/：RPS、TDX读取及其他相关研究模块。
- docs/、skills/、.agents/skills/：公式、数据边界、风控、退出与报告规范。两处技能目录按原样保留，不假定版本相同。
- 资料快照/：历史报告、候选、门控审计和回测结果，仅供复核。不要复制旧门控到生产output冒充当日验证。

## 环境与数据（需要另行准备）
1. Windows、Python及requirements.txt列出的依赖；另安装并认证hithink-finance CLI。原环境使用过0.1.5；包内不包含CLI安装程序或凭据。
2. 生产库路径 data/hithink/market.duckdb，必须包含v_daily、v_daily_qfq等生产视图。本包不含该库、复权事件原始库或大体积parquet。
3. 本地通达信默认路径 C:\\new_tdx\\vipdoc、C:\\new_tdx\\T0002\\hq_cache、C:\\new_tdx\\incon.dat；搬迁后检查脚本默认值或支持的路径参数。并非所有脚本参数化，部分研究脚本写死日期或本机路径。
4. 原始TDX价格用于价格锚与涨跌停生态；MA/RPS/ATR使用通过本地运营一致性G0的v_daily_qfq，不能拿未复权价格替代。
5. 如恢复已有生产库，请单独安全备份和复制，勿对生产库重新data init；认证使用环境变量或auth login，不把密钥写入脚本。

## 运行（在解压目录根部，数据准备完成后）
```powershell
python -m pip install -r requirements.txt
python tmp/run_daily_dual_strategy.py
python skills/a-share-rotation-trader/scripts/run_integrated_decision.py --help
```
每日流程不要使用--skip-sync。每条同花顺数据库命令都必须--db明确指向项目库。日期参数按目标交易日设置，不照抄历史日期。

运行后核验：实际数据截止日、同步/校验JSON状态、G0/G1、A/B计数、G2至G5、阻断原因。G0/G1失败应零交易，候选不等于买单。每日HTML：output/a_share_analysis_daily_dual_strategy_journal.html。

## 已知边界
- A日报列表截取前30；“刚启动”与“首次回踩”的严格历史去重需结合审计，不能仅凭名称认为已完整实现。
- 历史回测不等于样本外盈利证明；情绪参数、滑点、涨跌停无法成交、T+1、相关性与交易费用需独立检查。
- 打包仅检查文件完整性、Python语法和常见明文密钥模式，未在无行情的新电脑完成端到端部署测试；不保证所有研究分支可直接运行。
- 历史报告可能含股票观点、假设账户配置和本机路径；对外分享前自行确认。未包含真实账户凭据、.env、聊天记录、日志、飞书配置、.git、行情库及缓存。

manifest.json列出所有文件的原始路径、字节数和SHA256。解压后运行 python verify_package.py 检查完整性。
'''
verifier = '''from pathlib import Path
import hashlib,json
r=Path(__file__).resolve().parent
m=json.loads((r/'manifest.json').read_text(encoding='utf-8'))
bad=[x['path'] for x in m['files'] if not (r/x['path']).is_file() or hashlib.sha256((r/x['path']).read_bytes()).hexdigest()!=x['sha256']]
print('PASS' if not bad else 'FAIL', len(m['files']), 'files', bad)
raise SystemExit(bool(bad))
'''
payload = {dest: p.read_bytes() for dest, p in files.items()}
payload['打包说明.md'] = readme.encode('utf-8')
payload['verify_package.py'] = verifier.encode('utf-8')
import html
payload['打开这里.html'] = ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>右侧趋势选股资料包</title><style>body{max-width:980px;margin:50px auto;padding:24px;background:#f4f6f8;color:#183044;font:16px/1.8 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>右侧趋势选股 · 代码与资料包</h1><pre>' + html.escape(readme) + '</pre></html>').encode('utf-8')
manifest = {'created': datetime.now().isoformat(), 'type': 'code_and_reference_without_market_database', 'files': [{'path': k, 'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k,v in sorted(payload.items())]}
payload['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
out = ROOT / 'output' / ('right_side_strategy_bundle_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.zip')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for name, data in sorted(payload.items()):
        z.writestr('right_side_strategy/' + name, data)
with zipfile.ZipFile(out) as z:
    assert z.testzip() is None
    for item in manifest['files']:
        assert hashlib.sha256(z.read('right_side_strategy/' + item['path'])).hexdigest() == item['sha256']
print(json.dumps({'zip': str(out), 'files': len(payload), 'bytes': out.stat().st_size, 'integrity': 'PASS', 'secret_pattern_scan': 'PASS', 'python_syntax': 'PASS'}, ensure_ascii=False))
