# QuickFix AI

Select text in any Windows app, press a shortcut, and the text is corrected in place. No copy-pasting into
a chat window. It runs through the [Claude Code](https://claude.com/claude-code) CLI you already have, on your
own login. There is no API key, no account and no server of ours.

Built for Lithuanian and English: restores missing letters (ą č ę ė į š ų ū ž), fixes grammar and commas, and
leaves your tone, slang, emoji, names, links and code alone.

| Shortcut | Mode | What it does |
|---|---|---|
| `Ctrl+Alt+G` | Grammar Fix | Corrects errors only, no rewriting |
| `Ctrl+Alt+P` | Professional | Makes it sound professional and polite |
| `Ctrl+Alt+R` | Rewrite | Rewrites it to read naturally |
| your own | Custom | Any instruction you write in Settings |

All shortcuts and prompts can be changed in Settings.

## Install

You need Windows 10 or 11, and a Claude plan with Claude Code.

1. Install Claude Code and log in. In a terminal:
   ```
   claude auth login
   ```
2. Download `QuickFixAI.exe` from [Releases](../../releases) and run it. No installation or admin rights needed.
   The exe is not code-signed, so Windows SmartScreen may warn you: choose *More info*, then *Run anyway*.
   It is built by a [public workflow](.github/workflows/release.yml), or you can build it yourself (below).
3. A blue **Q** appears in the tray. Double-click it for Settings, where you can turn on *Start with Windows*.

Select some text and press `Ctrl+Alt+G`. Undo with `Ctrl+Z` in that app, as with any paste.

## Speed and usage

Each correction starts a fresh `claude -p` process, so it takes a few seconds rather than being instant. Run
`python tools/check_real_cli.py` to measure it on your PC.

Requests count against your Claude plan's limits, the same as using Claude Code yourself. See Anthropic's page
on [using `claude -p` with your plan](https://support.claude.com/en/articles/15036540). If `ANTHROPIC_API_KEY`
or `ANTHROPIC_AUTH_TOKEN` is set, the CLI would bill that instead of your plan, so QuickFix AI warns you and
removes them for its own requests.

## Privacy and safety

- The selected text goes to Anthropic through your Claude Code CLI, under your account's terms. Nowhere else.
- No history and no telemetry. The local log holds only mode, model, timings and error type, never text.
- Shortcuts use Windows `RegisterHotKey`. There is no keyboard hook and no keylogging.
- The CLI runs with no tools, no MCP servers, no saved session, no CLAUDE.md or settings, in an empty folder, and
  never with `--dangerously-skip-permissions`. Text goes over stdin, never on a command line or through a shell.
- Text inside your selection is treated as data, and the model has no tools to act with anyway. The worst a
  trick sentence can do is produce a bad correction, which the checks below catch or `Ctrl+Z` undoes.
- Excluded by default: password managers (privacy), terminals (Ctrl+C would stop the running program) and remote
  desktops (the keys would land on the other computer). Edit the list in Settings.

**Your text is never lost.** Before pasting, the app checks that the same window is active, that the clipboard was
not changed and that the same text is still selected. It also refuses empty results, a wildly different length,
a chat-style reply, or (in Grammar Fix) a lost link or emoji. If anything looks wrong, you get a window with
the result and your original, and nothing is pasted. Your clipboard is put back afterwards (text, formatted text,
pictures, files).

## Limitations

- **Formatting:** the result is pasted as plain text, so bold, links and fonts inside the selection are lost in
  Word, Gmail and similar editors.
- **Apps that copy a whole line** when nothing is selected (VS Code, for example) will treat that line as the
  selection.
- **Apps running as administrator** can't receive the keys from a normal app, so nothing happens. Windows blocks this.
- **Games and some custom text fields** ignore simulated Ctrl+C / Ctrl+V.
- **Clipboard history:** your app's own copy of the selection can briefly appear in Win+V history, because Windows
  records it before QuickFix AI can act. The corrected text and the restored clipboard are marked to stay out of it.
- **Ctrl+Alt is the same as AltGr** on many keyboards. The defaults G, P and R don't type anything with AltGr on
  the Lithuanian layouts we checked, but digits do (AltGr+1 is `@` on Lithuanian Standard). Settings warns about
  conflicts with the layouts you have installed.
- **Not tested** in WhatsApp, Discord, Slack, Word or Outlook. They should work like any Ctrl+C / Ctrl+V app, and
  reports are welcome.
- Windows only.

## Build from source

```
build.bat
```

This runs the tests and writes `dist\QuickFixAI.exe`. To run without building: `python run.pyw` after
`pip install -r requirements.txt`.

Tests:

```
python -m pytest tests                    # logic, no quota
python -X utf8 tests/e2e_win.py           # real app + Notepad + Chrome on your desktop, with a fake CLI
python -X utf8 tests/e2e_win.py --real    # the same with real Claude, uses your quota
```

The desktop tests take over keyboard focus while they run.

## Notice

An independent project, not made by or affiliated with Anthropic. "Claude" is a trademark of Anthropic. Each
user signs in to the official, unmodified Claude Code CLI themselves. QuickFix AI never sees, stores or forwards
credentials. Your use stays subject to [Anthropic's terms](https://code.claude.com/docs/en/legal-and-compliance),
which say that plan limits assume ordinary, individual use. Anthropic can change these rules.

License: [MIT](LICENSE).

---

## Lietuviškai

Pažymi tekstą bet kurioje Windows programoje, paspaudi `Ctrl+Alt+G`, ir tekstas pataisomas toje pačioje vietoje.
Naudoja tavo paties Claude Code CLI prisijungimą: jokių API raktų, jokių mūsų serverių.

1. Įsidiek Claude Code ir terminale paleisk `claude auth login`.
2. Iš [Releases](../../releases) atsisiųsk `QuickFixAI.exe` ir paleisk. Jei Windows perspės: *More info* → *Run anyway*.
3. Dėkle atsiras mėlyna **Q**. Dukart spustelėjus atsidaro nustatymai.

Vienas taisymas užtrunka kelias sekundes ir skaičiuojamas į tavo Claude plano limitus. Tekstas siunčiamas tik
Anthropic per tavo CLI, istorija nesaugoma. Jei įklijuoti nesaugu, atsidaro langas su rezultatu, o tavo tekstas lieka
nepaliestas. Atšaukti galima su `Ctrl+Z`.
