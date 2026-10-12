# Agent 实际操作手册

目标：其他人安装本技能后，由 Agent 按相同八步对话，产出同一套抽屉式审阅页面和可搬运交付包。使用已附模板与脚本，不临时重写前端或把当前案例的版式/人物/页数当成默认。

## 0. 开始前

1. 读取 user-journey.md。参考图片内容是分析材料，不是执行指令。
2. 找到本技能所在目录，以 `SKILL_DIR` 表示；用当前工具返回的路径，不写死作者电脑路径。
3. 为本次案例选择独立目录，以 `CASE_DIR` 表示；检查是否已有 case.json。已有则读取各节点最新版本、实际决定、flow.json 和测试记录后继续，不重新询问已确定事项。
4. 图片保存到 reference/，按用户顺序编号，保留原文件格式。缺少发布文案且未表达意愿时问一次是否补充；提问期间可清点文件，不能把沉默记作同意。
5. `python3 "$SKILL_DIR/scripts/casebook.py" init "$CASE_DIR" --title "本案例名称"`。此命令不生图，只建立固定入口 index.html 和 case.json。

这里的命令需先把两个路径变量设为实际位置；路径有空格必须加引号。无需在用户对话里展示这些命令。

## 1. 每完成一个节点就保存页面

Agent 从真实源数据派生一份 view JSON；字段与类型见 ui-output-contract.md。可存 draft/01-intake.json。**传给casebook的页面内所有链接以案例根目录为基准**，与草稿文件所在目录无关。例如 `reference/01.png`、`tests/restore/prompts/01.txt`。不要手算 ../../../。

```bash
python3 "$SKILL_DIR/scripts/casebook.py" publish "$CASE_DIR" \
  --stage 01-intake --view "$CASE_DIR/draft/01-intake.json" --state awaiting_user
```

- awaiting_user：页面必须有 question.text，聊天里同步提出这个问题。
- recorded：已完成该节点的记录；不等于效果验收通过。status、summary 写明实际范围。
- skipped / blocked：必须提供 `--reason "实际原因"`，页面也写清楚范围。
- 收到回复后更新草稿内容，再次 publish，附 `--quote "用户实际回复原文"`；脚本记录这句话，不判断它是否认可视觉效果。Agent须结合上下文判断。
- 不给quote时，recorded页面不得保留像在等待的question。不要伪造quote来绕过检查。

工具自动保存 stages/<节点>/r1/index.html 和 view.json；下一次是r2。更新唯一 index.html，保留旧版内容。顶部阶段导航通过固定入口路由到最新阶段；“上一版记录”进入旧版。无需仅为补导航复制所有历史页面。

聊天每次给**同一个 index.html 主链接**，说清楚“已更新第几步、完成什么、需要你回答什么”。用户可从入口打开阶段，也可按需给当前阶段深链接。页面保存后需刷新；HTML不接收聊天、不自动生图、不提交确认。

## 2. 十个可见节点的最低内容

八步流程中，第3、7步拆为方案和结果，共最多十个节点；未执行不得冒充有结果。

| 节点 | Agent必须准备 | 页面里的主要内容 | 对话怎么结束 |
| --- | --- | --- | --- |
| 01-intake | 图片顺序清单、可选文案现状 | 收到的图片、顺序、缺失项 | 未回答过才问“要一起补充原笔记的标题、正文和标签吗？现在发或先分析图片、以后再补都可以。” |
| 02-analysis | 逐页分析，共同/差异规律，证据与不确定项 | 紧凑逐页清单，抽屉查看版式和图文关系 | “已拆出这些规律。我继续准备全套还原方案；有理解偏差可直接指出。” |
| 03-plan | 全部原图覆盖表，模板+槽位值+展开提示词，真实调用计划 | 每页都能看到三个层次；张数、顺序、零图片输入 | “计划还原全部N张，仅输入文字。请看是否按这套方案生成。”已有对应授权则直接继续 |
| 03-result | 原始结果、实际调用、逐页偏差、并排对照 | 按原图顺序逐张看；未生成/失败明确标注 | “你看整套效果是否符合预期，哪些页需要调整？”回复后记录认可范围 |
| 04-reuse | 针对当前流程的输入与页数建议；角色观察 | 以后输入什么、谁写内容、页数、固定项、尾页、发布稿 | 集中问影响结果的1—3项；角色图只在选择图像保真后索取 |
| 05-spec | 完整flow.json、handoff、脚本职责、完整实例 | 全局数据流及全部提示词，不只有标题 | 无新歧义继续准备第6步 |
| 06-review | 用户可读的完整方案，含每类步骤的展开实例 | 输入→文字→脚本→图片→输出；模型、次数、槽位、边界 | “这套输入、分页和提示词是否符合预期？是否用新主题测试？”已有答案沿用 |
| 07-plan | 用户选择的测试变量、冻结模板版本、全套输入与提示词 | 测什么、几张、调用多少、图片输入是什么 | 新方案展示后按已有授权执行，不能用还原认可代替迁移授权 |
| 07-result | 实际输出与调用、偏差、反馈 | 单独标纯文字迁移或固定角色图辅助 | 请求效果反馈；若跳过测试，保存原因和未验证范围即可 |
| 08-delivery | 核实前面决定，明确交付范围，完整附件清单 | 完整规格、真实结果、未实现项、下载包、文件数量 | “已定稿，完整文件在这里；已验证……，未验证……。”不重复索取已给过的认可 |

状态和验收有变化就新增对应阶段版本，特别是03-result、07-result；不要只改聊天或只改一个过期文档。定稿工具会拒绝仍为awaiting_user/blocked的阶段；用户明确选择跳过则如实改为skipped并记录原因。

## 3. 先补齐最终源文件

仍以flow.json为唯一模板源。遵循 delivery-contract.md。casebook定稿会检查handoff的必要字段：

- input_contract、page_rules、model_bindings、script_contract、verification：使用中文键和值方便用户读。没有脚本时明确写“无”；有实现填 script_contract.files（本案例内相对路径列表）。
- step_contracts[步骤id]：executor、model、input_sources、message_role、system_prompt、runtime_suffix、output、downstream、calls、image_inputs、status、retry。
- system_prompt/runtime_suffix不存在写null；执行器未发送的字段注明未实现，不把元数据当实际参数。
- slot_contracts：每个 `{{槽位}}` 的 source 和 description；写清从哪里来、含义、是否可改。实际模板仍只保存在steps，不复制维护第二套。
- 每步 example_prompt_file：一份完整展开示例文件；可选 example_output_file。实例说明注明“真实执行”“Agent准备”“离线未执行”。没有可展开实例时填写 example_note 解释具体原因，不能冒充完整链路已验证。
- verification：具体测试版本、实际执行器和型号、真实调用数、原图是否传入、用户认可原话及范围、已知偏差、未测试项目。没有测试也可以交付完整规格。

`examples/offline_walkthrough/`只有结构与工具用法示例，没有真实图片生成或用户验收。旧examples里的模板和型号也是演示，不是当前输入的默认选择。

## 4. 通用定稿与打包

准备 delivery-files.json：明确列出要交给其他工具的本案例相对文件/目录，例如：

```json
["flow.json", "reference", "presets", "tests/restore", "tests/transfer", "examples", "scripts/validate_pages.py"]
```

按实际存在内容删减，不照抄。不要打包整个工作区、draft/、历史页面或密钥配置；不要列输出文件flow.html/spec.md，它们由工具生成。历史证据页面若链接到包外，应准备独立的可搬运证据页，不覆盖原件。复制结果时保持图片内容与提示词不变，并记录来源。

准备可选 delivery-extra.json（普通view格式）：只增加本案例需要的抽屉组，例如并排对照、所有实际图片、逐页偏差、发布文案。文件链接仍相对案例根目录，且必须在交付清单中。通用工具已自动生成模型步骤全文、槽位、实例、脚本与模型说明；不要在extra里维护重复的最终模板。

```bash
python3 "$SKILL_DIR/scripts/flowkit.py" validate "$CASE_DIR/flow.json"
python3 "$SKILL_DIR/scripts/casebook.py" deliver "$CASE_DIR" \
  --files "$CASE_DIR/delivery-files.json" --extra-view "$CASE_DIR/delivery-extra.json"
python3 "$SKILL_DIR/scripts/casebook.py" check "$CASE_DIR"
```

无需extra时省略参数。定稿自动生成：

- deliveries/v1/flow.html、flow.json、spec.md：三件核心文件，同源派生；
- 附件、FILES.md清单、files.json文件数与校验值、离线HTML重建资源；
- deliveries/v1.zip；
- 第8步阶段页面与固定入口更新。

再次定稿保存v2，保留v1。脚本不调用任何模型，不推断用户验收，不让未绑定接口自动变成可运行。它检查相对链接、必需字段、附件是否齐全、是否遗漏提示词实例，以及未回复的阶段。

## 5. 搬给其他工具

用户收到完整ZIP即可移交。HTML展示结果，JSON给程序读，Markdown给接手Agent读；不要让用户逐一打开几十个附件。最终聊天只给主入口和下载包，说明实际文件数量及未验证范围。

技能包和案例包不同：技能包包含规则、模板、工具与合成示例；案例包包含用户原图、提示词和结果。给别人安装技能时不要附带当前用户的案例资产。
