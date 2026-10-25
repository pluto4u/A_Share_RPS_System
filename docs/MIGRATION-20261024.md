# 工作树迁入主目录（2026-10-24）

- 来源：`C:/Users/pluto4u/.codex/worktrees/b24a/a_-share_-rps_-system`
- 正式工作目录：`F:/88888.投资/08.选股/a_-share_-rps_-system`
- 原始工作树Git检查点：`e47c57aebcd7fb846cc1e6f93ec6aae2af8ccb89`
- 检查点分支：`codex/archive-breadth-migration`
- 合并后Git检查点：`eeea97ff83079e04462b19749b24313f2d046401`，分支 `codex/migrated-workspace-20261024`
- 完整迁移备份：同级目录 `a_-share_-rps_-system_migration_20261024/`

备份中的 `incoming/` 保存工作树文件（含本地配置和所有图片、文案、SQLite索引）；
`target_before/` 保存迁移前目标目录中同名文件；`migration-plan.json` 保存源文件SHA256及合并清单。
`.git`、Python缓存和虚拟环境未复制，目标主仓库继续使用原Git目录。

本次采用共同祖先的三方合并。保留目标目录较新的Kelly相关函数、研究模块和原TDX选股入口；
对README、忽略规则、导入语句及Windows控制台处理进行了冲突处理。
原工作树README另存 `docs/WORKTREE-README-20261024.md`。

迁入的档案和出图工作流使用 `outputs/`；原主目录既有策略继续按原契约使用 `output/`。
本地组件配置中的signals路径改为 `.`，默认TDX数据源仍为 `C:/new_tdx`。

验证：迁入功能相关74项测试通过；统一组件检查三个组件READY；在目标目录重新生成市场宽度图片。
此次测试不代表主仓库所有研究脚本的全量验收。原有其他文件及其未提交修改未覆盖。

旧工作树清理状态：尝试使用Codex的工作树归档工具，返回
`The task's primary checkout cannot be removed.`，因此旧工作树暂时保留。
正式文件和输出已经迁入上述主目录；本聊天仍绑定旧工作树，后续应在主目录打开本地聊天继续工作。

Git远端目前是Gitee。已连接GitHub账户，但连接器查询 `pluto4u/a_-share_-rps_-system`
返回404，当前也没有GitHub CLI；本次未创建GitHub仓库或上传源码。
