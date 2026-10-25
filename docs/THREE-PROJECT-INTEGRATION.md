# 三工程整理与整合边界

## 定位

| 工程 | 责任 | 保留的真相源 | 对外边界 |
|---|---|---|---|
| `a_-share_-rps_-system` | 情绪、RPS、风控、策略验证和定时任务 | `data/` 中的行情缓存 | 文件导出、飞书、任务入口 |
| `investment_research` | 研报摄入、证据核验、产业链瓶颈评分 | PostgreSQL | 只读 API / JSON 快照 |
| `tick-stock-panel` | 行情工作台、筛选、监控、回测和可视化 | 自身 DuckDB/Parquet | Open API / MCP / 前端扩展 |

## 结论

三个源码树不应直接合并。依赖栈、数据真相源和发布节奏不同，硬合并会让升级、测试和
数据权限互相污染。推荐把它们作为同一“投资研究工作台”的三个独立组件管理：

1. `tick-stock-panel` 负责统一交互界面与市场数据展示。
2. `investment_research` 只向面板提供带出处的只读研究快照；不得从面板写回研究库。
3. `a_-share_-rps_-system` 独立生成信号和报告，可由面板通过 Open API/外部页面读取，
   但不把研究管线变成自动买卖系统。

当前落地的是统一组件清单、状态检查和目录契约。下一阶段若接 UI，优先在面板的扩展
机制中增加两个只读页面，而不是复制 Python 包或共用数据库。

## 统一管理

本机路径记录在被 Git 忽略的 `config/workspace.local.json`。运行：

```powershell
python tools/workspace.py status
python tools/workspace.py inventory
```

`inventory` 的结果写入 `outputs/workspace/inventory.json`，不会混入源码目录。

