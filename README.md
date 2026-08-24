# Lunar Base

> Web-based management console for a [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) private server — backup, restore, and edit the player database from a browser.
> 基于浏览器的 [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) 私服管理面板 —— 浏览器中备份、恢复、编辑玩家数据库。

**UI language / 界面语言:** every page has a top-right `中文 / EN` toggle (remembered per browser, `lb_lang`). 全站右上角可切换中/英文，选择会被记住。

---

## What changed vs. upstream / 相对原项目的更改内容

This fork adds the following on top of the upstream project. 本分支在原项目基础上新增/修改了以下内容：

**全站 / Global**
- **中英双语切换**：每个页面右上角 `中文 / EN` 按钮，所有文案（导航、按钮、提示、搜索占位、动态弹窗/横幅）双语，选择存于 localStorage。
- **网页内确认弹窗**：全部原生 `window.confirm()` 替换为统一的网页内模态框（遮罩 + 确定/取消，点击遮罩取消），任何页面都不再弹出浏览器原生对话框。
- **静态资源缓存版本号**：`i18n.js` / `automata.css` 自动附加 `?v=<mtime>`，避免浏览器缓存旧脚本导致按钮失效。
- **Ajax 原地刷新**：关卡完成/还原、事件页应用/激活后均通过 Ajax 更新界面，不再整页刷新。

**Quest Editor / 关卡编辑**
- 全关卡目录渲染为多级树：`全部（总）→ 主线/活动 → 章节 → 难度 → 关卡`，层级缩进，父级勾选级联子级，父级自动显示全选/半选状态。
- 工具栏：完成选中 / 全部完成 / 清除选择；每个章节行与难度行内嵌「完成」按钮（显示剩余数）。
- **已通关关卡不可选中**（锁定样式 + 禁用勾选，级联/恢复时自动跳过），行内提供「还原 RESTORE」按钮 —— Go shim 新增 `revert_quests` 动作（状态还原 + 任务行重置，单事务、先备份）。
- 勾选状态存 localStorage，刷新不丢失；完成/还原后 Ajax 原地更新锁定状态与全部计数。

**Admin → Events / 管理 → 活动**
- 三行设置面板：① 存放路径（生成 bin.e 的输出目录，不存在自动创建，未选时应用/保存顺序禁用）② 启用 bin.e 文件（列出名称含 `bin.e` 的所有文件：bin、`.bak` 备份、`.old.<时间戳>` 旧文件，来自默认 release 目录 + 所选路径，自动合并加载）③ 自定义备份文件名（`20240404193219.bin.e.<自定义>.bak`，支持中文与特殊字符，空则回退时间戳）。
- 选择启用 bin.e：自动改名为 `20240404193219.bin.e`；**非默认路径的文件自动移动到默认 release 路径**；**旧激活 bin 按 `<被激活文件全名>.old.<时间戳>` 命名并存入所选路径**（`.old.` 只添加一次，已有时仅更新时间戳）。
- 激活/应用后 Ajax 自动刷新下方列表；bin.e 列表**每 5 秒自动检测文件变化**（目录指纹）并 Ajax 刷新。
- 全部确认使用网页内模态框，报错/提示双语。

**Backup / 存档管理**
- 新增「备份存放路径」：可自定义（不存在自动创建），服务端持久化（`data/backup_dir.txt`，gitignored），新备份（含编辑器变更前自动备份）自动存到所选路径。

**Go shim / 后端**
- 新增动作 `revert_quests`（还原已通关关卡）；`clear_quests` / `revert_quests` 响应新增 `quest_ids`（实际处理的 ID），供前端 Ajax 原地更新。
- 新增 `GET /admin/events/bins`（含目录指纹）、`GET /admin/events/state`、`POST /admin/events/bin/activate` 等接口。

---

## Setup & Run / 安装与运行

**Requirements / 依赖:** Python 3.10+ · Go 1.25+ (on PATH) · a sibling `../lunar-tear/` checkout with the encrypted bin at `server/assets/release/*.bin.e` · `../lunar-scripts/` (one-time master-data dump).

```sh
# one-time setup / 首次安装
./setup.sh          # Windows: setup.bat
# run / 运行
./run-lunar-base.sh # Windows: run-lunar-base.bat   [--auth]
```

- Binds to your LAN IP by default (banner prints the URL). Override with `LUNAR_BASE_HOST` / `LUNAR_BASE_PORT`. 默认绑定局域网 IP（启动横幅打印地址），可用环境变量覆盖。
- `--auth` (or `LUNAR_BASE_AUTH=1`) requires login: game accounts see only their own record; the admin account is created with `tools/set_admin_password.py`. `--auth` 开启登录：玩家仅见自己的记录，管理员账号用 `tools/set_admin_password.py` 创建。

> ⚠️ Default is **open mode (no login)** — anyone who can reach this PC can edit the database. Run only on a trusted network, or use `--auth`. 默认**开放模式（无登录）**，请在可信网络运行或开启 `--auth`。

---

## Features / 功能

| Page / 页面 | What it does / 功能 |
|---|---|
| Save Data / 存档管理 | Snapshot `game.db`, restore (refused while lunar-tear runs), 50 kept; custom backup directory. 备份/恢复（运行中禁止恢复，保留 50 份，可自定义备份路径）。 |
| Users / 用户 | List players, view currencies & inventory. 查看玩家与货币/库存。 |
| Item Editor / 物品编辑 | Gems, gold, materials, consumables, important items via `GrantPossession`; batch + MAX ALL. 宝石/金币/材料/消耗品/重要物品发放。 |
| Costume Editor / 服装编辑 | Grant R40/R30 costumes via `GrantCostume`; batch + karma effects. 发放 4星/3星服装、批量发放与卡玛效果。 |
| Weapon Editor / 武器编辑 | Grant weapons via `GrantWeapon` (skills/notes/stories cascade); 999-cap enforced. 发放武器（技能/笔记/剧情联动），999 上限。 |
| Upgrade Manager / 强化管理 | Exalt characters, fill mythic slabs, add missing companions/remnants/debris, upgrade all companions/weapons/costumes, skip DM cutscenes, fill karma slots. 角色突破、神话石板、补全伙伴/残响/碎片、批量升级、跳过过场、填卡玛。 |
| Memoir Editor / 回忆编辑 | Build R40 sets at lv15, upgrade all to lv15, rewrite sub-status slots. 构建 R40 套装、批量升 15 级、重写副属性。 |
| Mission Editor / 任务编辑 | Tick missions to complete/reset, category & all-active bulk ops. 勾选完成任务/重置，批量操作。 |
| Quest Editor / 关卡编辑 | Multi-level tree (see above). 多级多选树（见上）。 |
| Admin → Events / 管理 → 活动 | Bin output settings + event/banner toggling (see above). 输出路径/启用 bin/活动开关（见上）。 |

---

## Architecture / 架构

```
lunar-base\
├── web\          FastAPI + Jinja2 UI (app.py, routes/, services/, templates/, static/js/i18n.js)
├── tools\
│   ├── extract_names.py   resolve IDs → names from text bundles / 提取名称
│   └── grant\             Go shim sources (src/) + compiled binary (gitignored)
└── data\          gitignored — masterdata JSON, name maps, backups, admin.json
```

- `web\` reads `game.db` directly (sqlite3); **all mutations** go through the Go shim (`tools/grant/grant`), which replays lunar-tear's real grant/finish logic in one `UpdateUser` transaction.
- The shim is built by `setup.sh`/`setup.bat`: it copies `tools/grant/src/*.go` into `../lunar-tear/server/cmd/lunar-base-grant/` (required by Go's `internal/` rule) and runs `go build`. Re-run setup after pulling new shim sources.
- `web\` 直接读取 `game.db`（sqlite3）；**所有写入**都经由 Go shim 在单个 `UpdateUser` 事务中重放 lunar-tear 的真实发放/通关逻辑。shim 由 setup 脚本编译（复制源码到 lunar-tear 内再 `go build`）。

---

## Safety / 安全

- Only writes to `game.db` and the shim dir; everything else in lunar-tear is read-only. 仅写入 `game.db` 与 shim 目录，其余只读。
- Every mutation takes an **automatic backup** first (default `data/backups/` or your chosen path, 50 kept). 每次变更前自动备份（默认 `data/backups/` 或自定义路径，保留 50 份）。
- Restore is refused while the server is running. 服务运行中禁止恢复。
- All grants are **additive** — quantities never decrease; roll back via backup. 发放均为叠加，可随时回滚。

---

## License & Disclaimer / 许可与免责

[MIT](LICENSE). Fan-made, non-commercial preservation project; not affiliated with the original publisher. All game IP belongs to its owners; no copyrighted game assets are distributed. Use at your own risk.
非商业同人存档/研究项目，与原厂商无关；游戏 IP 归其所有者，本仓库不含受版权保护的游戏资源，使用风险自负。
