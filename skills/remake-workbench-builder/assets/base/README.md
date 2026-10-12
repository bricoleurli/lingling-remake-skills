# 本地多玩法工作台

Python 3.10+，标准库运行。Mac双击启动工作台.command，Windows运行启动工作台.bat；也可执行python3 server.py。默认仅监听127.0.0.1:8766。

四部分：生成工作台、玩法库、生成历史、设置。日常生成无需Agent；首次搭建、添加玩法与修改固定规则由Agent完成。

已实现两类执行契约：固定角色知识卡（2次文字＋6—14次带参考图生图）；青绿主题图解（1次文字＋6次纯文字生图）。其他flow必须先实现适配器，不是任意JSON通用引擎。

模型连接在设置填写。密钥保存于用户本机data/settings.json，不随作品导出。供应商API与Windows运行环境须单独验证。未配置模型时可浏览玩法，不能声称已生成新作品。

Agent添加：python3 manage.py <交付目录>；读取flow.json与可选workbench.json，新版本保留旧版本。DESIGN.md为界面约定。测试：python3 -m unittest discover -s tests -v。
