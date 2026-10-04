# Lunar Base

> Web-based management console for a [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) private server — backup, restore, and edit the player database from a browser.
> 基于浏览器的 [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) 私服管理面板 —— 浏览器中备份、恢复、编辑玩家数据库。

**UI language / 界面语言:** every page has a top-right `中文 / EN` toggle (remembered per browser, `lb_lang`). 全站右上角可切换中/英文，选择会被记住。

---

## What changed vs. upstream / 相对原项目的更改内容

This fork adds the following on top of the upstream project. 本分支在原项目基础上新增/修改了以下内容：

**全站 / Global**
- **中文会话首屏不再闪英文**：`base.html` 在 `<head>` 内置预绘制脚本，检测到 `lb_lang=zh` 时先给 `<html>` 加 `i18n-loading` 隐藏 body 并设 `lang=zh-CN`，`i18n.js` 翻译完成后移除；另有 load / 1.5s 超时双重兜底，翻译失败也不会白屏。切换页面时直接呈现中文，不再先画英文再切。
- **所有执行/保存按钮防重复执行**：`i18n.js` 提供 `lbBusyMark / lbBusyClear / lbConfirm`，按钮请求期间标记 `data-busy="1"`；捕获阶段监听 click/submit/change 吞掉对 busy 元素的再次激活（双击、连按 Enter、复选框连点），确认弹窗也改为**先标记 busy 再弹窗**，杜绝弹窗期间重复触发；忙碌按钮变暗 + progress 光标，作为「探测/处理中」提示。各编辑器的保存、发放、强化、备份、恢复、删除等入口全部接入。
- **按钮尺寸/对齐修复（中英双语）**：按钮统一 `white-space: nowrap`，操作列改为 `max-content`（不再被固定宽度挤压导致换行、错位、重叠）；窄屏下收紧按钮内边距/字距，任务/事件/关卡行在手机上允许换行堆叠；确认弹窗按钮自适应宽度。
- **等级与经验挂钩**：修改等级/经验时按游戏经验曲线自动保持一致 —— 经验为准派生等级（超上限按封顶截断）；只改等级则自动补上该级门槛经验；前端只提交有变化的字段。等级/经验已从「账户信息」栏移入 **等级/经验计算栏**，独立成行并在行尾带保存按钮。
- **回到顶部按钮**：CSS 绘制箭头（不依赖字体字形，杜绝平台显示异常）。
- **顶部导航常驻**：终端栏 + 主导航整组 sticky 固定顶部（页面内其它 sticky 元素经 `--header-h` 自动让位）；主导航新增 **Profile 档案** 入口。
- **回到顶部按钮**：所有页面右下角固定「回到顶部」按钮，滚动超过 320px 出现，点击平滑回到顶部。
- **NieR: Automata 光标全套（取自参考图集 mouse.webp）**：14 个光标全部从图片**逐像素提取**（PNG 内联，无外部文件；配色统一为近白填充 + 深炭描边，浅色面板与深色终端栏都清晰），并按图集命名映射到标准 CSS 状态：

  | 图集原名 | 用途 | CSS 状态 |
  |---|---|---|
  | alt-select（飞机） | 默认指针 | `default` |
  | normal select（箭头） | 右键菜单指针 | `context-menu` |
  | normal select（箭头） | 链接/按钮 | `pointer`（原 link-select 手型已改为 normal-select 箭头） |
  | help-select（?） | 带提示元素 | `help`（`[title]` 自动生效） |
  | busy | 忙 | `wait`（`.cur-wait` / `body.cur-wait`） |
  | background | 后台处理中 | `progress`（操作中的按钮 `data-busy="1"` 自动生效） |
  | precision select（十字） | 精确定位 | `crosshair` / `cell` |
  | text-select（I 型） | 文本输入 | `text` / `vertical-text` |
  | move（四向） | 拖拽排序 | `move` / `all-scroll`（`[draggable]` 自动生效） |
  | unavailable（禁止） | 禁用/已通关 | `not-allowed` / `no-drop` |
  | horizontal resize | 水平调整 | `ew-resize` / `col-resize` |
  | vertical resize | 垂直调整 | `ns-resize` / `row-resize` |
  | diagonal resize ×2 | 对角调整 | `nwse-resize` / `nesw-resize` |

  另提供工具类 `.cur-default / .cur-context / .cur-pointer / .cur-help / .cur-wait / .cur-progress / .cur-cross / .cur-text / .cur-move / .cur-na / .cur-ew / .cur-ns / .cur-nesw / .cur-nwse`，以及 `data-resize="n|s|e|w|ne|nw|se|sw"` 属性，可对任意元素指定任一样式。

- **中英双语切换**：每个页面右上角 `中文 / EN` 按钮，所有文案（导航、按钮、提示、搜索占位、动态弹窗/横幅）双语，选择存于 localStorage。
- **网页内确认弹窗**：全部原生 `window.confirm()` 替换为统一的网页内模态框（遮罩 + 确定/取消，点击遮罩取消），任何页面都不再弹出浏览器原生对话框。
- **静态资源缓存版本号**：`i18n.js` / `automata.css` 自动附加 `?v=<mtime>`，避免浏览器缓存旧脚本导致按钮失效。
- **全站 Ajax 局部刷新**（无整页刷新）：任何操作/数据变化只刷新关联区域 ——
  - 关卡：完成/还原后原地更新行、锁定状态与全部计数；
  - 事件页：激活/应用后刷新 bin 列表与事件列表，bin 列表每 5 秒自动检测文件变化；
  - 存档页：创建备份/设置路径/恢复全部走 Ajax，列表与状态就地重渲染，每 5 秒自动检测外部新增的备份；
  - 物品/服装/武器：发放后就地刷新页签徽章、分组统计与搜索行统计（服装还会即时启用新拥有服装的卡玛下拉）；
  - 选择与发放：服装/武器页有 **全局（总）** 与 **每个分栏（分）** 两套「全选 / 取消全选 / 发放选中（N）」按钮，均显示实时选中数并在未选中时禁用；物品页每个页签的「发放选中（N）」同样按已填数量启用/禁用；
  - 任务：完成/重置类别或全部后就地勾选并刷新计数；
  - 强化管理：每次运行后就地刷新各操作的数量预览与按钮可用状态；
  - 回忆：发放/升级/改槽后就地刷新已拥有数量、文案与回忆选择器；
  - 用户档案：账户信息、宝石、批量操作后就地刷新身份与统计；**资源栏发放宝石后，上方账户信息的宝石输入框与资源行数值都会自动同步**；「发放全部 / 升级全部」类按钮在对应数量归零后自动禁用（无需刷新页面）；**等级/经验计算栏经既有 `GET /users/{id}/profile/stats` 轮询（5 秒）原地刷新等级/经验输入框、下一级差距与目标等级计算**（等级变化时目标自动顺延到当前等级+1）—— 不新增任何路由。

**Quest Editor / 关卡编辑**
- 全关卡目录渲染为多级树：`全部（总）→ 主线/活动 → 章节 → 难度 → 关卡`，层级缩进，父级勾选级联子级，父级自动显示全选/半选状态。
- 工具栏：完成选中 / 全部完成 / 清除选择；每个章节行与难度行内嵌「完成」按钮（显示剩余数）。
- **已通关关卡不可选中**（锁定样式 + 禁用勾选，级联/恢复时自动跳过），行内提供「还原 RESTORE」按钮 —— Go shim 新增 `revert_quests` 动作（状态还原 + 任务行重置，单事务、先备份）。
- 勾选状态存 localStorage，刷新不丢失；完成/还原后 Ajax 原地更新锁定状态与全部计数。

**Admin → Events / 管理 → 活动**
- 三行设置面板：① 存放路径（生成 bin.e 的输出目录，不存在自动创建，未选时应用/保存顺序禁用；**Linux/Windows 双端适配**——正反斜杠均可，Linux（WSL）下粘贴 Windows 盘符路径如 `D:\folder` 会自动映射为 `/mnt/d/folder`，并按服务器平台显示提示）② 启用 bin.e 文件（列出名称含 `bin.e` 的所有文件：bin、`.bak` 备份、`.old.<时间戳>` 旧文件，来自默认 release 目录 + 所选路径，自动合并加载）③ 自定义备份文件名：**与应用栏「无待应用更改」融合在同一行**，紧邻显示 `20240404193219.bin.e.[输入].bak`；**输入框默认显示为空**——留空即按时间戳命名，输入自定义内容则在其后追加时间戳（最终名字恒为 `<bin>.<自定义内容>.<时间戳>.bak`，时间戳有且只有一个；支持中文与特殊字符）。
- 选择启用 bin.e：自动改名为 `20240404193219.bin.e`；**非默认路径的文件自动移动到默认 release 路径**；**旧激活 bin 命名规则：`<被激活文件全名>.<时间戳>.bak`**（`20240404193219.bin.e` → `20240404193219.bin.e.<时间戳>.bak`；自定义内容则 `20240404193219.bin.e.自定义内容.<时间戳>.bak`，时间戳落在自定义内容之后）并存入所选路径；**追加内容有且只有一个**——名字里已有时间戳时只更新时间戳，绝不重复追加，且永不覆盖已存在文件（冲突时换时间戳，再冲突追加 `-2`/`-3`）。
- 激活/应用后 Ajax 自动刷新下方列表；bin.e 列表**每 5 秒自动检测文件变化**（目录指纹）并 Ajax 刷新。
- 全部确认使用网页内模态框，报错/提示双语。

**User Profile / 用户档案（新增页面）**
- `/users` 列表在 UUID 列后新增 **Edit 按钮**，点击进入 `/users/{id}/profile`（`/profile` 为入口别名，自动跳到记住的用户）。
- **用户信息操作**：账户信息区可直接修改并保存 —— 角色名称（≤32 字符）、个性签名（≤128 字符）、付费/免费宝石总量；走 Go shim 新增的 `set_user_info` 动作（`user_profile` / `user_status` / `user_gem`，单事务、先自动备份，备份原因 `profile-editor`），只提交改动的字段。**等级/经验整行已移出账户信息栏**，改为计算栏内的独立整行（等级 + 经验 + 行尾保存按钮）。
- **等级/经验计算栏**：等级/经验全页只在此处出现（上方身份概览与账户信息栏的等级/经验行均已剔除，避免重复），独立整行 = 等级输入 + 经验输入 + 行尾保存；下方目标等级行 = 目标等级标签与输入框固定同一行。刷新走既有 `/users/{id}/profile/stats`（5 秒轮询 + 保存后立即刷新），未新增路由。
- `/users` 列表的 **Edit 按钮**显式使用 `normal-select` 光标（`.cur-pointer`，且 CSS 保证可交互元素不被 `[title]` 的 help 光标覆盖）。
- 档案页把该用户的全部操作集中在一处，沿用既有面板/操作行/网页内模态框样式与交互：身份与货币概览、宝石快速发放、资源最大化、补全缺失（服装/武器/伙伴/残响/碎片）、批量升级（武器/服装/伙伴/回忆/突破/石板/卡玛/跳过过场）、任务与关卡（全部完成任务 / 全部通关 / 全部还原）、一键备份、以及各编辑器入口；操作后身份与统计数字经 Ajax 原地刷新，不整页刷新。
- **危险区（删除用户）**：档案页底部新增「危险区」，经双重确认（网页内模态框 + 输入用户ID核对）后调用 `POST /users/{id}/profile/delete`，由 Go shim 的 `delete_user` 动作在单个事务内删除 `users` 行及所有带 `user_id` 列的表（表结构自动发现，后续迁移新增的表也会覆盖），删除前自动备份（原因 `user-delete`，可在存档管理页恢复）；开启登录时仅管理员可执行。

**Backup / 存档管理**
- 新增「备份存放路径」：可自定义（不存在自动创建），服务端持久化（`data/backup_dir.txt`，gitignored），新备份（含编辑器变更前自动备份）自动存到所选路径。

**Go shim / 后端**
- 新增动作 `revert_quests`（还原已通关关卡）、`set_user_info`（名称/签名/等级/经验/宝石）、`set_missions`（任务状态/进度，走 `UpdateUser` 事务；状态 0 即删除行，服务器从不持久化 Unknown 状态）与 `delete_user`（删除账户及其全部关联数据，单事务）；`clear_quests` / `revert_quests` 响应新增 `quest_ids`（实际处理的 ID），供前端 Ajax 原地更新。
- `grant_weapon_batch` / `upgrade_all_weapons` 支持 `contents_story_ids`：在同一事务内把黑暗记忆获取过场写入 `user_contents_stories`，发放/进化出 DM 武器时不会遗留客户端会强制重播的过场队列（该队列已知会卡死地图推进）。
- 新增 `GET /admin/events/bins`（含目录指纹）、`GET /admin/events/state`、`POST /admin/events/bin/activate` 等接口。

---

## Setup & Run / 安装与运行

**Requirements / 依赖:** Python 3.10+ · Go 1.25+ (on PATH) · a `lunar-server` checkout with the encrypted bin at `server/assets/release/*.bin.e` (base may be a sibling checkout or an integrated `<lunar-server>/panel/` directory — setup/start auto-detect both layouts, and the legacy `lunar-tear` name is still recognized). The one-time master-data dump uses the bundled copy of lunar-scripts under `scripts/`.

```sh
# standalone base / 独立运行时（base 目录内）
./setup.sh          # Windows: setup.bat
./start.sh          # Windows: start.bat   [--auth]

# integrated panel / 集成到 lunar-server 时（仓库根目录，与 server/ 同级）
./panel-start.sh    # Windows: panel-start.bat   [--auth]   (auto-runs panel/setup.sh when needed)

# panel 自持的安装脚本（在 panel/ 目录内运行）
./setup.sh          # Windows: setup.bat        venv + 依赖 + master-data + shim + 补丁依赖
./patch-deps.sh     # Windows: patch-deps.bat   补丁依赖（protobuf + apktool + Java/build-tools）
```

Everything the setup script does is also available in the web UI under
**/settings → INITIALIZATION** (venv, dependencies, master-data dump, names,
grant shim, patch dependencies). Each step is auto-detected: finished steps are
reported as DONE and their button is disabled, so a step can never run twice.
setup 脚本的全部功能也已搬进 **/settings → 初始化** 分区（venv、依赖、master-data、名称、shim、补丁依赖），
每步自动检测：已完成则按钮禁用，不会重复执行。

- Binds to your LAN IP by default (banner prints the URL). Override with `LUNAR_BASE_HOST` / `LUNAR_BASE_PORT`. 默认绑定局域网 IP（启动横幅打印地址），可用环境变量覆盖。
- `--auth` (or `LUNAR_BASE_AUTH=1`) requires login: game accounts see only their own record; the admin account is created on the **/settings → Admin Account** section (or still with `tools/set_admin_password.py`). Enabling login is refused until an admin exists. `--auth` 开启登录：玩家仅见自己的记录；管理员账户在 **/settings → 管理员账户** 分区创建（`tools/set_admin_password.py` 仍可用），未创建管理员前无法开启登录。

> ⚠️ Default is **open mode (no login)** — anyone who can reach the web UI can edit the database. Run it only on a trusted network, or use `--auth`. 默认**开放模式（无登录）**——任何能访问该服务界面的人都可以编辑数据库；请在可信网络运行，或开启 `--auth`。

---

## Features / 功能

| Page / 页面 | What it does / 功能 |
|---|---|
| Save Data / 存档管理 | Snapshot `game.db`, restore (refused while lunar-tear runs), 50 kept; custom backup directory. 备份/恢复（运行中禁止恢复，保留 50 份，可自定义备份路径）。 |
| Users / 用户 | List players, view currencies & inventory; **Edit** button per row opens the profile. 查看玩家与货币/库存；每行 Edit 按钮进入档案页。 |
| Profile / 用户档案 | All operations for one user in one page: **account info edits** (name / message / gems), grants, completion, bulk upgrades, missions, quests, backup, **user deletion (danger zone, admin only with auth on)**, + editor links; **等级/经验计算栏**（等级/经验仅在此处：独立整行 + 行尾保存，目标等级与输入框同一行；既有 stats 轮询自动刷新）. 单用户全部操作集中页：**账户信息修改**（名称/签名/宝石）、**等级/经验计算栏**、发放/补全/批量升级/任务/关卡/备份、**删除用户（危险区）** + 编辑器入口。 |
| Item Editor / 物品编辑 | Gems, gold, materials, consumables, important items via `GrantPossession`; batch + MAX ALL. 宝石/金币/材料/消耗品/重要物品发放。 |
| Costume Editor / 服装编辑 | Grant R40/R30 costumes via `GrantCostume`; batch + karma effects. 发放 4星/3星服装、批量发放与卡玛效果。 |
| Weapon Editor / 武器编辑 | Grant weapons via `GrantWeapon` (skills/notes/stories cascade); 999-cap enforced; Dark Memory acquisition cutscenes are marked played in the same transaction so mass grants cannot soft-lock map progression. 发放武器（技能/笔记/剧情联动），999 上限；黑暗记忆获取过场在同事务内标记为已观看，批量发放不会卡死地图进度。 |
| Upgrade Manager / 强化管理 | Exalt characters, fill mythic slabs, add missing companions/remnants/debris, upgrade all companions/weapons/costumes, skip DM cutscenes, fill karma slots; Upgrade All Weapons also drains any DM cutscene queue an evolution produced. 角色突破、神话石板、补全伙伴/残响/碎片、批量升级、跳过过场、填卡玛；升级全部武器也会顺带清空进化产生的黑暗记忆过场队列。 |
| Memoir Editor / 回忆编辑 | Build R40 sets at lv15, upgrade all to lv15, rewrite sub-status slots. 构建 R40 套装、批量升 15 级、重写副属性。 |
| Mission Editor / 任务编辑 | Tick missions to complete/reset, category & all-active bulk ops; writes go through the Go shim's `set_missions` (lunar-tear's own save transaction), so the server may keep running. 勾选完成任务/重置，批量操作；写入经 Go shim 的 `set_missions` 走 lunar-tear 自身存档事务，服务器运行中也可安全编辑。 |
| Quest Editor / 关卡编辑 | Multi-level tree (see above). 多级多选树（见上）。 |
| Admin → Events / 管理 → 活动 | Bin output settings + event/banner toggling (see above). 输出路径/启用 bin/活动开关（见上）。 |
| Patch / 补丁 | Local port of the Colab patch tools: APK (apktool decode/patch/rebuild + zipalign + sign), IPA, master-data bin (download or one-click apply) and list.bin. Background jobs with progress/log/download. Also hosts the patch settings: tool paths, default addresses, job retention and upload limit. 本地化 Colab 补丁工具：APK 全流程、IPA、master-data（可下载或一键应用）与 list.bin；后台任务、进度/日志/下载；页内含补丁设置（工具路径、默认地址、任务/存储）。 |
| Settings / 设置 | Listen address/port (save → auto restart + browser redirect), auth toggle, **admin account create/reset in the UI**, **INITIALIZATION section (the panel owns the setup script: venv, deps, master-data dump, names, grant shim, patch deps — every step auto-detected and disabled once done)**, and game-server wizard config + best-effort start/stop/restart with log tail. 监听地址/端口（保存自动重启并跳转）、登录开关、**网页内创建/重置管理员账户**、**初始化分区（panel 接管 setup：venv/依赖/master-data/名称/shim/补丁依赖，自动检测、已完成不可重复执行）**、游戏服务器向导配置与启停/日志。 |

---

## Architecture / 架构

```
lunar-base\
├── web\          FastAPI + Jinja2 UI (app.py, routes/, services/, templates/, static/js/i18n.js)
├── scripts\      Bundled lunar-scripts tools (dump_masterdata.py + schemas.json + APK/IPA/assetbundle helpers)
├── tools\
│   ├── extract_names.py   resolve IDs → names from text bundles / 提取名称
│   └── grant\             Go shim sources (src/) + compiled binary (gitignored)
└── data\          gitignored — masterdata JSON, name maps, backups, admin.json
```

- `web\` reads `game.db` directly (sqlite3); **all mutations** go through the Go shim (`tools/grant/grant`), which replays lunar-tear's real grant/finish logic in one `UpdateUser` transaction.
- The shim is built by `panel/setup.sh`/`panel/setup.bat` (or the standalone `./setup.sh`/`setup.bat`): it detects the game-server checkout (sibling `../lunar-server/`, legacy `../lunar-tear/`, or integrated `../server/`), copies `tools/grant/src/*.go` into its `server/cmd/lunar-base-grant/` (required by Go's `internal/` rule) and runs `go build`. Re-run setup after pulling new shim sources.
- `web\` 直接读取 `game.db`（sqlite3）；**所有写入**都经由 Go shim 在单个 `UpdateUser` 事务中重放 lunar-tear 的真实发放/通关逻辑。shim 由 setup 脚本编译（复制源码到 lunar-server 内再 `go build`）。

---

## Safety / 安全

- Only writes to `game.db` and the shim dir; everything else in lunar-server is read-only. 仅写入 `game.db` 与 shim 目录，其余只读。
- Every mutation takes an **automatic backup** first (default `data/backups/` or your chosen path, 50 kept). 每次变更前自动备份（默认 `data/backups/` 或自定义路径，保留 50 份）。
- Restore is refused while the server is running. 服务运行中禁止恢复。
- All grants are **additive** — quantities never decrease; roll back via backup. 发放均为叠加，可随时回滚。

---

## Sync to a remote / 同步到远端（GitHub 等）

本仓库不绑定任何远端。推送到你自己的 fork 前，先添加远端并确认分支名：

```sh
# 添加你自己的远端（GitHub / GitLab / 任意托管平台均可）
# add your own remote (GitHub / GitLab / any host)
git remote add origin <your-repo-url>   # e.g. https://github.com/<you>/lunar-base.git
git branch -M main
```

### 一键流程 / One-shot flow

> 远端可能已领先于本地，所以**先提交本地改动，再 `pull --rebase`，最后 push**；工作区有未提交改动时 `git pull` 会直接拒绝。
> The remote may be ahead, so commit local changes first, then `pull --rebase`, then push.

```sh
cd /path/to/lunar-base

# 0) 看远端与本地差多少 / see how far ahead the remote is
git fetch origin
git status -sb                      # ## main...origin/main [behind 1] 之类

# 1) 查看改动（M=已跟踪被改，??=新文件）/ inspect changes
git status

# 2) 暂存全部改动（新增文件也会加入；.venv/、data/、编译出的 grant 二进制被 .gitignore 忽略）
#    stage everything; ignored paths (.venv/, data/, tools/grant/grant) stay out
git add -A

# 3) 提交 / commit
git commit -m "feat: describe your change"

# 4) 先拉取远端并变基（保持线性历史，避免 non-fast-forward；本地提交会重放到远端最新之上）
#    pull + rebase: replay local commit on top of the remote's latest
git pull --rebase origin main
#    冲突按 git 提示逐个解决，解决后 git rebase --continue。

# 5) 推送 / push
git push origin main
```

只想先同步远端、暂不提交 / pull without committing yet：

```sh
git stash push -u -m wip   # 连未跟踪文件一起暂存
git pull --rebase origin main
git stash pop              # 有冲突时按提示解决
```

### 查看本地与远端的差异 / Inspect local vs remote

```sh
git fetch origin
git rev-list --left-right --count HEAD...origin/main   # 左=本地独有，右=远端独有
git log --oneline origin/main -5                       # 远端最近提交
git diff origin/main..HEAD --stat                      # 将要推送的内容
```

### 首次推送的认证 / Auth for the first push

HTTPS（用 Personal Access Token 当密码；或先 `gh auth login`）：

```sh
git config --global credential.helper store   # 记住凭据（明文，注意安全）
git push origin main                          # username: 托管平台用户名，password: PAT
```

或改用 SSH / or switch to SSH：

```sh
git remote set-url origin git@github.com:<you>/<repo>.git
git push -u origin main
```

### 常用变体 / Common variants

```sh
# 只提交部分文件 / commit only selected files
git add <path> [<path> ...]
git commit -m "docs: describe your change"
git push origin main

# 撤销暂存 / unstage
git restore --staged <file>

# 丢弃工作区改动 / discard local changes to a file
git restore <file>

# 让已跟踪文件重新被 .gitignore 忽略 / stop tracking an ignored path
git rm -r --cached <path>
git commit -m "chore: stop tracking <path>"

# 查看将要推送的内容 / review what will be pushed
git diff origin/main..HEAD --stat
```

### 新增文件 / New files

新文件不会被 `.gitignore` 误伤，`git add -A` 会一并暂存；提交前用 `git status` 核对即可。
New files are not affected by `.gitignore` and are staged by `git add -A`; confirm the list with `git status` before committing.


> `.venv/`、`data/`（masterdata/名称表/备份/admin.json）与编译产物 `tools/grant/grant` 由 `.gitignore` 排除，**不会**被推送。
> `.venv/`, `data/` and the compiled shim are gitignored and never pushed.

---

## License & Disclaimer / 许可与免责

[MIT](LICENSE). Fan-made, non-commercial preservation project; not affiliated with the original publisher. All game IP belongs to its owners; no copyrighted game assets are distributed. Use at your own risk.
非商业同人存档/研究项目，与原厂商无关；游戏 IP 归其所有者，本仓库不含受版权保护的游戏资源，使用风险自负。
