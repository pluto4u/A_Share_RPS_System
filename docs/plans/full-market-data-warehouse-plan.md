---
name: 全市场数据本地化与多基准对比
overview: 构建全A股日线数据本地仓库（parquet格式，支持增量更新），并新增创业板/中证1000多基准对比回测能力。
status: completed (5269只股票, 263.6万行, parquet 63.6MB + 4指数515天)
---

# 全A股数据本地化 + 多基准对比

## 数据本地仓库设计

### 存储结构

```
backtest/data/
├── stock_daily.parquet          # 全市场个股日线（约5000只，1.5-2GB）
├── index_daily.parquet          # 主要指数日线
├── ecology_cache.csv            # 情绪生态特征
├── meta.json                    # 元数据：最后更新日期、股票数、行数
└── stock_list_cache.csv         # 全市场股票列表缓存
```

### 为什么用 parquet
- 1250万行: CSV 约 8-10GB，parquet 约 1.5-2GB
- 读取速度: CSV 约30秒，parquet 约2秒

## 数据采集

- 首次全量: 多线程拉取约 6-7 分钟
- 增量更新: 每天 2-3 分钟

### CLI 入口

```bash
python -m emotion_validation.data_layer.data_warehouse --init      # 首次全量
python -m emotion_validation.data_layer.data_warehouse --update    # 增量更新
python -m emotion_validation.data_layer.data_warehouse --status    # 查看状态
```

## 多基准对比

- 沪深300（000300）- 已有
- 创业板指（399006）- 新增
- 中证1000（000852）- 新增

回测输出: 各基准年化/回撤/Sharpe、超额收益、情绪有效性对比
