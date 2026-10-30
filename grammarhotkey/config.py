"""Settings stored as JSON in %APPDATA%\\GrammarHotkey\\config.json. Selected text is never stored."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

GRAMMAR_PROMPT = (
    "Correct spelling, grammar, punctuation and diacritics only. The text can be in any language or a "
    "mix of languages: detect the language of each part and keep it, never translate. "
    "Restore missing accents and special letters (for example ą č ę ė š ž in Lithuanian, ä ö ü ß in "
    "German, é è ç in French). Keep the meaning, tone, register, slang, swearing, emoji, line breaks, paragraphs, "
    "names, URLs, e-mail addresses, numbers, code and formatting exactly as they are. Do not rewrite "
    "sentences that are already correct, do not make the text more formal and do not add anything. "
    "If nothing needs fixing, return the text unchanged."
)
PROFESSIONAL_PROMPT = (
    "Rewrite the text so that it sounds professional, clear and polite, and fix all errors. Keep the "
    "same language (never translate), the same meaning and all facts, names, numbers, URLs and code. "
    "Keep a similar length and paragraph structure. Do not add greetings or signatures that are not there."
)
REWRITE_PROMPT = (
    "Rewrite the text so that it reads naturally and clearly, and fix all errors. Keep the same language "
    "(never translate), the same meaning and tone, and all facts, names, numbers, URLs and code."
)

DEFAULT_EXCLUDED = [
    # Password managers: their text must never leave the machine.
    "nordpass.exe", "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "lastpass.exe",
    "dashlane.exe", "enpass.exe", "keeper.exe", "roboform.exe", "protonpass.exe",
    # Terminals: Ctrl+C with nothing selected would interrupt the running program.
    "windowsterminal.exe", "cmd.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "mintty.exe",
    "alacritty.exe", "wezterm-gui.exe", "putty.exe",
    # Remote desktops: the keystrokes would land on another computer.
    "mstsc.exe", "parsecd.exe", "teamviewer.exe",
]

MODELS = ["sonnet", "opus", "haiku"]
EFFORTS = ["low", "medium", "high"]


@dataclass
class Mode:
    id: str
    name: str
    hotkey: str
    prompt: str
    strict: bool = False  # strict = output must stay close to the input (grammar fixing)
    enabled: bool = True


def default_modes() -> list[Mode]:
    return [
        Mode("grammar", "Grammar Fix", "Ctrl+Alt+G", GRAMMAR_PROMPT, strict=True),
        Mode("professional", "Professional", "Ctrl+Alt+P", PROFESSIONAL_PROMPT),
        Mode("rewrite", "Rewrite", "Ctrl+Alt+R", REWRITE_PROMPT),
    ]


@dataclass
class Config:
    model: str = "sonnet"
    effort: str = "low"
    timeout_s: int = 60
    max_chars: int = 20000
    paste_delay_ms: int = 350
    claude_path: str = ""  # empty = find it automatically
    excluded: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED))
    modes: list[Mode] = field(default_factory=default_modes)
    show_success: bool = True


def config_dir() -> Path:
    override = os.environ.get("GRAMMARHOTKEY_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA") or Path.home()) / "GrammarHotkey"


def config_path() -> Path:
    return config_dir() / "config.json"


def data_dir() -> Path:
    """Holds the rotating metadata log and the empty working directory given to the CLI."""
    override = os.environ.get("GRAMMARHOTKEY_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "GrammarHotkey"


def from_dict(raw: dict) -> Config:
    cfg = Config()
    for key in ("model", "effort", "claude_path"):
        if isinstance(raw.get(key), str):
            setattr(cfg, key, raw[key])
    for key in ("timeout_s", "max_chars", "paste_delay_ms"):
        if isinstance(raw.get(key), int) and raw[key] > 0:
            setattr(cfg, key, raw[key])
    if isinstance(raw.get("show_success"), bool):
        cfg.show_success = raw["show_success"]
    if isinstance(raw.get("excluded"), list):
        cfg.excluded = [str(x).strip().lower() for x in raw["excluded"] if str(x).strip()]
    if isinstance(raw.get("modes"), list):
        modes = []
        for item in raw["modes"]:
            if isinstance(item, dict) and item.get("id") and item.get("prompt"):
                modes.append(Mode(
                    id=str(item["id"]),
                    name=str(item.get("name") or item["id"]),
                    hotkey=str(item.get("hotkey") or ""),
                    prompt=str(item["prompt"]),
                    strict=bool(item.get("strict", False)),
                    enabled=bool(item.get("enabled", True)),
                ))
        if modes:
            cfg.modes = modes
    return cfg


def load() -> Config:
    try:
        return from_dict(json.loads(config_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Config()


def save(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
