"""activity_keywords.py — Ikaros 应用分类词库 (精简版)

移植自 N.E.K.O 的 config/activity_keywords.py（3089 行全量词库），
按需裁剪为 Ikaros 常用集合。职责：把 (process_name, window_title, url)
映射成结构化类别，供状态机推出 activity_state。

匹配语义：
* 进程名精确小写匹配 (PROCESS_MAP)。
* 浏览器进程走域名匹配 (BROWSER_DOMAIN)，否则标题匹配 (TITLE_MAP)。
* 隐私黑名单 (PRIVATE_*) 命中即 category='private'。
* 全部大小写不敏感；CJK 关键词直接子串匹配。

category 优先级（高→低）：gaming > work > communication > entertainment
（与 N.E.K.O 一致：游戏最强免打扰信号；工作压过后台 IM/视频）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("ikaros.activity")

__all__ = [
    "PROCESS_MAP", "BROWSER_PROCESS", "BROWSER_DOMAIN", "TITLE_MAP",
    "PRIVATE_PROCESS", "PRIVATE_TITLE", "OWN_APP_PROCESS",
    "classify", "add_override", "PROCESS_OVERRIDES_PATH",
]

# ── 用户学习到的进程覆盖（持久化到 JSON，优先级高于静态 PROCESS_MAP）──
# 监测到未知进程时，process_learner 会用 LLM 识别并写回这里，
# 下次启动自动加载。只存 exe 名，绝不存窗口标题（隐私）。
PROCESS_OVERRIDES_PATH = Path(__file__).resolve().with_name("process_overrides.json")


def _load_overrides() -> dict[str, tuple[str, str, str]]:
    try:
        if PROCESS_OVERRIDES_PATH.exists():
            data = json.loads(PROCESS_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k).lower(): tuple(v) for k, v in data.items()
                        if isinstance(v, (list, tuple)) and len(v) >= 3}
    except Exception as e:
        logger.warning("加载 process_overrides.json 失败: %s", e)
    return {}


# 运行时 + 持久化的用户覆盖表
_USER_PROCESS_MAP: dict[str, tuple[str, str, str]] = _load_overrides()


def add_override(proc: str, category: str, subcategory: str, canonical: str) -> None:
    """把一个识别到的进程写回 process_overrides.json（持久化 + 当前进程即时生效）。

    只接受白名单 category，避免 LLM 胡写污染分类。
    """
    ALLOWED = {"gaming", "work", "communication", "entertainment", "browser", "private"}
    if category not in ALLOWED:
        logger.debug("add_override 拒绝非法 category=%r (proc=%r)", category, proc)
        return
    p = (proc or "").lower().strip()
    if not p:
        return
    _USER_PROCESS_MAP[p] = (category, subcategory, canonical)
    try:
        data = {k: list(v) for k, v in _USER_PROCESS_MAP.items()}
        PROCESS_OVERRIDES_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已学习并持久化进程: %s -> %s/%s/%s", p, category, subcategory, canonical)
    except Exception as e:
        logger.warning("写 process_overrides.json 失败: %s", e)


# ── 进程名精确匹配 (小写 exe 名) ──
# value: (category, subcategory, canonical)
PROCESS_MAP: dict[str, tuple[str, str, str]] = {
    # --- 工作 / IDE ---
    "code.exe":            ("work", "ide", "VS Code"),
    "cursor.exe":          ("work", "ide", "Cursor"),
    "devenv.exe":          ("work", "ide", "Visual Studio"),
    "idea64.exe":          ("work", "ide", "IntelliJ IDEA"),
    "pycharm64.exe":       ("work", "ide", "PyCharm"),
    "webstorm64.exe":      ("work", "ide", "WebStorm"),
    "clion64.exe":         ("work", "ide", "CLion"),
    "rider.exe":           ("work", "ide", "Rider"),
    "sublime_text.exe":    ("work", "ide", "Sublime Text"),
    "notepad++.exe":       ("work", "editor", "Notepad++"),
    "vim.exe":             ("work", "editor", "Vim"),
    "neovide.exe":         ("work", "editor", "Neovim"),
    "notepad.exe":         ("work", "editor", "记事本"),
    # --- 工作 / Office ---
    "winword.exe":         ("work", "office", "Word"),
    "excel.exe":           ("work", "office", "Excel"),
    "powerpnt.exe":        ("work", "office", "PowerPoint"),
    "outlook.exe":         ("work", "office", "Outlook"),
    "acrobat.exe":         ("work", "pdf", "Acrobat"),
    "acrord32.exe":        ("work", "pdf", "Acrobat Reader"),
    "sumatrapdf.exe":      ("work", "pdf", "SumatraPDF"),
    # --- 工作 / 设计开发 ---
    "photoshop.exe":       ("work", "design", "Photoshop"),
    "illustrator.exe":     ("work", "design", "Illustrator"),
    "figma.exe":           ("work", "design", "Figma"),
    "blender.exe":         ("work", "3d", "Blender"),
    "autocad.exe":         ("work", "cad", "AutoCAD"),
    "windowsterminal.exe": ("work", "terminal", "Windows Terminal"),
    "conhost.exe":         ("work", "terminal", "控制台"),
    "powershell.exe":      ("work", "terminal", "PowerShell"),
    "cmd.exe":             ("work", "terminal", "命令提示符"),
    "bash.exe":            ("work", "terminal", "Bash"),
    "wsl.exe":             ("work", "terminal", "WSL"),
    # --- IDE / 编辑器 / 开发工具 ---
    "workbuddy.exe":       ("work", "ide", "WorkBuddy"),
    "code.exe":            ("work", "ide", "VS Code"),
    "cursor.exe":          ("work", "ide", "Cursor"),
    "devenv.exe":          ("work", "ide", "Visual Studio"),
    "idea64.exe":          ("work", "ide", "IntelliJ IDEA"),
    "pycharm64.exe":       ("work", "ide", "PyCharm"),
    "clion64.exe":         ("work", "ide", "CLion"),
    "webstorm64.exe":      ("work", "ide", "WebStorm"),
    "rider.exe":           ("work", "ide", "Rider"),
    "studio64.exe":        ("work", "ide", "Android Studio"),
    "eclipse.exe":         ("work", "ide", "Eclipse"),
    "sublime_text.exe":    ("work", "editor", "Sublime Text"),
    "notepad++.exe":       ("work", "editor", "Notepad++"),
    "atom.exe":            ("work", "editor", "Atom"),
    "gvim.exe":            ("work", "editor", "Vim"),
    "vim.exe":             ("work", "editor", "Vim"),
    "neovide.exe":         ("work", "editor", "Neovim"),
    "postman.exe":         ("work", "api", "Postman"),
    "datagrip.exe":        ("work", "db", "DataGrip"),
    "navicat.exe":         ("work", "db", "Navicat"),
    "typora.exe":          ("work", "editor", "Typora"),
    "obsidian.exe":        ("work", "note", "Obsidian"),
    # --- 浏览器 (域名匹配另走 BROWSER_DOMAIN) ---
    "chrome.exe":          ("browser", "browser", "Chrome"),
    "msedge.exe":          ("browser", "browser", "Edge"),
    "firefox.exe":         ("browser", "browser", "Firefox"),
    "brave.exe":           ("browser", "browser", "Brave"),
    "opera.exe":           ("browser", "browser", "Opera"),
    "360se.exe":           ("browser", "browser", "360 浏览器"),
    "qqbrowser.exe":       ("browser", "browser", "QQ 浏览器"),
    # --- 通讯 ---
    "wechat.exe":          ("communication", "im", "微信"),
    "weixin.exe":          ("communication", "im", "微信"),
    "qq.exe":              ("communication", "im", "QQ"),
    "dingtalk.exe":        ("communication", "im", "钉钉"),
    "feishu.exe":          ("communication", "im", "飞书"),
    "lark.exe":            ("communication", "im", "飞书"),
    "telegram.exe":        ("communication", "im", "Telegram"),
    "discord.exe":         ("communication", "im", "Discord"),
    "slack.exe":           ("communication", "im", "Slack"),
    "mattermost.exe":      ("communication", "im", "Mattermost"),
    "element.exe":         ("communication", "im", "Element"),
    "skype.exe":           ("communication", "im", "Skype"),
    "wxwork.exe":          ("communication", "im", "企业微信"),
    # --- Ikaros 自家对话 UI (Hermes 聊天桌面) ---
    "hermes.exe":          ("communication", "ai", "Hermes"),
    "mail.exe":            ("communication", "mail", "邮件"),
    "thunderbird.exe":     ("communication", "mail", "Thunderbird"),
    # --- 娱乐 / 视频音乐 ---
    "cloudmusic.exe":      ("entertainment", "music", "网易云音乐"),
    "neteasemusic.exe":    ("entertainment", "music", "网易云音乐"),
    "qqmusic.exe":         ("entertainment", "music", "QQ 音乐"),
    "potplayermini64.exe": ("entertainment", "video", "PotPlayer"),
    "potplayer.exe":       ("entertainment", "video", "PotPlayer"),
    "vlc.exe":             ("entertainment", "video", "VLC"),
    "iqiytask.exe":        ("entertainment", "video", "爱奇艺"),
    "youku.exe":           ("entertainment", "video", "优酷"),
    "bilibili.exe":        ("entertainment", "video", "B 站"),
    "spotify.exe":         ("entertainment", "music", "Spotify"),
    # --- 游戏 (常见, 其余靠 TITLE_MAP/CJK) ---
    "steam.exe":           ("gaming", "platform", "Steam"),
    "steamwebhelper.exe":  ("gaming", "platform", "Steam"),
    "epicgameslauncher.exe": ("gaming", "platform", "Epic"),
    "yuan shen.exe":       ("gaming", "game", "原神"),
    "starrail.exe":        ("gaming", "game", "星穹铁道"),
    "zenlesszonezero.exe": ("gaming", "game", "绝区零"),
    "cs2.exe":             ("gaming", "game", "CS2"),
    "dota2.exe":           ("gaming", "game", "Dota2"),
    "leagueclient.exe":    ("gaming", "game", "英雄联盟"),
    "gamespace.exe":       ("gaming", "game", "英雄联盟"),
    "wutheringwaves.exe":  ("gaming", "game", "鸣潮"),
    "minecraft.exe":       ("gaming", "game", "Minecraft"),
    "forge.exe":           ("gaming", "game", "Minecraft"),
    "gta5.exe":            ("gaming", "game", "GTA5"),
    "cyberpunk2077.exe":   ("gaming", "game", "赛博朋克2077"),
    "eldenring.exe":       ("gaming", "game", "艾尔登法环"),
    # --- 隐私黑名单 ---
    "keepass.exe":         ("private", "password", "KeePass"),
    "keepassxc.exe":       ("private", "password", "KeePassXC"),
    "bitwarden.exe":       ("private", "password", "Bitwarden"),
    "1password.exe":       ("private", "password", "1Password"),
    "enpass.exe":          ("private", "password", "Enpass"),
    "dashlane.exe":        ("private", "password", "Dashlane"),
    # --- 自家应用 (own_app, 不算监测对象) ---
    "ikaros-desktop-pet.exe": ("own_app", "pet", "伊卡洛斯"),
}

# 浏览器进程名集合（用于 is_browser 判定 + 走域名匹配）
BROWSER_PROCESS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "360se.exe", "qqbrowser.exe", "iexplore.exe", "sogouexplorer.exe",
}

# 浏览器内域名 → (category, subcategory, canonical)
# 命中则覆盖浏览器默认类别（实现"Chrome 里开 bilibili = 娱乐"）
BROWSER_DOMAIN: dict[str, tuple[str, str, str]] = {
    "bilibili.com":      ("entertainment", "video", "B 站"),
    "b23.tv":            ("entertainment", "video", "B 站"),
    "youtube.com":       ("entertainment", "video", "YouTube"),
    "youtu.be":          ("entertainment", "video", "YouTube"),
    "netflix.com":       ("entertainment", "video", "Netflix"),
    "iqiyi.com":         ("entertainment", "video", "爱奇艺"),
    "youku.com":         ("entertainment", "video", "优酷"),
    "douyin.com":        ("entertainment", "video", "抖音"),
    "tiktok.com":        ("entertainment", "video", "TikTok"),
    "spotify.com":       ("entertainment", "music", "Spotify"),
    "music.163.com":     ("entertainment", "music", "网易云音乐"),
    "steamcommunity.com":("gaming", "platform", "Steam 社区"),
    "store.steampowered.com": ("gaming", "platform", "Steam 商店"),
    "github.com":        ("work", "dev", "GitHub"),
    "gitlab.com":        ("work", "dev", "GitLab"),
    "notion.so":         ("work", "note", "Notion"),
    "notion.site":       ("work", "note", "Notion"),
    "figma.com":         ("work", "design", "Figma"),
    "mail.google.com":   ("communication", "mail", "Gmail"),
    "outlook.live.com":  ("communication", "mail", "Outlook 邮件"),
    "web.wechat.com":    ("communication", "im", "微信网页版"),
    "discord.com":       ("communication", "im", "Discord"),
    "telegram.org":      ("communication", "im", "Telegram"),
    "feishu.cn":         ("communication", "im", "飞书"),
    "larksuite.com":     ("communication", "im", "飞书"),
    "doc.qq.com":        ("work", "office", "腾讯文档"),
    "docs.google.com":   ("work", "office", "Google Docs"),
    "chatgpt.com":       ("work", "ai", "ChatGPT"),
    "claude.ai":         ("work", "ai", "Claude"),
    "kimi.moonshot.cn":  ("work", "ai", "Kimi"),
    "tongyi.aliyun.com": ("work", "ai", "通义千问"),
    "yuanbao.tencent.com": ("work", "ai", "元宝"),
}

# 标题关键词 → (category, subcategory, canonical)
# 用于非浏览器应用的标题兜底 + 浏览器标题（无域名时）
# CJK 直接子串匹配
TITLE_MAP: list[tuple[str, tuple[str, str, str]]] = [
    # 游戏 (CJK / 英文标题)
    ("原神", ("gaming", "game", "原神")),
    ("星穹铁道", ("gaming", "game", "星穹铁道")),
    ("绝区零", ("gaming", "game", "绝区零")),
    ("鸣潮", ("gaming", "game", "鸣潮")),
    ("英雄联盟", ("gaming", "game", "英雄联盟")),
    ("王者荣耀", ("gaming", "game", "王者荣耀")),
    ("穿越火线", ("gaming", "game", "穿越火线")),
    ("和平精英", ("gaming", "game", "和平精英")),
    ("我的世界", ("gaming", "game", "Minecraft")),
    ("Minecraft", ("gaming", "game", "Minecraft")),
    ("Steam", ("gaming", "platform", "Steam")),
    ("Elden Ring", ("gaming", "game", "艾尔登法环")),
    ("Cyberpunk", ("gaming", "game", "赛博朋克2077")),
    ("GTA", ("gaming", "game", "GTA")),
    # 娱乐
    ("Bilibili", ("entertainment", "video", "B 站")),
    ("哔哩哔哩", ("entertainment", "video", "B 站")),
    ("Netflix", ("entertainment", "video", "Netflix")),
    ("YouTube", ("entertainment", "video", "YouTube")),
    ("网易云音乐", ("entertainment", "music", "网易云音乐")),
    ("QQ 音乐", ("entertainment", "music", "QQ 音乐")),
    ("爱奇艺", ("entertainment", "video", "爱奇艺")),
    ("优酷", ("entertainment", "video", "优酷")),
    ("抖音", ("entertainment", "video", "抖音")),
    # 工作 / 开发
    ("Visual Studio Code", ("work", "ide", "VS Code")),
    ("Cursor", ("work", "ide", "Cursor")),
    ("IntelliJ", ("work", "ide", "IntelliJ IDEA")),
    ("PyCharm", ("work", "ide", "PyCharm")),
    ("GitHub", ("work", "dev", "GitHub")),
    ("Figma", ("work", "design", "Figma")),
    ("Notion", ("work", "note", "Notion")),
    ("ChatGPT", ("work", "ai", "ChatGPT")),
    ("Claude", ("work", "ai", "Claude")),
    # 通讯
    ("微信", ("communication", "im", "微信")),
    ("QQ", ("communication", "im", "QQ")),
    ("钉钉", ("communication", "im", "钉钉")),
    ("飞书", ("communication", "im", "飞书")),
    ("Telegram", ("communication", "im", "Telegram")),
    ("Discord", ("communication", "im", "Discord")),
    # 隐私
    ("KeePass", ("private", "password", "KeePass")),
    ("Bitwarden", ("private", "password", "Bitwarden")),
    ("1Password", ("private", "password", "1Password")),
]

PRIVATE_PROCESS = {
    "keepass.exe", "keepassxc.exe", "bitwarden.exe", "1password.exe",
    "enpass.exe", "dashlane.exe", "vault.exe",
}
PRIVATE_TITLE = ["KeePass", "Bitwarden", "1Password", "Enpass", "密码", "银行", "网银"]

OWN_APP_PROCESS = {"ikaros-desktop-pet.exe"}

# 优先级：private > own_app > gaming > work > communication > entertainment > browser > unknown
_CATEGORY_PRIORITY = {
    "gaming": 5, "work": 4, "communication": 3,
    "entertainment": 2, "browser": 1, "unknown": 0,
    "private": 9, "own_app": 8,
}


def _match_title(title: str) -> tuple[str, str, str] | None:
    if not title:
        return None
    for kw, val in TITLE_MAP:
        if kw in title:
            return val
    return None


def classify(process_name: str | None, title: str | None, url: str | None = None) -> dict:
    """把 (进程名, 窗口标题, url) 分类为结构化类别。

    返回: {category, subcategory, canonical, is_browser}
    category ∈ {gaming, work, communication, entertainment, browser, private, own_app, unknown}
    """
    proc = (process_name or "").lower().strip()
    is_browser = proc in BROWSER_PROCESS

    # 1) 隐私黑名单（进程或标题命中）→ 最高优先
    if proc in PRIVATE_PROCESS:
        return {"category": "private", "subcategory": "password", "canonical": "密码管理器", "is_browser": False}
    if title and any(k in title for k in PRIVATE_TITLE):
        return {"category": "private", "subcategory": "sensitive", "canonical": "隐私应用", "is_browser": False}

    # 2) 自家应用
    if proc in OWN_APP_PROCESS:
        return {"category": "own_app", "subcategory": "pet", "canonical": "伊卡洛斯", "is_browser": False}

    # 2.5) 用户学习到的 override（持久化，优先级高于静态 PROCESS_MAP，但低于隐私/自家）
    if proc in _USER_PROCESS_MAP:
        c, sc, canon = _USER_PROCESS_MAP[proc]
        return {"category": c, "subcategory": sc, "canonical": canon, "is_browser": is_browser}

    # 3) 进程精确匹配
    if proc in PROCESS_MAP:
        c, sc, canon = PROCESS_MAP[proc]
        return {"category": c, "subcategory": sc, "canonical": canon, "is_browser": is_browser}

    # 4) 浏览器：先域名，后标题
    if is_browser:
        if url:
            low = url.lower()
            for dom, val in BROWSER_DOMAIN.items():
                if dom in low:
                    return {"category": val[0], "subcategory": val[1], "canonical": val[2], "is_browser": True}
        title_hit = _match_title(title)
        if title_hit:
            return {"category": title_hit[0], "subcategory": title_hit[1], "canonical": title_hit[2], "is_browser": True}
        return {"category": "browser", "subcategory": "browser", "canonical": "浏览器", "is_browser": True}

    # 5) 标题匹配（非浏览器）
    title_hit = _match_title(title)
    if title_hit:
        return {"category": title_hit[0], "subcategory": title_hit[1], "canonical": title_hit[2], "is_browser": False}

    return {"category": "unknown", "subcategory": None, "canonical": None, "is_browser": False}


def category_priority(category: str) -> int:
    return _CATEGORY_PRIORITY.get(category, 0)


if __name__ == "__main__":
    tests = [
        ("Code.exe", "main.go - ikaros - Visual Studio Code", None),
        ("chrome.exe", "原神官网 - Google Chrome", "https://www.bilibili.com/video/xxx"),
        ("WeChat.exe", "文件传输助手", None),
        ("YuanShen.exe", "原神", None),
        ("keepass.exe", "KeePassXC", None),
        ("potplayermini64.exe", "番剧.mp4 - PotPlayer", None),
        ("explorer.exe", "文档", None),
        ("code.exe", "VS Code", None),
    ]
    for p, t, u in tests:
        print(f"{p:24s} | {t[:30]:30s} | {u} -> {classify(p, t, u)}")
