# A 股 TDX / RPS 选股

基于本地通达信导出的日线数据，计算 RPS 与多层筛选（L1 / L2 / 火车轨 / 早期启涨），输出 CSV 并可导入通达信自定义板块。

---

## 文档索引

详见 **[docs/tdx_docs_index.md](docs/tdx_docs_index.md)**。核心文档：

| 文档 | 说明 |
|------|------|
| [docs/tdx_rps_screener_guide.md](docs/tdx_rps_screener_guide.md) | 主入口：依赖、命令、输出文件、`csv_to_tdx_blk` |
| [docs/tdx_rps_extension_zhutu.md](docs/tdx_rps_extension_zhutu.md) | RPS 扩展数据与主图公式 |
| [docs/strategy_early_launch.md](docs/strategy_early_launch.md) | 早期启涨策略 |
| [docs/strategy_hong_zhanfa.md](docs/strategy_hong_zhanfa.md) | 洪战法量化定义 |
| [learn/tdx_taoboshi_reference.md](learn/tdx_taoboshi_reference.md) | 淘博士类条件与公式参考 |

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

输出默认在 `output/`（详见 `docs/tdx_rps_screener_guide.md`）。

---

## 项目结构（TDX 相关）

```
├── run_tdx_screener.py          # 日线筛选主入口
├── run_early_only.py            # 仅早期启涨
├── csv_to_tdx_blk.py            # 导出通达信板块
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
