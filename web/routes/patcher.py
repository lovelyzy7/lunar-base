"""内容修补器（Content patcher）：桌面面板 /patcher 枢纽页。
主数据识别（JP/GL 按内容）+ 日程状态 + 工具三件入口 + 重建/部署降级明示。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import masterdata_bin

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))

# 服务器双主数据（与 lunar-tear 双区一致）
MASTERS = {
    "jp": Path(r"E:\NieRReincarnation\lunar-tear\server\assets\release\20240405123139.bin.e"),
    "gl": Path(r"E:\NieRReincarnation\lunar-tear\server\assets\release\20240404193219.bin.e"),
}


def _identify(path: Path) -> dict:
    """按内容识别主数据版本（大小拒读 + AES/msgpack TOC + m_mission 特征判 jp）。"""
    out = {"path": str(path), "exists": path.is_file(), "size": 0, "edition": "unknown",
           "tables": 0, "note": ""}
    if not path.is_file():
        out["note"] = "主数据文件缺失"
        return out
    out["size"] = path.stat().st_size
    if out["size"] > 64 * 1024 * 1024:
        out["note"] = "文件过大，拒绝读取"
        return out
    try:
        n = masterdata_bin.validate_bin(path)
        out["tables"] = n
    except Exception as exc:
        out["note"] = f"解析失败：{exc}"
        return out
    # m_mission 的日服条件特征（与 launcher region.master_edition 同源）
    try:
        rows = masterdata_bin.decode_rows("m_mission", source=path)
        hit = 0
        for r in rows:
            cells = r if isinstance(r, list) else []
            flat = tuple(cells[:4])
            if flat in ((10, 198, 0, 0), (6, 201, 0, 0), (29, 7, 0, 0)) or (1, 101030801, 0) in [tuple(x[:3]) if isinstance(x, list) else x for x in cells]:
                hit += 1
        out["edition"] = "jp" if hit else "en"
        out["note"] = f"m_mission 日服特征 {hit} 处"
    except Exception as exc:
        out["note"] = f"版本识别降级：{exc}"
    return out


def _calendar_state() -> dict:
    cal = Path(r"E:\NieRReincarnation\lunar-tear\server\db\calendar.json")
    st = {"exists": cal.is_file(), "raw": None, "on": False, "loop": 0, "speed": 1}
    if cal.is_file():
        try:
            import json
            st["raw"] = json.loads(cal.read_text(encoding="utf-8"))
            st["on"] = True
            st["loop"] = st["raw"].get("loop", 0)
            st["speed"] = st["raw"].get("speed", 1)
        except Exception:
            st["on"] = False
    return st


@router.get("/patcher", response_class=HTMLResponse)
def patcher_home(request: Request):
    masters = {k: _identify(v) for k, v in MASTERS.items()}
    return templates.TemplateResponse(request, "patcher.html", {
        "active": "patcher",
        "masters": masters,
        "calendar": _calendar_state(),
        "now": time.strftime("%Y-%m-%d %H:%M"),
    })
