# 生成产物目录

所有程序输出统一进入这里，和功能代码、输入数据明确分离：

- `reports/`：回测与研究报告
- `exports/`：面向人的 Excel/CSV 导出
- `signals/`：每日信号、情绪历史和任务输出
- `logs/`：日志
- `workspace/`：三工程盘点与联调产物

目录内容默认不进入 Git。可用 `A_SHARE_OUTPUT_DIR` 将整棵输出目录迁移到其他磁盘。

