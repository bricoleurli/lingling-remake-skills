# 交给 Agent 的安装和更新入口

官方仓库：https://github.com/bricoleurli/lingling-remake-skills

请在你自己的 Agent 中发送：

> 请阅读 https://github.com/bricoleurli/lingling-remake-skills/blob/stable/INSTALL.md ，按说明安装或更新灵凌同学的爆款复刻技能套件，保留我的其他技能、工作台、玩法、作品和模型配置。

## Agent 执行说明

1. 确认本机 Python 3.10+ 和当前客户端实际技能目录。不得将 Codex 的路径直接套给其他客户端；不得修改托管插件缓存。不能识别位置时查客户端资料或询问实际安装位置。
2. 读取官方 stable 分支的 `skills/remake-suite-updater/SKILL.md` 和 `scripts/update_suite.py`，审阅后将脚本保存到技能目录外的临时文件。不使用 `curl | sh`。GitHub 读取失败则报告问题，不下载第三方替代包。
3. 用 Python 运行脚本，参数 `--skills-dir <实际目录>` 先检查。无冲突且用户已授权安装/更新时加 `--apply`。旧安装存在差异时检查差异，用户确认后才加 `--accept-existing`；不能静默覆盖。
4. 脚本默认下载 stable 的确定提交并验证发布清单，安装四个技能。无需 Git 或 GitHub 登录。网络获取不执行下载包中的程序。
5. 检查结果，报告版本、备份路径、是否需刷新/新开会话。临时脚本在确认完成后删除。以后用户直接调用 remake-suite-updater。

此入口只更新技能，不迁移已经生成的本地工作台。Windows/macOS 的程序逻辑共用，客户端实际安装方式仍需核对。
