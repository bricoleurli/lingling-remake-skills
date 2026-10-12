# 更新契约

唯一在线来源 https://github.com/bricoleurli/lingling-remake-skills 的 stable 分支。无需 GitHub 账号或 Git。先解析 commit，再下载不可变提交 ZIP，校验 suite-release.json 的所有技能文件。哈希检测损坏、不完整内容；不是独立签名，信任官方仓库及 HTTPS。

只管理 remake-studio、flow-spec-builder、remake-workbench-builder、remake-suite-updater。工作台数据、密钥、其他技能不在范围内。首次接管旧版本无基线时须确认替换；后续按已安装哈希识别修改。忽略 __pycache__、.pyc、.DS_Store 缓存。

备份、锁、state.json 位于技能目录同级 `.remake-suite-<目录标识>/`，不被发现为技能。snapshot.json 保存旧目录哈希和旧状态。应用前完整校验、备份并暂存四个目录；普通异常恢复已替换目录。断电和强杀不保证自动恢复，Agent 需确认进程并检查备份快照后恢复。备份不自动清理。回滚拒绝覆盖更新后的本地修改，校验备份后才替换。

Windows/macOS 共用 Python 3.10+ 标准库程序；Windows 实机和其他 Agent 发现过程仍待测试。符号链接安装需先确认真实路径。技能更新只更新未来搭建使用的底座，不升级已有工作台。

维护者修改后执行 `python3 scripts/build_release_manifest.py`、受影响测试，提交 main，通过后推进 stable。只发布经过验证的提交。记录见根目录 CHANGELOG.md。
