# flow.json 格式（规格定义 v0.4）

一个玩法写成一个 `flow.json`。字段含义见 `spec-definition.md`，这里只写格式。完整例子见 `examples/`。

## 顶层

```json
{
  "spec_version": "0.4",
  "id": "ip_comic_baomihua",            // 玩法 ID：流程类型_具体名，小写字母数字下划线
  "name": "爆米花团团 IP 漫画",
  "flow_type": "ip_comic",              // knowledge_card / ip_comic / meme / meme_grid / story_ip / avatar_pack / ip_asset / 自定义
  "summary": "一两句话：用户输入什么，得到什么",
  "source_note": "参考了哪篇小红书笔记（可选，一句话）",
  "reference": { ... },                 // 参考案例：复刻的目标，见下
  "models": {"image": "gpt-image-2", "text": "可选，不写则由 flowkit.env 决定"},
  "inputs": { ... },
  "presets": [ ... ],
  "steps": [ ... ],
  "outputs": [ ... ],
  "sample_input": { ... },              // 还原测试输入：尽量用原作自己的内容
  "transfer_input": { ... }             // 迁移测试输入：主题完全不同的内容
}
```

## reference

```json
"reference": {
  "note": "小红书 @某某 《打工人必备的 8 个效率神器》",
  "images": [
    {"file": "reference/01.png", "note": "封面：米黄旧纸，手写大标题居中，四周散落小插画"},
    {"file": "reference/02.png", "note": "正文页：上方小标题，下方一段解释，配一幅对应物件的小插画"}
  ],
  "title": "原标题",
  "body": "原正文，多段用 \n 分隔",
  "tags": ["效率工具", "打工人"]
}
```

- `images` 按原笔记顺序排列；`file` 相对于 flow.json 所在目录，建议放在 `reference/` 下。
- `reference` 不参与运行，只用于 spec.md 展示、`result.html` 对照和 validate 提醒（D13）。

## inputs

```json
"inputs": {
  "texts": [
    {"id": "lines", "name": "漫画台词", "rows": [2, 22], "multiple_of": 2,
     "required": true, "max_chars": 30, "verbatim": true,
     "placeholder": "第一行\n第二行"}
  ],
  "image": {"enabled": true, "required": false, "name": "参考照片", "hint": "上传说明"}
}
```

- `rows` 必填，写成 `[最少, 最多]`；固定行数写 `[16, 16]`。
- `default`（可选）：**建网页时预填进输入框**的内容，多行用 `\n` 分隔。它**不参与运行**——运行时输入仍然来自 `sample_input`、`--input` 或用户在网页上填的内容。
  - 用途：像“主角形象”这种长段固定文字，用户每次都要贴一遍很麻烦，写上 `default` 让网页预填，用户改几个字就能用。
  - 不要拿它当运行输入；也不要和 `placeholder` 混用。`placeholder` 是**输入框里的灰色提示语**（不进入任何数据），`default` 是**真正预填进输入框的值**。
  - 校验：`default` 的行数必须在自己的 `rows` 范围内，单行也不能超过 `max_chars`。
- 不用图片时写 `"image": {"enabled": false}`。用户图片的变量 ID 固定为 `user_image`。

## presets

```json
"presets": [{"id": "preset_1", "file": "presets/ip.png", "purpose": "固定主角参考图"}]
```

ID 只能是 `preset_1`～`preset_3`；`file` 相对于 flow.json 所在目录。

## subject（可选，有固定主角时填）

```json
"subject": {
  "fixed": true,
  "type": "cartoon",
  "preserve": ["脸型", "五官画法", "发型/发饰", "配色", "服装", "标志物", "身体比例"]
}
```

- `fixed`：现有脚本中表示图片固定主角。观察到角色后，在第4步确认当前这套流程的角色方式；只复用明确适用于本流程的已有选择。纯文字还原的 hero 描述是测试输入，不代表长期选择；纯文字角色不填 subject.fixed。
- `type`：主角类型，可选值：`cartoon`（卡通角色）、`human`（真人）、`animal`（动物）、`plant`（植物）、`object`（产品/物件）。
- `preserve`：保留清单，列出需要固定的外形特征**类别**。只允许特征类别词，出现具体名词即违规：
  - ❌ `["物种", "毛色和花纹", "标志部位（耳朵、尾巴）", "配饰（护目镜、红围巾、绿背包）"]`
  - ✅ `["物种", "毛色和花纹", "体型", "标志部位", "配饰"]`

  主角的身份和外形通过预置图传递，不通过文字。提示词正文里也不允许出现物种名或身份词（狐狸、猫、小女孩……）。

validate 的 D17 会检查：有 `subject.fixed` 时，是否写了 `preserve`、是否有预置图；每个传入预置图的生图步骤里，是否有“保持【图N】的……”并写到了 `preserve` 里的每一项，是否有“……按本次内容重新设计，不要照搬【图N】”。此外还有两条机械扫描：`preserve` 里命中具体名词、提示词正文里出现物种名或身份词，都会报警。spec.md 第 4 节会列出主角类型和保留清单。

## steps

三种步骤共用的字段：

| 字段 | 说明 |
|---|---|
| `id` / `name` / `type` | 必填。type 为 `text` / `image` / `code` |
| `consume` | `once`（默认）/ `per_item` / `grouped(k)` / `whole_list` |
| `over` | consume 不是 once 时必填 |
| `when` | 条件，如 `has(user_image)`、`count(lines) < 16`、`A and B` |
| `confirm` | `true` 表示完成后暂停，等人工确认 |
| `output` | `{"id": "变量ID", ...}`，见下 |

### text 步骤

```json
{
  "id": "txt_points", "name": "生成正文要点", "type": "text",
  "consume": "once", "confirm": true,
  "prompt": ["第一行提示词", "标题：{{title}}", "……"],
  "output": {"id": "points", "type": "list", "count": [6, 10]}
}
```

- `prompt` 可以是字符串，也可以是字符串数组（每项一行，推荐）；很长时用 `"prompt_file": "prompts/xxx.txt"`。
- `output.type`：`text` / `list` / `json` / `json_list`。
- `list` 和 `json_list` 必须写 `count`：整数、`[最少, 最多]`，或表达式 `"=16-count(lines)"`。
- `json` 和 `json_list` 必须写 `fields`，如 `["title", "body", "tags"]`，可以写 `"cover.title"` 表示多级。
- 可选：`multiple_of`（数量必须是几的倍数）、`verbatim`（结果进图不得改写）、`strip_numbering: false`（不去行首编号）。
- 循环（per_item / grouped）时，每轮输出一个 `type` 的值，收集成列表。

### image 步骤

```json
{
  "id": "img_pages", "name": "生成正文页", "type": "image",
  "role": "detail", "consume": "per_item", "over": "points",
  "ref_mode": "fixed", "image_size": "1024x1536",
  "prompt": ["{{img:cover}}", "【图1】是封面……", "本页内容：「{{item}}」"],
  "checks": ["文字逐字准确", "与封面风格一致"],
  "output": {"id": "pages"}
}
```

- `role`、`image_size` 必填。gpt-image-2 **只接受这三个尺寸**：`1024x1024`（1:1）、`1024x1536`（2:3 竖）、`1536x1024`（3:2 横）。`image_size` 会原样发给接口，写别的尺寸会被接口拒绝；validate 的 D7 会对表外尺寸提醒。
  原作比例不是这三个之一时，选最接近的那个，并在 `summary` 或 `design` 里记一句实际比例与原作的差异。
- `ref_mode`：`fixed` / `anchor(n)` / `chain`，只在循环里引用本循环生成的图时需要。
- `design`：提示词设计说明，四层写法见 `image-prompt-guide.md`。缺 `style` / `relation` / `varies` 时 validate 提醒（D14）。
- 固定主角写在顶层 `subject`，不写在步骤里。

```json
"design": {
  "style": "复古手绘图鉴风：米黄做旧纸、墨线、赭石/墨绿/暗红、手写体",
  "relation": "每页只画 1 个插画，画的是要点里提到的那个具体东西；文字在上、插画在下，插画约占画面 40%",
  "varies": "{{item}} 的要点文字；插画内容由要点决定",
  "avoid": "不要画原作里的咖啡杯、笔记本等具体物件",
  "origin": "AI 反推"
}
```

### code 步骤

```json
{"id": "op_merge_lines", "name": "合并贴纸文字", "type": "code",
 "op": {"name": "merge", "lists": ["lines", "extra_lines"]},
 "output": {"id": "all_lines", "type": "list", "count": 16}}
```

| op.name | 参数 |
|---|---|
| `merge` | `lists`: [变量ID…] |
| `pick` | `path`: `"plan.pages"` |
| `slice` | `list`, `start`, `end` |
| `split_lines` | `text` |
| `compose` | `images`: [变量表达式…]，`layout`: `row` / `column` / `grid:2x2`，`gap`，`background`，`size`: [宽, 高]；需写 `role` |
| `overlay_text` | `image`，`text`（可含槽位），`position`，`font`，`font_size`，`color`，`margin`；需写 `role` |

## outputs

```json
"outputs": [
  {"var": "cover", "name": "封面"},
  {"var": "pages", "name": "正文页"},
  {"var": "copy.title", "name": "标题"},
  {"var": "copy.body", "name": "正文"},
  {"var": "copy.tags", "name": "标签"}
]
```

按展示顺序排列。图片按顺序编号为 `00_封面.png`、`01_正文页01.png`……

## sample_input

```json
"sample_input": {"lines": "第一行\n第二行", "user_image": "sample/me.jpg"}
```

文字写成一个字符串，用 `\n` 分行；`user_image` 写图片路径（相对 flow.json）。也可以用 `--input 文件.json` 传入同样格式的输入。

`transfer_input` 格式与 `sample_input` 相同，用 `plan --transfer` / `run --transfer` 使用。

## 完整交付元数据 handoff（可选）

为统一生成 flow.html 与完整 spec.md，可在顶层保存 handoff；其结构与能力边界见 [完整交付契约](delivery-contract.md)。现有 flowkit 不执行 handoff 中的系统消息、后缀或待实现脚本，它们仅供 Agent 生成文档、下游实现。模型配置 models 不代表内置工具实测型号。reference 的标题、正文、标签可缺失，不阻塞图片分析。
