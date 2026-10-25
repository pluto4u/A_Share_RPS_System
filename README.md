# A 股 TDX / RPS 选股

最新研究入口：[重新回测协议与下一阶段路线](docs/REBACKTEST-PLAN-20261024.md)。
本地修改后先运行 `python tools/review_strategy_archive.py` 审阅源码漂移；
旧卡保留，不把重新归档当成收益验收。

工程目录、实际文件位置和兼容路径详见 [主工程整理说明](docs/PROJECT-ORGANIZATION.md)。
查找功能请使用 [运行入口索引](docs/ENTRYPOINTS.md)；使用说明已归入 `docs/guides/`，
原根目录实验脚本已归入 `research/experiments/`。

```powershell
python tools/workspace.py entries --category daily_workflow
python tools/workspace.py catalog
```

## 策略档案与市场宽度出图

2026-10-24已将Codex工作树中的归档和出图功能合并到本主目录，保留主目录现有策略与研究入口。
归档索引见 [strategy_archive/INDEX.md](strategy_archive/INDEX.md)，存储约定见
[docs/AI-STORAGE.md](docs/AI-STORAGE.md)。

```powershell
python tools/workspace.py status
python tools/workspace.py archive
python tools/workspace.py breadth
```

市场宽度默认读取 `C:/new_tdx`，图片、文案和可复核数据写入 `outputs/workflows/tdx_breadth/`。
原有TDX选股入口仍按下面文档写入 `output/`；本次迁入功能统一使用 `outputs/`。
原工作树README完整保存在 [docs/WORKTREE-README-20261024.md](docs/WORKTREE-README-20261024.md)。

基于本地通达信导出的日线数据，计算 RPS 与多层筛选（L1 / L2 / 火车轨 / 早期启涨），输出 CSV 并可导入通达信自定义板块。

---

## 文档索引

详见 **[docs/tdx_docs_index.md](docs/tdx_docs_index.md)**。核心文档：

| 文档 | 说明 |
|------|------|
| [docs/tdx_rps_screener_guide.md](docs/tdx_rps_screener_guide.md) | 主入口：依赖、命令、输出文件、`csv_to_tdx_blk` |
| [docs/strategy_macro_regime.md](docs/strategy_macro_regime.md) | 宏观状态（过热 / 牛熊九宫格）与周度板块强度白名单 |
| [docs/tdx_rps_extension_zhutu.md](docs/tdx_rps_extension_zhutu.md) | RPS 扩展数据与主图公式 |
| [docs/strategy_early_launch.md](docs/strategy_early_launch.md) | 早期启涨策略 |
| [docs/strategy_hong_zhanfa.md](docs/strategy_hong_zhanfa.md) | 洪战法量化定义 |
| [learn/tdx_taoboshi_reference.md](learn/tdx_taoboshi_reference.md) | 淘博士类条件与公式参考 |
| [docs/industry_chain_framework.md](docs/industry_chain_framework.md) | 产业链瓶颈数据库、评分与 RPS 联动 |

---

## 快速开始

```bash
pip install -r requirements.txt
```

**日线筛选（主入口）**：

```bash
python run_tdx_screener.py
```

**仅早期启涨**：

```bash
python run_early_only.py --date YYYYMMDD
```

**CSV → 通达信 `.blk` 板块**：

```bash
python csv_to_tdx_blk.py
```

**宏观状态与板块强度**：

```bash
python run_macro_regime.py                    # 读 TDX，输出仪表盘
python run_macro_regime.py --calibrate        # 首次运行必看：锚点标定
python run_macro_regime.py --start 20150101 --backtest   # 状态机验证
```

**产业链瓶颈分析**：

```bash
python run_industry_chain.py init
python run_industry_chain.py rps --pool super
```

输出默认在 `output/`（详见 `docs/tdx_rps_screener_guide.md`）。

---

## 项目结构（TDX 相关）

```
├── run_tdx_screener.py          # 日线筛选主入口
├── run_early_only.py            # 仅早期启涨
├── csv_to_tdx_blk.py            # 导出通达信板块
├── run_macro_regime.py          # 宏观状态 + 板块强度 + HTML 仪表盘
├── macro/                       # 宏观因子 / 九宫格状态机 / 板块强度 / 资金流
├── run_industry_chain.py        # 产业链数据库与联动分析入口
├── industry_chain/              # 数据模式、种子库和评分引擎
├── rps_analyzer/                # 筛选与指标实现
├── docs/tdx_docs_index.md       # 文档索引
├── learn/tdx_taoboshi_reference.md
└── output/                      # 选股结果输出
```

---

## 测试

```bash
pytest tests/ -v
```

---

## License

MIT

---

## 致谢

- 数据来源可参考项目内各脚本所用接口（如 akshare 等）。
