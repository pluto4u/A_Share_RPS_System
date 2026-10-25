# 主目录整理前后的测试基线

整理前先进行全量测试，确认不能把原有失败归因于目录移动。

整理前：439 passed、5 failed、1 error（另一个测试模块因 `tests` 包冲突无法收集）。

原有5项失败：

- `tests/test_alt_data_sources.py::test_get_stock_list_prefers_non_eastmoney_source`
- `tests/test_alt_data_sources.py::test_fetch_single_stock_uses_tx_hist_shape`
- `tests/test_alt_data_sources.py::test_etf_fetch_prefers_sina_hist`
- `tests/test_integrated_decision_qfq.py::test_hithink_qfq_loader_matches_frozen_rps_snapshot`
- `tests/test_update_pipeline_flow.py::test_today_action_update_aborts_when_warehouse_update_keeps_failing`

前三项是测试期待的源优先级与现有实现不一致；第四项为本机冻结RPS快照与加载证券池的匹配
数量5194低于测试要求5200；第五项是行情更新失败后退出行为与预期不一致。
它们没有在本次目录清理中修改数据源、策略行为或测试断言来绕过。

本次修复了两个测试组织问题：增加 `tests/__init__.py` 以避免解析到第三方同名包；
明确把需要真实文件参数的手动CLI辅助函数 `test_with_real_data` 排除于pytest自动收集。

整理后，排除上述5项已知失败，442项通过，无新增失败：

```powershell
python -m pytest -q --deselect=tests/test_alt_data_sources.py --deselect=tests/test_integrated_decision_qfq.py::test_hithink_qfq_loader_matches_frozen_rps_snapshot --deselect=tests/test_update_pipeline_flow.py::test_today_action_update_aborts_when_warehouse_update_keeps_failing
```

此外，29个实验脚本的AST与清理前逐项比较，除包导入路径调整外相同；生产脚本
`monitor_daily`、`weekly_pullback_scan` 的新包导入通过；统一启动器成功转发 `run_tdx_screener --help`。

不宣称全量测试全绿。全量运行仍会遇到上述5项原有失败；没有删除或改写这些测试。
