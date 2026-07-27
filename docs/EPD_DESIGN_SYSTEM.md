# 墨水屏 UI 设计标准(400×300 四色 BWRY)

> 适用设备:ZecTrix ESP32-S3 4.2" 四色墨水屏(400×300,黑/白/红/黄)
> 目的:统一所有页面的视觉语言,避免凭感觉调参,高效产出好看的页面。

---

## 1. 画布与栅格

| 参数 | 值 | 说明 |
|---|---|---|
| 物理分辨率 | 400×300 | 不可变 |
| **安全边距** | **左右 20px,上下 16px** | 内容不贴边,留呼吸感 |
| 内容区 | x∈[20,380], y∈[16,284] | 可用 360×268 |
| **基础栅格** | **8px** | 所有间距是 8 的倍数(8/16/24/32) |
| 圆角 | 卡片 8px,区块 6px,按钮 10px | 统一圆润感 |
| 圆角安全区 | 四角 15px 内不画(屏幕物理圆角) | 见 `kCornerSafeInset` |

**规则**:任何元素的 x/y/w/h 优先对齐到 8px 网格(或至少 4px)。

---

## 2.5 字体锯齿终极方案:PIL 1bit 渲染(已固化)

> **这是经过多轮实测定下来的方案**,解决了"截图方案文字有锯齿"的核心痛点。

### 问题根因
Chromium/cairosvg 等浏览器渲染文字时**强制开启抗锯齿(AA)**,产生灰色边缘像素。
这些灰边在四色墨水屏上无法显示,抖动后变成噪点/硬阶梯 = 你看到的"锯齿"。
CSS 的 `font-smooth: never` 在 headless Chromium 下**无效**,关不掉。

### 解决方案:PIL 1bit 模式
用 Python PIL 库直接渲染,文字画在 **`Image.new("1", ...)` 1bit 模式图层**上。
1bit 模式强制 PIL 用无 AA 的栅格化,产生**纯黑白像素,零灰边**。

```python
# 文字层(1bit,无AA)
txt = Image.new("1", (400, 300), 1)  # 1=白
draw_text = ImageDraw.Draw(txt)
draw_text.text((x,y), "十三", font=f48, fill=0)  # 0=黑

# 色块层(RGB,黄底/红框)
rgb = Image.new("RGB", (400, 300), (255,255,255))
draw_rgb = ImageDraw.Draw(rgb)
draw_rgb.rectangle([0,80,180,300], fill=(255,255,0))

# 合成:文字层黑像素 → RGB 上的纯黑
arr[mask] = [0, 0, 0]
```

### 三档字号(已固化,不用其它字号)
| 字号 | 用途 | 清晰度 |
|---|---|---|
| **48px** | 大字焦点(农历日) | ✅ 完美无锯齿 |
| **24px** | 标题/正文(宜忌标签+条目、日期) | ✅ 清晰可接受 |
| **16px** | 辅助小字(农历月、节气) | ⚠️ 凑合(物理极限) |

**铁律**:
- ❌ 不用 18/20/22px 中间字号(视觉层次混乱)
- ❌ 不用低于 16px 的字号(完全糊)
- ✅ 16px 只用于辅助信息(一行带过),关键信息用 24px+
- ✅ 大数字/焦点必须 48px

### 关键代码位置
- `tools/nas-service/board/pil_renderer.py` — PIL 渲染引擎(1bit 文字 + RGB 色块合成)
- `tools/nas-service/board/almanac.py` — 老黄历布局(三档字号 + 色块布局)
- `tools/nas-service/board/registry.py` — 看板注册表(每个 board 声明 render 函数)

### 多模板系统(已实现)
每个看板支持**多个预设模板**,用户在手机详情页(`/detail?board=almanac`)选择。

**架构**:
```
registry.py
  BoardSpec
    ├─ label: "老黄历"
    ├─ get_data: almanac.get_data      ← 数据源(所有模板共享)
    └─ templates: Dict[str, TemplateSpec]
        ├─ "classic": render_classic   ← 经典版(大数字焦点+宜忌卡片)
        └─ "minimal": render_minimal   ← 极简版(全屏黄底+居中大字)
```

**加新模板(3 步)**:
1. `board/<name>.py`:加 `render_<template>(data)` 函数(layout 闭包)
2. `board/registry.py`:在对应看板的 templates 字典加一行 `TemplateSpec`
3. `python update-nas.py` 同步 + 重启容器

**加新看板(3 步)**:
1. `board/<name>.py`:`get_data()` + 至少一个 `render_<template>()`
2. `board/registry.py`:加 `BoardSpec` 注册
3. `app.py` 的 `device_ping`:给设备对应页面加 `"type":"board","board_id":"<name>"`

**API**:
- `GET /api/board/list` — 返回所有看板及其模板列表
- `GET /api/board/preview?board=X&template=Y` — 生成预览 PNG
- `POST /api/board/render` body `{board, template, push, auto_switch}` — 渲染+推送
- `GET /detail?board=X` — 看板详情页(模板选择 UI)

### 为什么不直接用固件原生渲染?
固件 RawDraw 渲染也清晰(原理相同,都无 AA),但:
- 改 UI 要编译烧录(分钟级),PIL 改完 restart 容器即可(秒级)
- PIL 在 NAS Python 跑,布局用坐标计算,比 RawDraw C++ 灵活
- 固件 flash 有限(48px 字体就占 1.7MB)

**取舍**:看板页面用 PIL(NAS 截图推屏),离线页面用固件 RawDraw。

---



项目只有两档中文字体,**不要试图用 5 种字号**,在这两档里做层次:

| 角色 | 字体 | 实际 line_height | 字宽 | 用途 |
|---|---|---|---|---|
| **Title(标题/大数字)** | `SourceHanSansSC_Medium_slim` | **24px**(size=24) | 每汉字 16px | 页面主标题、大数字(百分比/温度) |
| **Body(正文/标签)** | `SourceHanSansSC_Regular_slim` | **16px**(size=16) | 每汉字 16px,ASCII 8px | 一切正文、标签、说明、列表项 |
| **Icon-16** | `font_zectrix_16_1` | 16px | - | 状态栏图标(WiFi/电量) |
| **Icon-48** | `font_zectrix_48_1` / `weather_icons_48` | 48px | - | 大图标(天气/状态) |

> **字宽实测(从 glyph_dsc 解析)**:汉字 adv_w=256→`(256+8)>>4=16px`;ASCII adv_w=128→8px。
> `MeasureTextWidth()` 返回的就是真实像素,可直接用于布局计算。

**规则**:
- 一个页面**最多出现 2 种字号**(Title + Body),不要混用更多
- 大数字(温度/百分比/进度)用 Title 字体,一眼聚焦
- 副标题/说明用 Body,颜色降级(secondary)
- **行高 = 字体 line_height + 4px**(舒适行距),不要贴行
- ⚠️ **InkyPi 大数字应占屏短边 32% = 96px,但我们 Title 只到 24px**(差 4 倍)。
>   固件无法渲染 96px 字体(字体文件只有 24px glyph)。
>   **补救**:大数字场景用"数字+单位分行" + 充分留白 + 占满宽度,模拟视觉冲击。

---

## 3. 四色语义(严格分工)

四色墨水屏只有 4 色,**每色必须有明确语义**,不能随便用:

| 颜色 | Token | 语义 | 用在哪 |
|---|---|---|---|
| ⬛ **黑** | `TextPrimary` | 正文/边框/图标 | 标题、正文、卡片边框、分隔线 |
| ⬜ **白** | `BackgroundPrimary` | 背景/留白 | 页面底色、卡片填充底 |
| 🟥 **红** | `Accent` / `Danger` | **强调/警示/当期** | 大数字、当月/今天、忌、警示、进度填充、选中态 |
| 🟨 **黄** | `SuccessLike` / `BackgroundSecondary` | **正面/已完成/装饰底** | 宜、已过月、已完成打卡、卡片强调底色 |

### 3.1 色板纯净度铁律(最重要!实测发现)

**墨水屏物理上只有 4 种墨水胶囊**。任何其它 RGB 颜色都**无法真实显示**,epaper-dithering 只能用这 4 色点阵去"模拟",结果就是噪点。

**在所有给墨水屏的内容里(HTML 模板 / 图片 / 任何 RGB 来源),颜色只能用这 4 个标准值:**

| 颜色 | 唯一允许的 RGB | 禁止的近似值(会抖动成噪点) |
|---|---|---|
| 黑 | `#000000` | 任何深灰(`#222`、`#333`) |
| 白 | `#ffffff` | 任何浅灰(`#f5f5f5`、`#eee`) |
| **红** | **`#ff0000`** | ❌ `#d92121`(掺黑点)、`#e74c3c` |
| **黄** | **`#ffff00`** | ❌ `#ffd400`(掺 25% 红点!)、`#ffcc00` |

**实测数据**(为什么禁止近似色):
```
#ffff00 纯黄  →  抖动 → 黄100%           (纯净 ✅)
#ffd400 偏橙  →  抖动 → 黄75% + 红25%    (花 ❌)
#ff0000 纯红  →  抖动 → 红100%           (纯净 ✅)
#d92121 暗红  →  抖动 → 红93% + 黑6%     (花 ❌)
#8a8a8a 灰色  →  抖动 → 黑白噪点         (灾难 ❌)
```

**根因**:epaper-dithering 的 BWRY 色板里,黄=`#ffff00`、红=`#ff0000`。偏橙的黄(含红分量)会被判定为"接近红",撒入红点模拟;暗红(含黑)会撒入黑点。**只有纯色才能映射到单一墨水**。

**延伸影响**:
- **NAS 看板模板**(board/templates/*.html):CSS 颜色变量必须用 4 标准值,不用任何灰/橙/中间色
- **照片抖动**:照片里的渐变色必然产生抖动噪点,这是物理限制,只能靠 gamut 参数整体调
- **图标/SVG**:只能用纯 BWRY 四色,不能用渐变/阴影/半透明
- **固件 RawDraw**:用 `Color` 枚举(BLACK/WHITE/RED/YELLOW),直接映射墨水索引,**跳过抖动**,天然纯净——这是 RawDraw 相比 HTML 截图的唯一色彩优势

**层级建立方式**(不能用灰色):
- ❌ 标签灰 `#8a8a8a` + 值黑 → 标签糊成噪点
- ✅ 标签黑 13px + 值黑 15px → 靠字号差异建立层级(InkyPi weather 的 lighter/bold 手法的墨水屏适配版)

**严禁**:
- ❌ 红黄混用不分语义(比如宜忌都用红)
- ❌ 黄色当正文(对比度太低,墨水屏上几乎看不清)
- ❌ 大面积红色(墨水屏红会很"刺眼",只用于强调)
- ❌ **任何非 #000/#fff/#ff0/#f00 的颜色**(会抖动成噪点)
- ✅ 黄色适合做**区块底色**(纯黄底 + 黑字,对比够且不刺眼)
- ✅ 红色适合做**边框/数字/标签**(小面积强调)

---

## 4. 间距与呼吸感(8px 网格)

**核心原则:宁可留白,不要拥挤。** 墨水屏分辨率低,拥挤 = 糊成一团。

| 间距 | 值 | 用途 |
|---|---|---|
| `kSpacingXXS` | 4px | 极小间隙(图标和文字间) |
| `kSpacingXS` | 8px | 同组元素内(标题和副标题) |
| `kSpacingSM` | 12px | 区块内行间距 |
| `kSpacingMD` | 16px | **区块间标准间距**(卡片之间) |
| `kSpacingLG` | 24px | 大段落分隔 |
| `kSpacingXL` | 32px | 顶部留白/页面标题区 |

**规则**:
- 区块之间用 `kSpacingMD`(16px)
- 同一区块内行用 `kSpacingXS`(8px)
- **绝对不要**把多行信息用 4px 间距堆叠(会糊)

---

## 5. 信息密度原则(关键)

**墨水屏不是手机屏,信息密度必须低。**

### 5.1 一页一个焦点
- 每个页面有**一个视觉焦点**(大数字/大图标/大标题)
- 其他信息围绕焦点做支撑,不要平铺

### 5.2 三区块上限
- 一个页面**最多 3 个信息区块**(Header / 焦点 / 辅助)
- 超过 3 个区块 = 拥挤,考虑拆页或砍信息

### 5.3 老黄历的教训(反面案例)
❌ 错误做法(我之前犯的):塞了 农历+公历+生肖+星座+冲煞+吉神×3+宜忌 = 8 个信息块 → 全挤一起
✅ 正确做法:**砍信息,留核心**:
  - 区块1:农历大字(焦点)+ 公历(副)
  - 区块2:宜(黄区块)
  - 区块3:忌(红区块)
  - 其他(生肖/星座/冲煞/吉神)**移到次级页面或彻底砍掉**

### 5.4 文字长度
- 单行文字不超过内容区宽度(360px),超出就换行或截断
- 横向排列的标签项,用**流式布局**(动态计算宽度),不写死间距

---

## 6. 布局模板(三个标准模板)

### 模板 A:大数字焦点型(LifeBar / YearProgress / 年度进度)
```
┌────────────────────────────┐
│  小标题(左上,Body,灰)    │
│                            │
│         ┌───────┐          │
│         │  58%  │          │  ← 焦点:大数字(Title,红)
│         └───────┘          │
│      ──────────(进度条)    │
│                            │
│   辅助信息(Body,居中)    │
└────────────────────────────┘
```

### 模板 B:列表/卡片型(News / Todo)
```
┌────────────────────────────┐
│  标题(Body,灰)            │
│  ──────────────────        │
│  ┌────────────────────┐    │
│  │ 卡片1(白底,黑字)  │    │
│  └────────────────────┘    │
│  ┌────────────────────┐    │
│  │ 卡片2              │    │
│  └────────────────────┘    │
└────────────────────────────┘
```

### 模板 C:左右分栏型(Weather / 老黄历)
```
┌────────────────────────────┐
│  标题区(Header)            │
├──────────────┬─────────────┤
│              │             │
│   左栏       │   右栏      │  ← 主信息 | 辅助
│  (大图标)    │ (数据列表)  │
│              │             │
└──────────────┴─────────────┘
```

---

## 7. 组件规范

### 卡片
- 圆角矩形(8px),白底,黑描边(1px)
- 内边距:上下 8px,左右 12px
- 卡片间距:16px
- **强调卡片**:用黄色底 或 红色描边(二选一,不要又红边又黄底)

### 分隔线
- 横线:1px 黑,从左 margin 到右 margin
- 用于区块分隔(Header 与内容之间)

### 进度条
- 高度 12px,圆角 6px(pill 形)
- 底色:黄(SuccessLike)→ 填充:红(Accent) ← **双色环/条的核心**
- 宽度:内容区全宽或 80%

### 状态指示(全屏页角落)
- 右上角:时间(WiFi 图标 + 电量%)
- 高度 14px,不占内容区

---

## 8. 反模式(不要做)

1. ❌ **信息堆砌**:一页塞 >5 个信息块 → 糊
2. ❌ **黄色当正文**:墨水屏黄字看不清 → 黄只做底色
3. ❌ **大面积红**:红底大区块很刺眼 → 红做边框/小标签
4. ❌ **间距 <8px**:行间距太小 → 糊成一团
5. ❌ **4 种以上字号**:没有这么多字体,不要硬造
6. ❌ **文字贴边**:不留安全边距 → 像 demo,不精致
7. ❌ **自画标题栏**:全局状态栏/角落指示已经够了,别再画一个

---

## 9. 验收清单(每个新页面上线前过一遍)

- [ ] 只有 1 个视觉焦点
- [ ] 最多 3 个信息区块
- [ ] 只用了 2 种字号(Title + Body)
- [ ] 间距都是 8 的倍数,最小 8px
- [ ] 红黄语义正确(红=强调/警示,黄=正面/底色)
- [ ] 文字不贴边(留 20px 左右安全边距)
- [ ] 大数字/标题用 Title 字体
- [ ] 没有文字溢出/重叠
- [ ] 全屏页有角落状态指示

---

## 参考资料

- [kimmo.blog – Building an e-ink weather display (Figma 设计流程)](https://kimmo.blog/posts/7-building-eink-weather-display-for-our-home/)
- [InkyPi – 开源四色墨水屏 dashboard](https://github.com/chiuy05/InkyPi)
- [A Dedicated E-Paper Design System (ACM 2026)](https://dl.acm.org/doi/full/10.1145/3772318.3791459)
- [Material Design Type System](https://m2.material.io/design/typography/the-type-system.html)
- [E-Ink Display 字体选择 (StackExchange)](https://graphicdesign.stackexchange.com/questions/11372/what-fonts-are-ideal-for-e-ink-displays)

---

## 附录:InkyPi 源码逐文件实测(2026-07 拉取验证,本地 `_research/InkyPi/`)

> **源码版本**:`fatihak/InkyPi` main 分支(本地副本 `D:/AI/youn-ink-fourcolor-firmware/_research/InkyPi/`)
> **渲染模型**:HTML/CSS + **Chromium headless** 整屏截图 → PNG → 推屏。CSS 的 `dvw/dvh` **直接 = 屏幕物理像素**(无浏览器 chrome 占用)。
> 所以它的相对单位照搬到我们 400×300 时,直接按百分比换算即可。

### A.1 全局公共地基(`base_plugin/plugin.css`)

| 规则 | InkyPi 值 | 400×300 换算 | 我们是否遵循 |
|---|---|---|---|
| 外圈 padding | `1.5vw` | **6px** | ✅ 我们用 20px(偏大,但安全) |
| 装饰边框厚度 | `0.7vw` | 3px | 可选 |
| 关闭抗锯齿 | `font-smooth: never` | - | ✅ RawDraw 位图天然满足 |
| flex 居中 | `.container { flex; center }` | - | ✅ 用 InkCenteredTextTopYInBox |
| 四种 frame | 矩形/上下条/角框/无 | - | 用 DrawRoundRectBorder 实现 |

### A.2 字号比例铁律(全项目共识,核心!)

InkyPi 几乎所有字号都写成 `min(X% × 屏宽, Y% × 屏高)`,取**短边方向**。对我们 400×300(短边 300):

| 角色 | InkyPi 公式 | 400×300 换算 | 我们当前 | 差距 |
|---|---|---|---|---|
| **大数字(焦点)** | `min(32dvh, 32dvw)` | **96px** | Title 24px | ⚠️ 小 4 倍! |
| 一级标题 | `min(11dvw, 11dvh)` | 33px | Title 24px | 偏小 |
| 二级标题/数据值 | `min(8dvh, 8dvw)` | 24px | Title 24px | ✅ 吻合 |
| 副标题/标签 | `min(5dvw, 5dvh)` | 15px | Body 16px | ✅ 吻合 |
| 正文/描述 | `min(4dvh, 3vw)` | 12px | Body 16px | 偏大(可接受) |

> **关键启示**:我们固件只有 24/16 两档字体,**做不出 96px 大数字**。
> InkyPi 用浏览器能渲染任意字号,我们受限于字体文件。
> **补救**:大数字场景(温度/百分比)用"大数字+单位分行"+ 充分留白,模拟视觉冲击。

### A.3 countdown(倒计时)— 单焦点大数字范例

源码:`plugins/countdown/render/countdown.css`
```css
.title       { font-size: min(11dvw, 11dvh); line-height: 1; }     /* 标题 = 大数字×0.34 */
.day_count   { font-size: min(32dvh, 32dvw); line-height: 1; }     /* 大数字:屏短边 32% */
.label       { letter-spacing: .1em; font-size: min(8dvh, 8dvw); } /* 标签:大写+字距 */
.countdown-wrapper { width: 90%; }                                  /* 内容区留 10% 边距 */
```
**启示**:
- 大数字 `line-height: 1`(紧贴,不浪费垂直空间)
- 标题/副标题/大数字/标签 **纯垂直 4 层堆叠**
- 标题 = 大数字 × 0.34,标签 = × 0.25(固定比例关系)
- 内容区只占 90% 宽(对应我们的 ~20px 边距)

### A.4 rss(列表)— 用细线分隔而非卡片框

源码:`plugins/rss/render/rss.css`
```css
.rss-item { border-top: 1px solid; padding: 2dvh 0; }          /* 顶部 1px 线分隔 */
.rss-item:nth-child(even) { flex-direction: row-reverse; }     /* 奇偶条目镜像 */
.item-title { font-weight: bold; padding-bottom: 1dvh; }
.item-image { max-width: 15%; }                                 /* 缩略图上限 15% */
```
**启示**:
- 列表项用 **1px 顶边线分隔**(比卡片框简洁省空间)
- 列表项垂直 padding = 屏高 2%(我们用 6px ≈ 2%)
- **奇偶镜像**(图文左右翻转)是廉价视觉节奏
- 缩略图宽度上限 = 屏宽 15% = 60px

### A.5 weather(复杂仪表盘)— 分区比例 + 四色策略

源码:`plugins/weather/render/weather.css`
```css
.weather-dashboard { flex column; gap: 1dvh; }   /* 4 个高度段 */
.header { height: 15dvh; }                        /* 头部 15% */
.chart-container { height: 24dvh; }               /* 图表 24% */
.current-temp { font-size: 45cqmin; }             /* 大温度 = 容器短边 45% */
.temperature-unit { font-size: 0.4em; }           /* ° 上标,主字号 0.4 倍 */
.data-points { grid: repeat(2, 1fr); gap: 0.5vh 1vw; }
.data-point-label { font-weight: lighter; }       /* 标签 lighter */
.data-point-measurement { font-weight: bold; }    /* 数值 bold(weight 对比代色) */
.forecast-day { border: 1px; border-radius: 1.2vw; }  /* 预报卡:1px 边+圆角 */
```
**四色策略(关键)**:
- 面板前景/背景 = 黑白单色对比
- **只在数据可视化用色**:温度线橙 `rgba(241,122,36)`、填充黄渐变、降水柱蓝
- 即:**信息层单色,装饰/数据层用色** ← 正是我们 宜(黄)/忌(红) 的做法 ✓

**启示**:
- 仪表盘分 4 段:头部 15% / 主区 45% / 图表 24% / 底部 15%
- 单位符号(°)做上标,字号 = 主字号 × 0.4
- 数据网格用 2 列,每格内左 25% 图标 + 右 75% 文字
- **weight 对比代替颜色**(bold 数值 vs lighter 标签)

### A.6 year_progress(进度条)— 点阵纹理表示"未填充"

源码:`plugins/year_progress/render/year_progress.css`
```css
.progress-title { font-size: min(20dvh, 16vw); }     /* 年份大数字 */
.progress-bar { height: 10dvh; border-radius: 5px; }
.progress-remaining {
  background-size: 5px 5px;
  background-image: radial-gradient(textColor 1px, transparent 1px);  /* 5×5 点阵 */
}
```
**启示**:
- 进度条高度 = 屏高 10% = 30px
- **未填充部分用 5×5px 点阵纹理**(不是留白,视觉更丰富)
- 大数字占屏短边 16~20%

### A.7 todo_list(卡片化列表)— 卡片标准样式

源码:`plugins/todo_list/render/todo_list.css`
```css
.list { border: 1.5px solid; border-radius: 10px; padding: 1rem; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }
li { border-top: 1px; padding: 2cqh 0; line-height: 1.2; }
.more-item { font-style: italic; }   /* "And X more..." 斜体提示 */
```
**启示**:
- 卡片 = 1.5px 边框 + 10px 圆角 + 1rem padding + 极淡阴影
- 超出项追加 "还有 X 项..." 斜体提示(我们 News 可借鉴)

### A.8 照搬到 ESP32 RawDraw 的 12 条通用规则

1. **字号一律相对**:`font-size = min(X% × 屏宽, Y% × 屏高)`,取短边方向
2. **大数字占屏短边 32%**,line-height = 1.0(我们受字体限制只能 24px,用留白弥补)
3. **标题 = 大数字 × 0.34**,标签 = × 0.25(固定比例)
4. **列表项垂直 padding = 屏高 2%**(6px),分隔靠 **1px 顶边线**
5. **卡片**:1~1.5px 边框 + 圆角 10px(或屏宽 × 1.2%)+ padding
6. **数据网格**:`repeat(N, 1fr)`,gap ≈ 0.5vh 1vw;每格左 25% 图标 + 右 75% 文字
7. **仪表盘分区**:头部 15% / 主区 45% / 图表 24% / 底部 15%
8. **四色策略**:面板黑白对比,**只在数据可视化用色**(橙=温度、蓝=降水、黄=填充)
9. **强调色稀少**:calendar 仅"今日"2px 边框、"当前时间"3px 线;数值 bold、标签 lighter
10. **奇偶交替/镜像**(rss row-reverse)是廉价视觉节奏
11. **点阵纹理**(year_progress 5×5px radial-gradient)表示"未填充"
12. **响应式靠 aspect-ratio**:竖屏折叠纵向、调网格列数

> 本地源码副本路径:`D:/AI/youn-ink-fourcolor-firmware/_research/InkyPi/src/plugins/`
> 需要查别的插件(clock/newspaper/ai_image/apod/comic)可直接读本地副本。
