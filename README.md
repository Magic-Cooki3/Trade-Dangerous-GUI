# Trade Dangerous GUI

*A lightweight, modern UI for the Trade Dangerous CLI that makes composing commands, running them, and reading results fast and friendly. It introspects the existing subcommands and options from the codebase, so it stays in sync with TD without manual updates.*

---

## Highlights

- **Command-aware UI** that reads `tradedangerous.commands` at runtime.
- **Smart forms** with required options pre-selected and positional args handled correctly.
- **Real-time command preview** with safe quoting; **Copy** and **Run** actions.
- **Output** and **Help** tabs with an inline run timer: `Running (HH:MM)` → `Finished (HH:MM)`.
- **Per-command state persistence** (selected options, values) plus **global settings** (CWD/DB/Link-Ly/verbosity).
- **Reset** button to return to first-launch defaults and clear saved preferences.
- **Update/Rebuild DB** convenience action for full database refresh via **eddblink**.
- **Dark theme**, white insertion cursor, and dynamically wrapped help text for readability.

---

## Why This Exists

Trade Dangerous is powerful but CLI-heavy. This GUI helps you:

- Discover commands and flags by browsing.  
- Avoid common syntax pitfalls (e.g., positional vs. named args).  
- Iterate quickly with a live preview and single-click **Run**.

---

## Requirements

- **Python:** 3.8.19+ (Trade Dangerous enforces this)
- **OS packages:**

| OS      | Package / Command |
|---------|--------------------|
| Linux (Debian/Ubuntu) | `sudo apt install python3-tk` |
| Linux (Fedora) | `sudo dnf install python3-tkinter` |
| macOS | Tk is included with most Python dists (or install via your Python vendor/Homebrew). |
| Windows | Tk ships with the official Python installer. |

- **Python packages** (installed via `requirements.txt`):  
  `rich`, `requests`, `ijson`, `appJar`  
  *GUI uses Tk/ttk; `requests`/`ijson` support data import plugins.*

```bash
pip install -r requirements.txt
