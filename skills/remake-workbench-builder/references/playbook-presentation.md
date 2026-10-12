# 玩法展示契约

在拆解交付包旁添加workbench.json，供第二技能使用；不要修改第一技能原flow。无需执行任何导入脚本。新安装会将workbench.json与flow、素材一同复制、哈希并保存到版本目录；旧任务保持旧版本。

字段均为普通文本，不支持HTML、JS或外部脚本：
- result：产物与数量。
- asset_title、asset_note、asset_summary：输入旁固定设定标题、说明与详情概览。
- intro：流程总说明。
- calls：正常模型调用数量。
- reference_note：图片输入说明。
- confirmation：生成前确认区说明。
- stages：顺序列表，每项含title、actor、description、prompt_step。prompt_step为flow里的真实步骤ID，非模型步骤填null。

参照examples/两个workbench.json。presentation.py检查字段、长度及引用，页面统一转义文本。新玩法不应通过更改页面代码来写专属说明。新增执行类型仍需新增受测试适配器；展示文件不赋予程序执行未知flow的能力。

兼容：无workbench.json的早期已登记玩法读取ui-profiles默认说明，不修改旧flow与历史；重新发行玩法时随交付提供独立workbench.json。修改独立展示文件也会形成新版本。

固定变量和流程语义以flow与适配器为准，Agent要对照展示说明，避免声明1次调用却执行2次。公开示例不要附带真实密钥、个人历史、开发路径或未经同意的素材。
