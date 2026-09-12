/* Lunar Base i18n — client-side Chinese/English switching.
 *
 * Static text: mark elements with data-i18n="key" (and data-i18n-ph="key" for
 * placeholders). The original English markup is captured on init and restored
 * when switching back to English, so templates keep readable EN defaults.
 * Dynamic JS strings use t("key", "English fallback").
 *
 * Language is persisted in localStorage under "lb_lang" ("en" | "zh").
 */
(function () {
  "use strict";

  var STORAGE_KEY = "lb_lang";
  var TOGGLE_ID = "lang-toggle";

  var ZH = {
    // ---- base / nav ----
    "nav.status": "状态",
    "nav.users": "用户",
    "nav.backups": "存档管理",
    "nav.items": "物品编辑",
    "nav.costumes": "服装编辑",
    "nav.weapons": "武器编辑",
    "nav.upgrades": "强化管理",
    "nav.memoirs": "回忆编辑",
    "nav.missions": "任务编辑",
    "nav.quests": "关卡编辑",
    "nav.admin": "管理",
    "nav.profile": "档案",
    "bar.change": "切换",
    "bar.logout": "退出登录",
    "bar.menu": "菜单",
    "footer.line": "传输结束 // 人类荣光永存",
    "common.owned": "已拥有",
    "common.total": "总数",
    "common.active": "进行中",
    "common.cleared": "已清理",
    "common.missing": "缺失",
    "common.found": "已找到",
    "common.never": "从未",
    "common.none": "（无）",
    "common.filter": "筛选",
    "common.select_all": "全选",
    "common.unselect_all": "取消全选",
    "common.grant_chosen": "发放选中",
    "common.grant": "发放",
    "common.add": "添加",
    "common.working": "处理中...",
    "common.network_err": "网络错误：",
    "common.err_prefix": "错误：",
    "common.top": "回到顶部",

    // ---- index / status ----
    "status.title": "系统状态",
    "status.db": "游戏数据库",
    "status.missing": "缺失",
    "status.server": "Lunar Tear 服务器",
    "status.running": "运行中",
    "status.offline": "未运行",
    "status.no_proc": "未检测到 gRPC 端口上的进程。",
    "status.stage": "阶段",
    "status.stage_val": "0b — 只读查看器",
    "ops.title": "操作",
    "ops.users": "用户查看器",
    "ops.users_desc": "选择用户，查看货币、库存数量和可叠加总数。",
    "ops.backups": "存档管理",
    "ops.backups_desc": "备份游戏数据库并从之前的快照恢复。",

    // ---- login ----
    "login.title": "需要身份验证",
    "login.hint": "使用您的游戏账号（用户名和密码）登录。管理员使用 Lunar Base 管理员账户。",
    "login.username": "用户名",
    "login.password": "密码",
    "login.signin": "[ 登 录 ]",
    "login.no_admin": "尚未配置管理员账户。运行 <code>python tools/set_admin_password.py</code> 创建一个。",

    // ---- users ----
    "users.title": "已注册用户",
    "users.th_id": "用户ID",
    "users.th_name": "名称",
    "users.th_level": "等级",
    "users.th_logins": "登录次数",
    "users.th_last": "最后登录",
    "users.th_reg": "注册时间",
    "users.th_uuid": "UUID",
    "users.empty": "数据库中未找到用户。",
    "users.th_edit": "编辑",
    "users.edit_btn": "[ 编辑 ]",

    // ---- user detail ----
    "detail.back": "← 所有用户",
    "detail.identity": "身份信息",
    "detail.user_id": "用户ID",
    "detail.player_id": "玩家ID",
    "detail.uuid": "UUID",
    "detail.name": "名称",
    "detail.unset": "（未设置）",
    "detail.message": "签名",
    "detail.level": "等级 / 经验",
    "detail.registered": "注册时间",
    "detail.last_login": "最后登录",
    "detail.total_logins": "累计登录",
    "detail.currencies": "货币",
    "detail.paid": "付费宝石",
    "detail.free": "免费宝石",
    "detail.edit_items": "[ 编辑物品 ]",
    "detail.edit_costumes": "[ 编辑服装 ]",
    "detail.edit_weapons": "[ 编辑武器 ]",
    "detail.upgrades": "[ 强化管理 ]",
    "detail.memoirs": "[ 回忆编辑 ]",
    "detail.missions": "[ 任务编辑 ]",
    "detail.inventory": "库存",
    "detail.th_type": "类型",
    "detail.th_distinct": "不同物品数",
    "detail.th_source": "来源表",
    "detail.stackables": "可堆叠物品",
    "detail.th_ids": "不同ID数",
    "detail.th_total": "总数",
    "detail.stackables_note": "「高级物品」存储获取时间戳而非数量，因此不显示总数。",

    // ---- backup ----
    "backup.title": "存档归档",
    "backup.intro": "备份保存完整游戏数据库。仅保留最近 <strong>{{retention}}</strong> 个；创建新备份时自动清理更早的条目。恢复前会自动创建标记为 <code>pre-restore</code> 的备份。",
    "backup.source": "来源：",
    "backup.create": "创建备份",
    "backup.nodb": "未找到游戏数据库 — 无可备份。",
    "backup.archives": "归档（{{n}}）",
    "backup.restore_disabled": "恢复已禁用 ::",
    "backup.th_created": "创建时间",
    "backup.th_reason": "原因",
    "backup.th_size": "大小",
    "backup.th_filename": "文件名",
    "backup.th_restore": "恢复",
    "backup.restore_btn": "恢复",
    "backup.type_restore": "输入 RESTORE",
    "backup.empty": "暂无备份。请使用上方的「创建备份」。",
    "backup.restore_confirm": "确定从 {{name}} 恢复？\n\n将先创建恢复前的安全备份。",
    "backup.dir_label": "备份存放路径",
    "backup.dir_hint": "新备份的存放路径（不存在会自动创建），创建备份时自动保存该路径。",
    "backup.use_default": "使用默认",
    "backup.dir_save": "保存路径",
    "backup.dir_required": "备份路径不能为空。",
    "backup.confirm_phrase": "确认短语不匹配。请输入大写的 RESTORE 以确认。",

    // ---- item editor ----
    "items.title": "物品编辑",
    "items.intro": "每次发放为叠加式（使用 lunar-tear 的 <code>GrantPossession</code>）。每次变更前自动备份。游戏中打开相应菜单时界面会更新。",
    "items.warn": "编辑不应破坏存档或数据库，但仍请谨慎操作。",
    "items.max_all_note": "MAX ALL 会在一次事务中发放数百件物品。现代机器通常不到一秒完成；较慢硬件上可能需要几秒。页面在此期间保持响应。",
    "items.tab_gems": "宝石",
    "items.paid": "付费宝石",
    "items.free": "免费宝石",
    "items.owned": "已拥有",
    "items.not_owned": "未拥有",
    "items.of_currencies": "种货币（共2种）",
    "items.grant_all": "发放选中全部",
    "items.max_all": "全部最大化",
    "items.search": "按名称或ID搜索{{label}}...",
    "items.enter_amount": "发放前请输入正数数量。",
    "items.grant_failed": "发放失败。",
    "items.granted": "已发放 +{{n}}（#{{id}}）。",
    "items.nothing": "此标签页未选择任何物品。",
    "items.batch": "已发放 {{n}} 件物品，耗时 {{s}}s。",
    "items.op_failed": "操作失败。",
    "items.max_done": "{{label}} 完成：{{n}} 次发放，耗时 {{s}}s。",
    "items.max_cons_prompt": "全部最大化消耗品：将发放 5000 万金币及大量门票/勋章/硬币/碎片/晶片，覆盖整个消耗品列表。是否继续？",
    "items.max_mat_prompt": "全部最大化材料：游戏中每种材料约发放 5000 个（跳过 Longing Flicker、Recalling Light 和四个未知ID）。是否继续？",

    // ---- costume editor ----
    "costumes.title": "服装编辑",
    "costumes.intro": "通过 lunar-tear 的 <code>GrantCostume</code> 发放可玩服装 — 若角色缺失，游戏会以 1 级创建。每次变更前自动备份。R20（剧情初始）服装除外，可通过正常游玩获得。",
    "costumes.warn": "编辑不应破坏存档或数据库，但仍请谨慎操作。",
    "costumes.sort_note": "服装排序：黄昏回忆（冰心）» 黑暗记忆（重生）» 其他4星 » 3星，每组内按字母排序。GRANT ALL MISSING 在一次事务中遍历全部 258 件服装目录（约几秒）。",
    "costumes.grant_all": "发放选中全部",
    "costumes.select_all": "全选",
    "costumes.unselect_all": "取消全选",
    "costumes.karma": "更新全部卡玛效果（{{n}}）",
    "costumes.grant_missing": "发放全部缺失（{{n}}）",
    "costumes.search": "按服装名、角色或ID搜索...",
    "costumes.slot": "槽位",
    "costumes.karma_confirm": "将所选卡玛效果应用到全部 {{n}} 个已解锁槽位？备份将先行创建。",
    "costumes.karma_ok": "已更新 {{n}} 个槽位，耗时 {{s}}s。",
    "costumes.granted": "已发放 {{n}} 件服装，耗时 {{s}}s。",
    "costumes.group_confirm": "在「{{label}}」中发放选中的 {{n}} 件服装？将先自动备份。",
    "costumes.granted_one": "已发放服装 #{{id}}，耗时 {{s}}s。",
    "costumes.granted_missing": "已发放 {{n}} 件缺失服装，耗时 {{s}}s。",
    "costumes.no_karma": "没有需要应用的卡玛更改。",
    "costumes.grant_missing_confirm": "发放所有尚未拥有的 R30 和 R40 服装。这将解锁数十名角色和 250+ 件服装。是否继续？",
    "costumes.nothing": "未勾选任何服装。",

    // ---- weapon editor ----
    "weapons.title": "武器编辑",
    "weapons.intro": "通过 lunar-tear 的 <code>GrantWeapon</code> 发放武器，一次事务内填充技能、能力、武器笔记和已解锁剧情章节。每次变更前自动备份。R20（剧情初始）武器除外。",
    "weapons.warn": "游戏强制 999 武器库存上限。超出上限的批次将整体拒绝 — 不会部分发放。<code>GrantWeapon</code> 不去重，已拥有的武器在客户端过滤；重复发放会创建第二把。编辑不应破坏存档，但仍请谨慎操作。",
    "weapons.sort_note": "排序：黄昏回忆 » 黑暗记忆 » 其他4星 » 3星，每段内按字母排序。RoD 和黑暗记忆直接发放最终 R50 形态；其他4星和3星武器发放基础阶，以便在游戏内进化。",
    "weapons.grant_all": "发放选中全部",
    "weapons.select_all": "全选",
    "weapons.unselect_all": "取消全选",
    "weapons.grant_missing": "发放全部缺失（{{n}}）",
    "weapons.search": "按武器名称或ID搜索...",
    "weapons.inventory": "库存：",
    "weapons.catalog": "目录：",
    "weapons.granted": "已发放 {{n}} 件武器，耗时 {{s}}s。",
    "weapons.group_confirm": "在「{{label}}」中发放选中的 {{n}} 件武器？将先自动备份。",
    "weapons.granted_one": "已发放武器 #{{id}}，耗时 {{s}}s。",
    "weapons.granted_missing": "已发放 {{n}} 件缺失武器，耗时 {{s}}s。",
    "weapons.weapons": "件武器",
    "weapons.grant_missing_confirm": "发放目录中所有缺失的武器。若库存不足，999 行库存上限会拒绝此操作。是否继续？",
    "weapons.nothing": "未勾选任何武器。",

    // ---- mission editor ----
    "missions.title": "任务编辑",
    "missions.intro": "按类别列出所有任务。勾选任务以完成（状态设为所选值，进度填满至达成目标）；取消勾选以重置。使用「完成本类别」/「全部完成」批量操作。每次变更前自动备份。",
    "missions.warn": "&gt; CLEAR（可领取）需要服务器的任务领取 RPC 才能真正发放游戏内奖励。在标准服务器上请使用 RECEIVED 来标记任务完成而不发放物品。",
    "missions.server_running": "&gt; lunar-tear 似乎正在运行（{{info}}）。编辑前请停止它 — 运行中的服务器会在下次保存时用内存数据覆盖任务行。",
    "missions.hide_completed": "隐藏已完成",
    "missions.complete_as": "完成方式：",
    "missions.include_events": "包含活动",
    "missions.complete_all": "完成全部进行中",
    "missions.reset_all": "重置全部进行中",
    "missions.complete_cat": "完成本类别",
    "missions.reset_cat": "重置本类别",
    "missions.inactive": "（未激活）",
    "missions.search": "按名称或ID搜索{{label}}...",
    "missions.never": "从未",
    "missions.status_clear": "CLEAR（可领取）",
    "missions.status_received": "RECEIVED（已完成，无奖励）",
    "missions.one_done": "任务 #{{id}} 已{{done}}。",
    "missions.done_word": "完成",
    "missions.reset_word": "重置",
    "missions.cat_confirm": "完成此类别中的所有进行中任务（状态 {{status}}）？",
    "missions.completed": "已完成 {{n}} 个任务，耗时 {{s}}s。",
    "missions.all_confirm": "完成所有进行中的非活动任务？是否继续？",
    "missions.all_confirm_ev": "完成全部进行中任务（包括活动，数千行）？是否继续？",
    "missions.cat_reset_confirm": "将此类别中的所有进行中任务重置（取消勾选）为未开始？",
    "missions.reset_done": "已重置 {{n}} 个任务，耗时 {{s}}s。",
    "missions.reset_all_confirm": "将所有进行中的非活动任务重置为未开始？是否继续？",
    "missions.reset_all_confirm_ev": "将所有进行中的任务（包括活动）重置为未开始？是否继续？",

    // ---- memoir editor ----
    "memoir.title": "回忆编辑",
    "memoir.intro": "构建 R40 回忆套装、批量升级全部回忆至15级，或重写指定回忆的副属性。每次操作前自动备份（原因 <code>memoir-editor</code>）。",
    "memoir.warn": "绕过消耗 — lunar-base 直接写入结果状态。回忆库存上限为 {{cap}}。",
    "memoir.note": "已拥有：{{owned}} / {{cap}}。百分比属性存储为 ×10（3% → 30，12.5% → 125）。4级完美值已预填副属性字段，可编辑。",
    "memoir.build": "构建套装",
    "memoir.set": "套装",
    "memoir.grant_set": "发放15级套装",
    "memoir.mass": "批量操作",
    "memoir.upgrade_all": "升级全部回忆",
    "memoir.upgrade_all_desc": "{{n}} 个已拥有回忆 — 将所有部件等级设为 15（副属性不变）。",
    "memoir.run": "执行",
    "memoir.fix": "修复指定回忆的槽位",
    "memoir.memoir": "回忆",
    "memoir.rewrite": "重写槽位",
    "memoir.no_owned": "尚未拥有回忆。请先在上方构建套装。",
    "memoir.primary": "主属性",
    "memoir.slot": "槽位",
    "memoir.bonus": "2件套：{{a}} / 3件套：{{b}}",
    "memoir.grant_confirm": "发放套装「{{name}}」（3件回忆，15级）？备份将先行创建。",
    "memoir.granted": "已发放 {{name}}：{{n}} 件回忆，耗时 {{s}}s。",
    "memoir.up_confirm": "将所有已拥有回忆升级至15级？备份将先行创建。",
    "memoir.upgraded": "已升级 {{n}} 件回忆至15级，耗时 {{s}}s。",
    "memoir.pick": "请先选择回忆。",
    "memoir.fix_confirm": "重写所选回忆的槽位 1-4？备份将先行创建。",
    "memoir.fixed": "槽位已重写，耗时 {{s}}s。",

    // ---- upgrade manager ----
    "upgrade.title": "强化管理",
    "upgrade.weapons": "升级全部武器",
    "upgrade.costumes": "升级全部服装",
    "upgrade.companions": "升级全部伙伴",
    "upgrade.fill_karma": "填满全部卡玛槽位",
    "upgrade.skip_dm": "跳过全部暗黑记忆过场",
    "upgrade.intro": "对玩家存档进行批量强化。每次操作都是单个事务；每次运行前自动备份（原因 <code>upgrade-manager</code>）。",
    "upgrade.warn": "绕过消耗 — lunar-base 直接设置结果状态，不消耗金币或材料。游戏在下次同步时以新状态为准。",
    "upgrade.chars": "角色",
    "upgrade.inventory": "库存",
    "upgrade.mass": "批量强化",
    "upgrade.run": "执行",
    "upgrade.slot": "槽位",
    "upgrade.karma_note": "[R40/30/20] = 稀有度档位。[Ngrp] = 游戏内包含该效果的独立概率组数量（越多 = 更多服装会匹配此精确选择；其余回退到各自概率池中最稀有的条目）。",
    "upgrade.confirm": "运行「{{label}}」？备份将先行创建。",
    "upgrade.done": "{{label}}：已应用 {{n}} 项，耗时 {{s}}s。",

    // ---- quest editor (tree) ----
    "profile.title": "用户档案",
    "profile.stat_weapons": "武器",
    "profile.stat_costumes": "服装",
    "profile.stat_companions": "伙伴",
    "profile.stat_memoirs": "回忆",
    "profile.stat_quests": "已通关",
    "profile.sec_account": "账户信息",
    "profile.f_name": "角色名称",
    "profile.f_name_hint": "最多 32 个字符。",
    "profile.f_message": "个性签名",
    "profile.f_message_hint": "最多 128 个字符。",
    "profile.f_level_exp": "等级 / 经验",
    "profile.f_level_hint": "直接设置数值。",
    "profile.f_gems": "付费宝石 / 免费宝石",
    "profile.f_gems_hint": "直接设置总量（如需叠加请用下方「资源」）。",
    "profile.save": "保存",
    "profile.save_confirm": "保存「{{label}}」？将先自动备份。",
    "profile.save_ok": "{{label}}：已保存 {{n}} 个字段，耗时 {{s}}s。",
    "profile.sec_resources": "资源",
    "profile.sec_complete": "补全缺失内容",
    "profile.sec_upgrades": "批量升级",
    "profile.sec_progress": "任务与关卡",
    "profile.sec_editors": "编辑入口",
    "profile.gem_hint": "叠加发放。",
    "profile.max_consumables": "全部最大化消耗品",
    "profile.max_consumables_desc": "5000 万金币 + 大量门票/勋章/硬币/碎片/晶片。",
    "profile.max_materials": "全部最大化材料",
    "profile.max_materials_desc": "游戏中每种材料约 5000 个。",
    "profile.backup": "备份游戏数据库",
    "profile.backup_desc": "把 game.db 快照到已配置的备份目录。",
    "profile.backup_ok": "备份完成：{{name}}（{{size}}）。",
    "profile.missing_costumes": "发放全部缺失服装",
    "profile.missing_costumes_desc": "所有未拥有的 R30/R40 服装（R20 剧情初始除外）。",
    "profile.missing_weapons": "发放全部缺失武器",
    "profile.missing_weapons_desc": "所有缺失武器（强制 999 库存上限）。",
    "profile.missing_companions": "添加全部缺失伙伴",
    "profile.missing_companions_desc": "跳过已知会导致问题的 ID。",
    "profile.missing_remnants": "添加全部缺失残响",
    "profile.missing_remnants_desc": "名称前缀为「Remnant」的重要物品。",
    "profile.missing_thoughts": "添加全部缺失碎片",
    "profile.missing_thoughts_desc": "每件服装 5 次觉醒对应一个思念物品。",
    "profile.exalt_all": "突破全部可用角色",
    "profile.exalt_all_desc": "将所有未满突破 5 的角色提升至上限。",
    "profile.fill_slabs": "填满神话石板页",
    "profile.fill_slabs_desc": "已拥有角色的两座纪念碑、全部阶级。",
    "profile.up_weapons_desc": "进化、突破、精炼、强化，技能/能力全部升至 15 级。",
    "profile.up_costumes_desc": "觉醒 5、突破、强化、主动技能，并解锁卡玛槽位。",
    "profile.up_companions_desc": "所有已拥有伙伴升至最高 50 级。",
    "profile.up_memoirs_desc": "所有已拥有回忆升至 15 级（副属性不变）。",
    "profile.fill_karma_desc": "每个已解锁槽位填入该概率池中最稀有项（请先运行升级全部服装）。",
    "profile.skip_dm_desc": "清除排队中的暗黑记忆获取过场循环。",
    "profile.complete_missions": "完成全部进行中任务",
    "profile.complete_missions_desc": "标记为 RECEIVED（已完成、不发放奖励），包含活动。",
    "profile.clear_quests": "全部通关",
    "profile.clear_quests_desc": "对每个关卡重放 lunar-tear 真实通关流程（已通关跳过）。",
    "profile.revert_quests": "还原全部已通关",
    "profile.revert_quests_desc": "重新打开全部已通关关卡（仅状态；不回收已发放奖励）。",
    "profile.view_save": "[ 查看存档 ]",
    "profile.sec_calc": "等级/经验计算",
    "profile.calc_cur": "当前：Lv",
    "profile.calc_target": "目标等级",
    "profile.calc_result": "升到 Lv {{t}} 还需 {{n}} EXP（目标累计 {{tt}} - 当前累计 {{ct}}）",
    "profile.calc_next": "下一级（Lv {{n}}）还需 {{x}} EXP",
    "profile.calc_invalid": "目标等级需在 1-999 之间",
    "profile.nothing_changed": "没有需要保存的更改。",
    "quest.title": "关卡编辑",
    "quest.back": "← 返回用户 {{id}}",
    "quest.intro": "多级勾选：<strong>全部（总）</strong> → <strong>章节</strong> → <strong>不同难度</strong>，勾选父级会级联勾选其下所有关卡。点击下方按钮完成所选范围。每次通关都重放 lunar-tear 真实的通关流程 — 发放首通、任务与掉落奖励，标记通关，解锁下一关卡/难度，并记录支线剧情，与真实游玩完全一致。已通关关卡会被跳过。每次变更前自动备份。",
    "quest.warn": "主线关卡应按<strong>顺序</strong>清理。跳过前置关卡可能导致剧情进度不一致（下一章可能无法正确开启）。活动关卡相互独立 — 先勾选低难度再勾选高难度，以便解锁级联。",
    "quest.search": "按关卡名称或ID搜索...",
    "quest.all": "全部（总）",
    "quest.main": "主线",
    "quest.events": "活动",
    "quest.difficulty": "关卡",
    "quest.cleared": "已通关",
    "quest.open": "未通关",
    "quest.clear_sel": "完成选中",
    "quest.clear_all": "全部完成",
    "quest.restore": "还原",
    "quest.restore_confirm": "确定将关卡 #{{id}} 还原为未通关？将先创建备份。\n\n注意：已发放的奖励不会回收，剧情进度指针也不会回退。",
    "quest.restore_done": "已还原 {{n}} 个关卡，耗时 {{s}}s。",
    "quest.chap_done": "完成章节（{{n}}）",
    "quest.diff_done": "完成难度（{{n}}）",
    "quest.chap_done_note": "完成此章节中的所有未通关关卡（已通关自动跳过）",
    "quest.diff_done_note": "完成此难度下的所有未通关关卡（已通关自动跳过）",
    "quest.clear_sel_note": "完成所有已勾选（叶级）的关卡",
    "quest.clear_all_note": "完成目录中的全部关卡",
    "quest.clear_sel_btn": "完成选中（{{n}}）",
    "quest.clear_all_btn": "全部完成（{{n}}）",
    "quest.reset_sel": "清除选择",
    "quest.confirm": "确定要完成 {{n}} 个关卡？将先创建备份。\n\n提醒：主线关卡请确保同时勾选了前置关卡 — 乱序通关可能导致剧情指针不一致。",
    "quest.nothing": "未选择任何关卡。",
    "quest.done": "已完成 {{n}} 个关卡，耗时 {{s}}s。",

    // ---- admin events ----
    "events.title": "管理 — 活动",
    "events.out_path": "存放路径",
    "events.out_ph": "默认：服务器 release 目录",
    "events.out_hint": "生成的 bin.e 存放目录（不存在会自动创建）。必须先选择路径，否则「应用」和「保存顺序」保持禁用。",
    "events.use_default": "使用默认",
    "events.bin_pick": "启用 bin.e 文件",
    "events.bin_hint": "列出所有名称含 <code>bin.e</code> 的文件：bin、<code>.bak</code> 备份、以及被移开的 <code>.old.&lt;时间戳&gt;</code> 旧文件。选中后自动改名为 <code>20240404193219.bin.e</code>，旧激活 bin 会以<strong>所选文件的完整名字 + <code>.old.&lt;时间戳&gt;</code></strong> 移开在最后（例如激活 <code>20240404193219.bin.e.abyss-tower.bak</code> 时，旧 bin 保存为 <code>20240404193219.bin.e.abyss-tower.bak.old.20260824-123456</code>）。选择 <code>.bak</code> 或 <code>.old.&lt;时间戳&gt;</code> 文件可回滚到该版本。",
    "events.need_path": "请先选择存放路径",
    "events.activated": "已将 {{name}} 激活为 20240404193219.bin.e{{displaced}}",
    "events.displaced": "（旧文件已移开：",
    "events.activate_fail": "激活失败",
    "events.load_bins_fail": "读取 bin.e 列表失败",
    "events.modal_ok": "确定",
    "events.modal_cancel": "取消",
    "events.dir_release": "服务器",
    "events.dir_output": "输出",
    "events.kind_backup": "备份",
    "events.kind_old": "旧版",
    "modal.ok": "确定",
    "modal.cancel": "取消",
    "events.intro": "每个复选框反映所选 master-data 二进制文件中的当前活动状态。勾选要开放的活动，取消其余，然后按「应用并重建」重建文件。在此勾选/取消不会立即生效，需点击应用。",
    "events.warn": "&gt; 应用会重新打包加密文件，并在所选输出路径旁保存带日期的备份。游戏客户端重新下载 master data 后更改才会生效 — 请完全重启应用（仅重启服务器不够）。",
    "events.drag": "拖拽调整顺序",
    "events.pending": "无待应用更改",
    "events.pending_n": "有 {{n}} 项待应用更改 — 尚未应用",
    "events.reset": "重置",
    "events.apply": "应用并重建",
    "events.rebuilding": "重建中...",
    "events.search": "按名称或ID搜索{{label}}...",
    "events.check_all": "全选",
    "events.uncheck_all": "取消全选",
    "events.sort_az": "按字母排序",
    "events.save_order": "保存顺序",
    "events.saving": "保存中...",
    "events.category": "类别 — 点击全部启用/停用：",
    "events.no_tables": "未加载活动表。",
    "events.active": "已启用",
    "events.backup_label": "备份文件名：",
    "events.backup_ph": "留空 = 时间戳",
    "events.backup_hint": "自定义备份文件名的中间部分：{{prefix}}[输入].bak",
    "events.path_hint_linux": "服务器：Linux（WSL）—— 可直接粘贴 Windows 盘符路径（如 D:\\folder），将自动映射为 /mnt/d/folder。",
    "events.path_hint_win": "服务器：Windows —— 支持 \"C:\\...\" 与 \"/C:/...\" 两种路径写法。",

    "events.apply_confirm": "使用这 {{n}} 项更改重建 master-data 二进制文件？\n输出路径：{{path}}\n将先保存备份。",
    "events.apply_ok": "二进制文件已重建。{{sums}}。备份：{{name}}。\n输出文件：{{bin}}\n重新启动游戏客户端以查看更改。",
    "events.on": "开",
    "events.off": "关",
    "events.saving": "保存中...",
    "events.reorder_fail": "保存失败。",
    "events.reminder_banner": "重新启动 lunar-tear 服务器以应用新顺序。",
    "events.reminder_client": "重新启动游戏客户端以应用新顺序。",
    "events.sort_ok": "已按 A→Z 排列 — 可拖拽微调，然后保存顺序。",
    "events.order_ok": "已保存 {{n}} 条 {{kind}} 的顺序。备份：{{name}}。{{reminder}}",
    "events.reorder_fail": "保存失败。",
    "events.apply_fail": "应用失败。",
    "events.active": "active",
  };

  var EN = {
    // Only strings produced dynamically in JS need an EN entry; static text
    // lives in the templates themselves. Add here as needed.
    "common.working": "WORKING...",
    "common.network_err": "Network error: ",
    "common.err_prefix": "Error: ",
    "items.enter_amount": "Enter a positive amount before granting.",
    "backup.restore_confirm": "Restore from {{name}}?\n\nA pre-restore safety backup will be taken first.",
    "backup.dir_label": "BACKUP DIRECTORY",
    "events.path_hint_linux": "Server OS: Linux (WSL) — Windows drive paths (e.g. D:\\folder) are mapped to /mnt/d/folder automatically.",
    "events.path_hint_win": "Server OS: Windows — both \"C:\\...\" and \"/C:/...\" path styles are accepted.",
    "users.th_edit": "Edit",
    "users.edit_btn": "[ EDIT ]",
    "nav.profile": "Profile",
    "common.top": "Back to top",
    "upgrade.weapons": "Upgrade All Weapons",
    "upgrade.costumes": "Upgrade All Costumes",
    "upgrade.companions": "Upgrade All Companions",
    "upgrade.fill_karma": "Fill All Karma Slots",
    "upgrade.skip_dm": "Skip All Dark Memory Cutscenes",
    "profile.title": "User Profile",
    "profile.stat_weapons": "Weapons",
    "profile.stat_costumes": "Costumes",
    "profile.stat_companions": "Companions",
    "profile.stat_memoirs": "Memoirs",
    "profile.stat_quests": "Quests cleared",
    "profile.sec_account": "Account Info",
    "profile.f_name": "Display name",
    "profile.f_name_hint": "Max 32 characters.",
    "profile.f_message": "Message",
    "profile.f_message_hint": "Max 128 characters.",
    "profile.f_level_exp": "Level / EXP",
    "profile.f_level_hint": "Sets the values directly.",
    "profile.f_gems": "Paid / Free Gems",
    "profile.f_gems_hint": "Sets the totals directly (use Resources below to add).",
    "profile.save": "SAVE",
    "profile.save_confirm": "Save \u201c{{label}}\u201d? A backup is taken first.",
    "profile.save_ok": "{{label}}: {{n}} field(s) saved in {{s}}s.",
    "profile.sec_resources": "Resources",
    "profile.sec_complete": "Complete Missing Content",
    "profile.sec_upgrades": "Bulk Upgrades",
    "profile.sec_progress": "Missions & Quests",
    "profile.sec_editors": "Editors",
    "profile.gem_hint": "Additive grant.",
    "profile.max_consumables": "MAX ALL Consumables",
    "profile.max_consumables_desc": "50M gold + thousands of tickets / medals / coins / fragments / shards.",
    "profile.max_materials": "MAX ALL Materials",
    "profile.max_materials_desc": "~5,000 of every material in the game.",
    "profile.backup": "Backup game database",
    "profile.backup_desc": "Snapshot game.db into the configured backup directory.",
    "profile.backup_ok": "Backup created: {{name}} ({{size}}).",
    "profile.missing_costumes": "Grant All Missing Costumes",
    "profile.missing_costumes_desc": "Every R30/R40 costume not owned (R20 story starters excluded).",
    "profile.missing_weapons": "Grant All Missing Weapons",
    "profile.missing_weapons_desc": "Every missing weapon (999-row inventory cap enforced).",
    "profile.missing_companions": "Add All Missing Companions",
    "profile.missing_companions_desc": "Skips the known game-breaking ids.",
    "profile.missing_remnants": "All Missing Remnants",
    "profile.missing_remnants_desc": "Important Items prefixed \u201cRemnant\u201d.",
    "profile.missing_thoughts": "Add All Missing Debris",
    "profile.missing_thoughts_desc": "One Thought item per costume awakening 5.",
    "profile.exalt_all": "Exalt All Available Characters",
    "profile.exalt_all_desc": "Raises every owned character below exalt 5.",
    "profile.fill_slabs": "Fill Mythic Slab Pages",
    "profile.fill_slabs_desc": "Both monuments, all ranks, for owned characters.",
    "profile.up_weapons_desc": "Ascend, evolve, refine, enhance, all skills/abilities to lv15.",
    "profile.up_costumes_desc": "Awaken 5, ascend, enhance, active skill, unlock karma slots.",
    "profile.up_companions_desc": "Every owned companion to max level 50.",
    "profile.up_memoirs_desc": "Every owned memoir to lv15 (sub-status rows untouched).",
    "profile.fill_karma_desc": "Rarest pool entry per unlocked slot (run Upgrade All Costumes first).",
    "profile.skip_dm_desc": "Clears the queued DM-acquisition cutscene loop.",
    "profile.complete_missions": "Complete All Active Missions",
    "profile.complete_missions_desc": "Marked RECEIVED (done, no reward) including events.",
    "profile.clear_quests": "Complete Every Quest",
    "profile.clear_quests_desc": "Replays lunar-tear's real finish flow for every quest (cleared ones skipped).",
    "profile.revert_quests": "Restore Every Cleared Quest",
    "profile.revert_quests_desc": "Reopens every cleared quest (state only; rewards are not removed).",
    "profile.view_save": "[ VIEW SAVE ]",
    "profile.sec_calc": "Level / EXP Calculator",
    "profile.calc_cur": "Current: Lv",
    "profile.calc_target": "Target level",
    "profile.calc_result": "Reach Lv {{t}}: need {{n}} EXP (target total {{tt}} - current total {{ct}})",
    "profile.calc_next": "Next level (Lv {{n}}): {{x}} EXP to go",
    "profile.calc_invalid": "Target level must be 1-999",
    "profile.nothing_changed": "Nothing changed.",
    "backup.dir_hint": "Path for new backups (created automatically if missing). Saved server-side when a backup is created.",
    "backup.use_default": "USE DEFAULT",
    "backup.dir_save": "SAVE PATH",
    "backup.dir_required": "Backup directory is required.",
    "backup.confirm_phrase": "Confirmation phrase did not match. Type RESTORE in uppercase to confirm.",
    "items.grant_failed": "Grant failed.",
    "items.op_failed": "Operation failed.",
    "items.nothing": "Nothing chosen on this tab.",
    "items.max_cons_prompt": "MAX ALL consumables: 50M gold + thousands of tickets/medals/coins/fragments/shards across the whole consumable list. Continue?",
    "items.max_mat_prompt": "MAX ALL materials: ~5,000 of every material in the game (skipping Longing Flicker, Recalling Light, and four unknown ids). Continue?",
    "costumes.karma_confirm": "Apply the selected karma effect to all {{n}} unlocked slots? A backup is taken first.",
    "costumes.select_all": "CHECK ALL",
    "costumes.unselect_all": "UNCHECK ALL",
    "weapons.select_all": "CHECK ALL",
    "weapons.unselect_all": "UNCHECK ALL",
    "costumes.granted_one": "Granted costume #{{id}} in {{s}}s.",
    "costumes.group_confirm": "Grant {{n}} selected costume(s) in \u201c{{label}}\u201d? A backup is taken first.",
    "costumes.granted_missing": "Granted {{n}} missing costume(s) in {{s}}s.",
    "costumes.no_karma": "No karma changes to apply.",
    "costumes.grant_missing_confirm": "Grant every R30 + R40 costume you don't already own. This unlocks dozens of characters and 250+ costumes. Continue?",
    "costumes.nothing": "Nothing chosen.",
    "weapons.granted_one": "Granted weapon #{{id}} in {{s}}s.",
    "weapons.group_confirm": "Grant {{n}} selected weapon(s) in \u201c{{label}}\u201d? A backup is taken first.",
    "weapons.granted_missing": "Granted {{n}} missing weapon(s) in {{s}}s.",
    "weapons.grant_missing_confirm": "Grant every missing weapon in the catalog. The 999-row inventory cap will refuse this if you don't have room. Continue?",
    "weapons.nothing": "Nothing chosen.",
    "memoir.pick": "Pick a memoir first.",
    "memoir.grant_confirm": "Grant set \"{{name}}\" (3 memoirs at lv15)? A backup is taken first.",
    "memoir.up_confirm": "Upgrade every owned memoir to lv15? A backup is taken first.",
    "memoir.fix_confirm": "Rewrite slots 1-4 on the selected memoir? A backup is taken first.",
    "upgrade.confirm": "Run \"{{label}}\"? A backup is taken first.",
    "quest.clear_sel_btn": "CLEAR SELECTED ({{n}})",
    "quest.clear_all_btn": "COMPLETE ALL ({{n}})",
    "quest.clear_sel_note": "Complete every checked (leaf-level) quest",
    "quest.clear_all_note": "Complete every quest in the catalog",
    "quest.restore": "RESTORE",
    "quest.restore_confirm": "Revert quest #{{id}} to OPEN? A backup is taken first.\n\nNote: rewards already granted are not removed, and the story pointer stays where it is.",
    "quest.restore_done": "Reverted {{n}} quest(s) in {{s}}s.",
    "quest.chap_done": "COMPLETE CHAPTER ({{n}})",
    "quest.diff_done": "COMPLETE DIFFICULTY ({{n}})",
    "quest.chap_done_note": "Complete every uncleared quest in this chapter (already-cleared are skipped)",
    "quest.diff_done_note": "Complete every uncleared quest at this difficulty (already-cleared are skipped)",
    "quest.done": "Cleared {{n}} quest(s) in {{s}}s.",
    "quest.confirm": "Clear {{n}} quest(s)? A backup is taken first.\n\nReminder: for main-story quests, make sure you've ticked the earlier quests too — clearing out of order can leave the story pointer inconsistent.",
    "missions.one_done": "Mission #{{id}} {{done}}.",
    "missions.cat_confirm": "Complete every active mission in this category (status {{status}})?",
    "missions.completed": "Completed {{n}} mission(s) in {{s}}s. Reloading...",
    "missions.all_confirm": "Complete all active non-event missions. Continue?",
    "missions.all_confirm_ev": "Complete ALL active missions INCLUDING events (thousands of rows). Continue?",
    "missions.cat_reset_confirm": "Reset (untick) every active mission in this category to NOT STARTED?",
    "missions.reset_done": "Reset {{n}} mission(s) in {{s}}s. Reloading...",
    "missions.reset_all_confirm": "Reset all active non-event missions to NOT STARTED. Continue?",
    "missions.reset_all_confirm_ev": "Reset ALL active missions INCLUDING events to NOT STARTED. Continue?",
    "quest.nothing": "Nothing chosen.",
    "events.apply_confirm": "Rebuild the master-data bin with these {{n}} change(s)? A dated backup is saved first.",
    "events.out_path": "Output path",
    "events.out_ph": "default: server release dir",
    "events.out_hint": "Where the generated bin.e will be stored (created automatically if missing). Choose a path first — Apply / Save Order stay disabled until then.",
    "events.use_default": "USE DEFAULT",
    "events.bin_pick": "Active bin.e file",
    "events.bin_hint": "Every file whose name contains bin.e is listed — bins, .bak backups, and displaced .old.<stamp> files. Picking one enables it: it is renamed to 20240404193219.bin.e automatically in its own directory, and the old active bin is moved aside under the picked file's full name with .old.<stamp> appended at the very end (e.g. activating 20240404193219.bin.e.abyss-tower.bak preserves the old bin as 20240404193219.bin.e.abyss-tower.bak.old.20260824-123456). Picking a .bak or .old.<stamp> file rolls the bin back to that version.",
    "events.need_path": "Choose an output path first",
    "events.activated": "Enabled {{name}} as 20240404193219.bin.e{{displaced}}",
    "events.displaced": " (old file moved aside: ",
    "events.activate_fail": "Activation failed",
    "events.load_bins_fail": "Failed to load bin.e list",
    "events.modal_ok": "OK",
    "events.modal_cancel": "CANCEL",
    "events.dir_release": "release",
    "events.dir_output": "output",
    "events.kind_backup": "backup",
    "events.kind_old": "old",
    "modal.ok": "OK",
    "modal.cancel": "CANCEL",
    "events.drag": "Drag to reorder",
    "events.on": "on",
    "events.off": "off",
    "events.saving": "SAVING...",
    "events.reorder_fail": "Failed.",
    "events.reminder_banner": "Restart the lunar-tear server to apply the new order.",
    "events.reminder_client": "Relaunch the game client to apply the new order.",
  };

  function currentLang() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return v === "zh" ? "zh" : "en";
    } catch (e) {
      return "en";
    }
  }

  function setLang(lang) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}
    applyI18n(lang);
  }

  // t("key", "English fallback") — dynamic strings built in JS.
  function t(key, fallback) {
    var lang = currentLang();
    var dict = lang === "zh" ? ZH : EN;
    if (key in dict) return dict[key];
    if (lang === "zh" && key in EN) return EN[key];
    return fallback != null ? fallback : key;
  }

  // Substitute {{name}} placeholders inside a translation.
  function tpl(key, vars, fallback) {
    var s = t(key, fallback);
    for (var k in vars) {
      s = s.split("{{" + k + "}}").join(String(vars[k]));
    }
    return s;
  }

  function elementVars(el) {
    // Substitute {{var}} placeholders declared as data-i18n-vars-<name> /
    // data-i18n-ph-vars-<name>.
    var vars = {};
    if (!el.dataset) return vars;
    for (var attr in el.dataset) {
      var key = null;
      if (attr.indexOf("i18nVars") === 0 && attr.length > "i18nVars".length) {
        key = attr.slice("i18nVars".length);
      } else if (attr.indexOf("i18nPhVars") === 0 && attr.length > "i18nPhVars".length) {
        key = attr.slice("i18nPhVars".length);
      }
      if (key) vars[key.charAt(0).toLowerCase() + key.slice(1)] = el.dataset[attr];
    }
    return vars;
  }

  function applyVars(text, vars) {
    for (var k in vars) {
      text = text.split("{{" + k + "}}").join(String(vars[k]));
    }
    return text;
  }

  function translateElement(el, lang) {
    var vars = elementVars(el);
    if (el.dataset && el.dataset.i18nTitle) {
      var tk = el.dataset.i18nTitle;
      if (lang === "zh" && ZH[tk]) el.title = applyVars(ZH[tk], vars);
      else if (el.dataset.i18nTitleOrig !== undefined) el.title = el.dataset.i18nTitleOrig;
      return;
    }
    if (el.dataset && el.dataset.i18nPh) {
      var phKey = el.dataset.i18nPh;
      el.placeholder = lang === "zh"
        ? (ZH[phKey] ? applyVars(ZH[phKey], vars) : el.placeholder)
        : (el.dataset.i18nPhOrig || "");
      return;
    }
    var key = el.dataset && el.dataset.i18n;
    if (!key) return;
    if (lang === "zh") {
      var zh = ZH[key];
      if (zh) el.innerHTML = applyVars(zh, vars);
    } else {
      if (el.dataset.i18nOrig !== undefined) el.innerHTML = el.dataset.i18nOrig;
    }
  }

  function applyI18n(lang) {
    lang = lang || currentLang();
    var root = document.documentElement;
    root.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      if (el.dataset.i18nOrig === undefined) {
        el.dataset.i18nOrig = el.innerHTML;
      }
      translateElement(el, lang);
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      if (el.dataset.i18nPhOrig === undefined) {
        el.dataset.i18nPhOrig = el.placeholder || "";
      }
      translateElement(el, lang);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      if (el.dataset.i18nTitleOrig === undefined) {
        el.dataset.i18nTitleOrig = el.title || "";
      }
      translateElement(el, lang);
    });
    var toggle = document.getElementById(TOGGLE_ID);
    if (toggle) {
      toggle.textContent = lang === "zh" ? "EN" : "中文";
      toggle.setAttribute("aria-label", lang === "zh" ? "Switch to English" : "切换到中文");
    }
    if (window.I18N_HOOK) window.I18N_HOOK(lang);
  }

  function bindToggle() {
    var toggle = document.getElementById(TOGGLE_ID);
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      setLang(currentLang() === "zh" ? "en" : "zh");
    });
  }

  // Shared in-page confirm modal. Exposes window.askConfirm(message) ->
  // Promise<boolean> so every page replaces native confirm() with a styled
  // dialog (overlay click / CANCEL -> false, OK -> true). Defined immediately
  // as a native fallback so a click can never die; initModal() swaps in the
  // real modal once the DOM is ready.
  window.askConfirm = function (message) {
    try {
      return Promise.resolve(window.confirm ? window.confirm(message) : true);
    } catch (e) {
      return Promise.resolve(true);
    }
  };
  function initModal() {
    var overlay = document.getElementById("app-modal-overlay");
    var text = document.getElementById("app-modal-text");
    var okBtn = document.getElementById("app-modal-ok");
    var cancelBtn = document.getElementById("app-modal-cancel");
    window.askConfirm = function (message) {
      // Safety net: if the modal markup is missing (e.g. a cached page) fall
      // back to the native dialog rather than leaving every button silent.
      if (!overlay || !okBtn || !cancelBtn) {
        try {
          return Promise.resolve(window.confirm ? window.confirm(message) : true);
        } catch (e) {
          return Promise.resolve(true);
        }
      }
      return new Promise(function (resolve) {
        text.textContent = message;
        overlay.hidden = false;
        function done(val) {
          overlay.hidden = true;
          okBtn.onclick = null;
          cancelBtn.onclick = null;
          overlay.onclick = null;
          resolve(val);
        }
        okBtn.onclick = function () { done(true); };
        cancelBtn.onclick = function () { done(false); };
        overlay.onclick = function (e) { if (e.target === overlay) done(false); };
      });
    };
  }

  // 把服务器（Linux/WSL）路径转成 Windows 风格显示（/mnt/d/x -> D:\\x）。
  // 仅用于显示；发给服务器的原始输入由服务端做反向映射。
  window.winPath = function (p) {
    if (!p || typeof p !== "string") return p;
    var m = p.match(/^\/mnt\/([a-z])(?:\/(.*))?$/i);
    if (m) {
      var drive = m[1].toUpperCase();
      var rest = (m[2] || "").replace(/\//g, "\\");
      return drive + ":\\" + rest;
    }
    return p;
  };

  window.I18N = {
    ZH: ZH,
    EN: EN,
    lang: currentLang,
    set: setLang,
    t: t,
    tpl: tpl,
    apply: applyI18n,
  };

  function bootstrap() {
    try { applyI18n(); } catch (e) { /* never let i18n break the page */ }
    try { bindToggle(); } catch (e) {}
    try { initModal(); } catch (e) {}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
