#!/usr/bin/env python3
import sys
import os
import threading
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    # Ensure local package import works when run from this file
    sys.path.insert(0, os.path.dirname(__file__))
    from tradedangerous import commands as td_commands
except Exception as e:
    raise SystemExit(f"Failed to import tradedangerous: {e}")

# --- GUI toolkit ---
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
from tkinter import filedialog, messagebox


# ---------- Introspection models ----------

@dataclass(eq=False)
class OptionSpec:
    args: Tuple[str, ...]
    kwargs: Dict[str, Any]
    group_id: Optional[int] = None

    @property
    def long_flag(self) -> str:
        # Prefer a long option (starts with --), else the first
        longs = [a for a in self.args if a.startswith("--")]
        return longs[0] if longs else self.args[0]

    @property
    def display_name(self) -> str:
        return self.long_flag

    @property
    def help(self) -> str:
        return self.kwargs.get("help", "")

    @property
    def action(self) -> Optional[str]:
        return self.kwargs.get("action")

    @property
    def is_flag(self) -> bool:
        return self.action == "store_true"

    @property
    def is_positional(self) -> bool:
        # Positional args in our CLI have names without leading dashes
        try:
            first = self.args[0]
        except Exception:
            return False
        return not (isinstance(first, str) and first.startswith("-"))

    @property
    def key(self) -> str:
        # Stable identifier used for saving/restoring state
        try:
            return self.kwargs.get("dest") or (self.args[0] if self.args else self.long_flag.lstrip('-'))
        except Exception:
            return self.long_flag.lstrip('-')

    @property
    def metavar(self) -> Optional[str]:
        return self.kwargs.get("metavar")

    @property
    def default(self) -> Any:
        return self.kwargs.get("default")

    @property
    def choices(self) -> Optional[List[str]]:
        return self.kwargs.get("choices")

    @property
    def dest(self) -> Optional[str]:
        return self.kwargs.get("dest")

    @property
    def multiple(self) -> bool:
        return self.action == "append"


@dataclass
class CommandMeta:
    name: str
    help: str
    arguments: List[OptionSpec] = field(default_factory=list)
    switches: List[OptionSpec] = field(default_factory=list)
    # When provided, these args are used verbatim instead of building from options
    fixed_args: Optional[List[str]] = None


def _flatten_args(items: List[Any]) -> List[OptionSpec]:
    flat: List[OptionSpec] = []
    for item in items or []:
        # MutuallyExclusiveGroup has 'arguments'
        if hasattr(item, "arguments"):
            gid = id(item)
            for sub in getattr(item, "arguments", []):
                flat.append(OptionSpec(tuple(sub.args), dict(sub.kwargs), group_id=gid))
        else:
            flat.append(OptionSpec(tuple(item.args), dict(item.kwargs)))
    return flat


def load_commands() -> Dict[str, CommandMeta]:
    metas: Dict[str, CommandMeta] = {}
    for cmd_name, module in td_commands.commandIndex.items():
        help_text = getattr(module, "help", cmd_name)
        arguments = _flatten_args(getattr(module, "arguments", []))
        switches = _flatten_args(getattr(module, "switches", []))
        metas[cmd_name] = CommandMeta(cmd_name, help_text, arguments, switches)
    # Add a convenience action for updating/rebuilding the DB via eddblink plugin
    metas["Update/Rebuild DB"] = CommandMeta(
        name="import",
        help="Convenience: import with eddblink (clean/all/skipvend/force)",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "eddblink", "-O", "clean,all,skipvend,force"],
    )
    return metas


# ---------- GUI ----------

class TdGuiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trade Dangerous – New GUI")
        self.geometry("1100x720")
        self.minsize(900, 560)
        # Suspend preference writes during first-time UI construction
        self._suspend_save = True

        # Theme colors (Dracula-like base + your palette)
        self.colors: Dict[str, str] = {
            "bg": "#282a36",           # Dracula background
            "panel": "#1f2029",        # Slightly darker panel surface
            "surface": "#1c1e26",      # Inputs / text areas
            "line": "#44475a",         # Lines / selection
            "fg": "#f8f8f2",           # Primary foreground
            "muted": "#6272a4",        # Subtle/help text
            "primary": "#7849bf",      # Provided primary
            "primaryActive": "#8a5ad7",# Active/hover primary
            "secondary": "#49a2bf",    # Provided secondary
            "secondaryActive": "#5cb3c9",
            "success": "#49bf60",      # Provided 3rd
        }

        # Apply custom dark theme styling first
        self._apply_theme()

        # Data
        self.cmd_metas = load_commands()
        self.current_meta: Optional[CommandMeta] = None
        self.widget_vars: Dict[OptionSpec, Dict[str, Any]] = {}

        # Paths
        self.repo_dir = os.path.dirname(__file__)
        self.trade_py = os.path.join(self.repo_dir, "trade.py")

        # Build UI
        self._build_topbar()
        self._build_preview_row()
        self._build_main_area()
        self._build_global_options()

        # Make all scrollable areas respond to mouse wheel
        self._install_global_mousewheel()

        # Load last-used paths (CWD/DB)
        self._load_prefs()

        # Ensure save on close
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

        # Initialize with the first command
        all_cmds = sorted(self.cmd_metas)
        if all_cmds:
            # Use restored command if available
            restore_cmd = getattr(self, "_restore_cmd", None)
            if restore_cmd in self.cmd_metas:
                self.cmd_var.set(restore_cmd)
            else:
                self.cmd_var.set(all_cmds[0])
            self._on_command_change()
            # Apply saved option states if present
            self._apply_saved_state_for_current()
        # Now allow preference writes and save the fully restored state once
        self._suspend_save = False
        try:
            self._save_prefs()
        except Exception:
            pass

    # ----- Top bar -----
    def _build_topbar(self):
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        top.columnconfigure(2, weight=1)

        ttk.Label(top, text="Command:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self.cmd_var = tk.StringVar()
        self.cmd_combo = ttk.Combobox(
            top,
            textvariable=self.cmd_var,
            values=sorted(self.cmd_metas),
            state="readonly",
            width=20,
        )
        self.cmd_combo.grid(row=0, column=1, sticky="w")
        self.cmd_combo.bind("<<ComboboxSelected>>", lambda e: self._on_command_change())
        # Spacer stretches
        ttk.Label(top, text="").grid(row=0, column=2, sticky="ew")

    # ----- Command preview -----
    def _build_preview_row(self):
        prev = ttk.Frame(self)
        prev.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 6))
        prev.columnconfigure(1, weight=1)
        # Make Copy/Run/Reset columns equal width without forcing fixed sizes
        prev.columnconfigure(2, uniform='btn')
        prev.columnconfigure(3, uniform='btn')
        prev.columnconfigure(4, uniform='btn')
        ttk.Label(prev, text="Preview:").grid(row=0, column=0, sticky="w")
        self.preview_var = tk.StringVar()
        self.preview_entry = ttk.Entry(prev, textvariable=self.preview_var)
        self.preview_entry.grid(row=0, column=1, sticky="ew", padx=(6,6))
        # Make insertion cursor white in the preview box
        try:
            self.preview_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        copy_btn = ttk.Button(prev, text="Copy", command=self._copy_preview, style="Secondary.TButton")
        copy_btn.grid(row=0, column=2, sticky="ew", padx=(0,6))
        run_btn = ttk.Button(prev, text="Run", command=self._run, style="Accent.TButton")
        run_btn.grid(row=0, column=3, sticky="ew", padx=(0,6))
        reset_btn = ttk.Button(prev, text="Reset", command=self._reset_defaults)
        reset_btn.grid(row=0, column=4, sticky="ew")

    # ----- Forms (top) + Output (bottom) -----
    def _build_main_area(self):
        # Horizontal split: left option selector, right editor+output
        self.main_pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self.main_pane.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        # Left: Option Selector (scrollable)
        left_container = ttk.Frame(self.main_pane)
        left_container.rowconfigure(0, weight=1)
        left_container.columnconfigure(0, weight=1)
        self.main_pane.add(left_container, weight=1)

        self.selector_canvas = tk.Canvas(left_container, highlightthickness=0, bg=self.colors["bg"], bd=0)
        self.selector_scroll = ttk.Scrollbar(left_container, orient=tk.VERTICAL, command=self.selector_canvas.yview)
        self.selector_frame = ttk.Frame(self.selector_canvas)
        self.selector_frame.bind(
            "<Configure>",
            lambda e: self.selector_canvas.configure(scrollregion=self.selector_canvas.bbox("all"))
        )
        self.selector_canvas.create_window((0,0), window=self.selector_frame, anchor="nw")
        self.selector_canvas.configure(yscrollcommand=self.selector_scroll.set)
        self.selector_canvas.grid(row=0, column=0, sticky="nsew")
        self.selector_scroll.grid(row=0, column=1, sticky="ns")
        # Wheel on selector area
        self._bind_mousewheel_target(self.selector_canvas)
        self._bind_mousewheel_target(self.selector_frame, target=self.selector_canvas)

        # Right: editor (top) + notebook (bottom)
        right_container = ttk.Frame(self.main_pane)
        right_container.rowconfigure(0, weight=1)
        right_container.columnconfigure(0, weight=1)
        self.main_pane.add(right_container, weight=3)

        # Vertical splitter between selected options (top) and output/help (bottom)
        self.right_split = ttk.Panedwindow(right_container, orient=tk.VERTICAL)
        self.right_split.grid(row=0, column=0, sticky="nsew")

        self.selected_frame = ttk.LabelFrame(self.right_split, text="Selected Options")
        self.selected_frame.columnconfigure(1, weight=1)
        self.selected_frame.rowconfigure(0, weight=1)

        # Make selected options scrollable as well
        self.sel_canvas = tk.Canvas(self.selected_frame, highlightthickness=0, height=200, bg=self.colors["bg"], bd=0)
        self.sel_scroll = ttk.Scrollbar(self.selected_frame, orient=tk.VERTICAL, command=self.sel_canvas.yview)
        self.sel_inner = ttk.Frame(self.sel_canvas)
        # Track help labels for dynamic wrap updates
        self._help_labels: List[ttk.Label] = []
        # Update scrollregion and help label wrap lengths on size changes
        self.sel_inner.bind("<Configure>", self._on_sel_inner_configure)
        self.sel_canvas.create_window((0,0), window=self.sel_inner, anchor="nw")
        self.sel_canvas.configure(yscrollcommand=self.sel_scroll.set)
        self.sel_canvas.grid(row=0, column=0, sticky="nsew")
        self.sel_scroll.grid(row=0, column=1, sticky="ns")
        # Wheel on selected options area
        self._bind_mousewheel_target(self.sel_canvas)
        self._bind_mousewheel_target(self.sel_inner, target=self.sel_canvas)

        # Output/Help tabs
        self.tabs = ttk.Notebook(self.right_split)
        # Output tab
        out_tab = ttk.Frame(self.tabs)
        # 0: status, 1: route cards (optional), 2: output
        out_tab.rowconfigure(0, weight=0)
        out_tab.rowconfigure(1, weight=0)
        out_tab.rowconfigure(2, weight=1)
        out_tab.columnconfigure(0, weight=1)
        # Status line above output
        self.run_status_var = tk.StringVar(value="")
        self.run_status = ttk.Label(out_tab, textvariable=self.run_status_var)
        self.run_status.grid(row=0, column=0, sticky="w", padx=4, pady=(2,2))
        # Route cards container (populated when parsing 'run' output)
        self.routes_frame = ttk.Frame(out_tab)
        self.routes_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(2,4))
        self.routes_frame.columnconfigure(0, weight=1)
        self._route_cards: List[Dict[str, Any]] = []
        # Output area
        self.output = ScrolledText(out_tab, wrap="word")
        self.output.grid(row=2, column=0, sticky="nsew")
        self._style_scrolled_text(self.output)
        self._bind_mousewheel_target(self.output)
        # Help tab
        help_tab = ttk.Frame(self.tabs)
        help_tab.rowconfigure(0, weight=1)
        help_tab.columnconfigure(0, weight=1)
        self.help_text = ScrolledText(help_tab, wrap="word", height=12)
        self.help_text.grid(row=0, column=0, sticky="nsew")
        self._style_scrolled_text(self.help_text)
        self._bind_mousewheel_target(self.help_text)
        self.tabs.add(out_tab, text="Output")
        self.tabs.add(help_tab, text="Help")
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Add top/bottom panes to the vertical splitter with weights
        try:
            self.right_split.add(self.selected_frame, weight=1)
            self.right_split.add(self.tabs, weight=2)
        except Exception:
            # Fallback if weights unsupported
            self.right_split.add(self.selected_frame)
            self.right_split.add(self.tabs)

    def _build_global_options(self):
        bottom = ttk.LabelFrame(self, text="Global Options")
        bottom.grid(row=3, column=0, sticky="ew", padx=8, pady=(0,8))
        for i in range(9):
            bottom.columnconfigure(i, weight=1 if i in (1,3,5) else 0)

        # CWD
        ttk.Label(bottom, text="CWD:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.cwd_var = tk.StringVar()
        _cwd_entry = ttk.Entry(bottom, textvariable=self.cwd_var)
        _cwd_entry.grid(row=0, column=1, sticky="ew", padx=(0,6))
        try:
            _cwd_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        ttk.Button(bottom, text="Browse...", command=self._browse_cwd).grid(row=0, column=2, sticky="e")
        # DB
        ttk.Label(bottom, text="DB:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.db_var = tk.StringVar()
        _db_entry = ttk.Entry(bottom, textvariable=self.db_var)
        _db_entry.grid(row=1, column=1, sticky="ew", padx=(0,6))
        try:
            _db_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        ttk.Button(bottom, text="Browse...", command=self._browse_db).grid(row=1, column=2, sticky="e")
        # Link-Ly
        ttk.Label(bottom, text="Link-Ly:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.linkly_var = tk.StringVar()
        _ll_entry = ttk.Entry(bottom, textvariable=self.linkly_var)
        _ll_entry.grid(row=2, column=1, sticky="ew", padx=(0,6))
        try:
            _ll_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass

        # Detail / Quiet / Debug counters on the right
        ttk.Label(bottom, text="Detail:").grid(row=0, column=6, sticky="e")
        self.detail_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.detail_var, width=3, command=self._update_preview).grid(row=0, column=7, sticky="e", padx=(0,6))
        ttk.Label(bottom, text="Quiet:").grid(row=1, column=6, sticky="e")
        self.quiet_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.quiet_var, width=3, command=self._update_preview).grid(row=1, column=7, sticky="e", padx=(0,6))
        ttk.Label(bottom, text="Debug:").grid(row=2, column=6, sticky="e")
        self.debug_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.debug_var, width=3, command=self._update_preview).grid(row=2, column=7, sticky="e", padx=(0,6))
        # Export button next to Debug
        ttk.Button(bottom, text="Export", command=self._export_output).grid(row=2, column=8, sticky="e", padx=(6,6))

        # Trace to update preview when globals change
        self.cwd_var.trace_add("write", lambda *_: self._update_preview())
        self.db_var.trace_add("write", lambda *_: self._update_preview())
        self.linkly_var.trace_add("write", lambda *_: self._update_preview())

        # Also persist on change
        self.cwd_var.trace_add("write", lambda *_: self._save_prefs())
        self.db_var.trace_add("write", lambda *_: self._save_prefs())
        self.linkly_var.trace_add("write", lambda *_: self._save_prefs())
        self.detail_var.trace_add("write", lambda *_: self._save_prefs())
        self.quiet_var.trace_add("write", lambda *_: self._save_prefs())
        self.debug_var.trace_add("write", lambda *_: self._save_prefs())

    def _clear_option_frames(self):
        # Clear selector and selected entries
        for child in self.selector_frame.winfo_children():
            child.destroy()
        for child in self.sel_inner.winfo_children():
            child.destroy()
        self.widget_vars.clear()
        # Also keep a selection map
        self._selected: Dict[OptionSpec, Dict[str, Any]] = {}

    # ----- Populate dynamic forms -----
    def _on_command_change(self):
        # Before switching, capture current command state (if any)
        try:
            prev_label = getattr(self, "_current_cmd_label", None)
            if prev_label and self.current_meta:
                self._save_state_for_label(prev_label)
        except Exception:
            pass
        name = self.cmd_var.get()
        self.current_meta = self.cmd_metas.get(name)
        self._clear_option_frames()
        if not self.current_meta:
            return

        # Build left selector groups and pre-select required args
        groups = self._categorize_current()
        row = 0
        for group_name, specs in groups:
            lf = ttk.LabelFrame(self.selector_frame, text=group_name)
            lf.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
            lf.columnconfigure(1, weight=1)
            r = 0
            for spec in specs:
                # Required args are in current_meta.arguments
                is_required = spec in self.current_meta.arguments
                var = tk.BooleanVar(value=is_required and not spec.is_flag or is_required)
                cb = ttk.Checkbutton(lf, variable=var)
                cb.grid(row=r, column=0, sticky="w")
                if is_required:
                    cb.state(["disabled"])  # Always selected
                ttk.Label(lf, text=spec.display_name).grid(row=r, column=1, sticky="w")
                # Keep ref
                self.widget_vars.setdefault(spec, {})["selected"] = var
                # Bind
                def make_cb(s=spec, v=var):
                    return lambda *_: (self._on_toggle_option(s, v.get()), self._save_prefs())
                var.trace_add("write", make_cb())
                # If pre-selected (required), add to editor panel
                if var.get():
                    self._ensure_selected_row(spec)
                r += 1
            row += 1

        # Apply saved values for this command, if any
        self._apply_saved_state_for_current()
        self._update_preview()
        self._save_prefs()
        # Track which command the UI currently represents
        self._current_cmd_label = name

    def _on_toggle_option(self, spec: OptionSpec, selected: bool):
        # enforce mutual exclusion if needed
        if selected and spec.group_id is not None:
            # Unselect other specs from the same group
            for other, vars_ in list(self.widget_vars.items()):
                if other is spec:
                    continue
                if isinstance(other, OptionSpec) and other.group_id == spec.group_id:
                    sv = vars_.get("selected")
                    if isinstance(sv, tk.BooleanVar) and sv.get():
                        sv.set(False)
                        # row will be removed in recursive call
        # Show/remove from editor
        if selected:
            self._ensure_selected_row(spec)
        else:
            self._remove_selected_row(spec)
        self._update_preview()

    def _ensure_selected_row(self, spec: OptionSpec):
        if spec in self._selected:
            return
        row = len(self._selected)
        lbl = ttk.Label(self.sel_inner, text=spec.display_name + ":")
        lbl.grid(row=row*2, column=0, sticky="w", padx=6, pady=(6,0))
        # For flags, show a checked indicator but no input
        if spec.is_flag:
            val_var = tk.BooleanVar(value=True)
            chk = ttk.Checkbutton(self.sel_inner, variable=val_var)
            chk.grid(row=row*2, column=1, sticky="w", padx=6, pady=(6,0))
            val_var.trace_add("write", lambda *_: (self._update_preview(), self._save_prefs()))
            help_lbl = None
            widgets = {"flag": val_var, "row": row}
        else:
            val_var = tk.StringVar()
            entry = ttk.Entry(self.sel_inner, textvariable=val_var)
            entry.grid(row=row*2, column=1, sticky="ew", padx=6, pady=(6,0))
            try:
                entry.configure(insertbackground=self.colors["fg"])
            except Exception:
                pass
            self.sel_inner.columnconfigure(1, weight=1)
            if spec.default not in (None, False):
                entry.insert(0, str(spec.default))
            val_var.trace_add("write", lambda *_: (self._update_preview(), self._save_prefs()))
            help_lbl = None
            if spec.help:
                help_lbl = ttk.Label(
                    self.sel_inner,
                    text=spec.help,
                    foreground=self.colors["muted"],
                    justify="left",
                    wraplength=max(300, self.sel_canvas.winfo_width() - 20 if self.sel_canvas.winfo_width() else 600),
                )
                help_lbl.grid(row=row*2+1, column=0, columnspan=2, sticky="ew", padx=6)
                self._help_labels.append(help_lbl)
            widgets = {"value": val_var, "row": row, "help": help_lbl}
        self._selected[spec] = widgets

    def _remove_selected_row(self, spec: OptionSpec):
        widgets = self._selected.pop(spec, None)
        if not widgets:
            return
        # Destroy row widgets: find widgets in the row (labels/entries)
        for w in list(self.sel_inner.grid_slaves()):
            info = w.grid_info()
            # Each row occupies two grid rows: row*2 and row*2+1
            if info.get("row") in (widgets.get("row", -1)*2, widgets.get("row", -1)*2 + 1):
                w.destroy()
        # Re-pack remaining rows
        for i, (s, wd) in enumerate(list(self._selected.items())):
            # Move to new row index i
            target_r0 = i*2
            # Find row of label of s
            for w in self.sel_inner.grid_slaves():
                inf = w.grid_info()
                if inf.get("row") == wd.get("row")*2:
                    w.grid(row=target_r0, column=inf.get("column"))
                elif inf.get("row") == wd.get("row")*2 + 1:
                    w.grid(row=target_r0+1, column=inf.get("column"))
            wd["row"] = i
        # Also purge from help label tracker
        try:
            hl = widgets.get("help")
            if hl in self._help_labels:
                self._help_labels.remove(hl)
        except Exception:
            pass
        self._save_prefs()

    # ----- Build args and preview -----
    def _build_args(self) -> List[str]:
        if not self.current_meta:
            return []
        # If this meta defines fixed args, use them verbatim
        if getattr(self.current_meta, "fixed_args", None):
            parts: List[str] = list(self.current_meta.fixed_args)  # includes subcommand
        else:
            parts: List[str] = [self.current_meta.name]

        # Selected options (right panel)
        for spec, wd in self._selected.items():
            if spec.is_flag:
                if wd.get("flag").get():
                    parts.append(spec.display_name)
            else:
                val = wd.get("value").get().strip()
                if val != "":
                    # For positional required arguments, emit only the value
                    if spec.is_positional:
                        parts.append(val)
                    # Support comma-separated values for append-type options
                    elif spec.multiple and "," in val:
                        for v in [x.strip() for x in val.split(',') if x.strip()]:
                            parts.extend([spec.display_name, v])
                    else:
                        parts.extend([spec.display_name, val])

        # Global/common switches
        # cwd (-C)
        if self.cwd_var.get().strip():
            parts.extend(["-C", self.cwd_var.get().strip()])
        # db
        if self.db_var.get().strip():
            parts.extend(["--db", self.db_var.get().strip()])
        # link-ly (-L)
        if self.linkly_var.get().strip():
            parts.extend(["-L", self.linkly_var.get().strip()])
        # detail (-v), quiet (-q), debug (-w)
        parts.extend(["-v"] * int(self.detail_var.get()))
        parts.extend(["-q"] * int(self.quiet_var.get()))
        parts.extend(["-w"] * int(self.debug_var.get()))

        return parts

    def _update_preview(self):
        args = self._build_args()
        # Render a shell-like preview
        cmd = [sys.executable, self.trade_py] + args
        def quote_double(s: str) -> str:
            s = str(s)
            if s is None:
                s = ""
            # For preview: trim leading/trailing whitespace
            s = s.strip()
            needs_quotes = (s == "" or any(ch.isspace() for ch in s) or "/" in s)
            if needs_quotes:
                return '"' + s.replace('"', '\\"') + '"'
            return s
        self.preview_var.set(" ".join(quote_double(p) for p in cmd))

    # ----- Layout helpers -----
    def _on_sel_inner_configure(self, event=None):
        # Maintain scrollregion and re-wrap help labels to available width
        try:
            self.sel_canvas.configure(scrollregion=self.sel_canvas.bbox("all"))
        except Exception:
            pass
        try:
            wrap = max(300, self.sel_canvas.winfo_width() - 20)
            for lbl in list(self._help_labels):
                try:
                    lbl.configure(wraplength=wrap)
                except Exception:
                    pass
        except Exception:
            pass

    # ----- Running the command -----
    def _run(self):
        self.output.delete("1.0", tk.END)
        self._start_timer()
        self._clear_routes()
        args = [sys.executable, self.trade_py] + self._build_args()

        def reader():
            try:
                proc = subprocess.Popen(
                    args,
                    cwd=self.repo_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1,
                    env={**os.environ, "PYTHONIOENCODING": "UTF-8"},
                )
            except Exception as e:
                self._append_output(f"Failed to start: {e}\n")
                self.after(0, self._finish_timer)
                return

            with proc.stdout:
                for line in iter(proc.stdout.readline, ''):
                    self._append_output(line)
            rc = proc.wait()
            self.after(0, self._finish_timer)
            # Post-process output to extract routes (on main thread)
            self.after(0, self._process_routes_from_output)
            # Persist the latest output for this command so it restores on tab switch
            self.after(0, self._save_prefs)

        threading.Thread(target=reader, daemon=True).start()

    def _append_output(self, text: str):
        def _append():
            self.output.insert(tk.END, text)
            self.output.see(tk.END)
        self.after(0, _append)

    def _clear_output(self):
        self.output.delete("1.0", tk.END)
    
    # ----- Routes parsing and UI -----
    def _clear_routes(self):
        for child in list(self.routes_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._route_cards = []
    
    def _strip_ansi(self, s: str) -> str:
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", s)
    
    def _parse_routes(self, text: str) -> List[Dict[str, str]]:
        import re
        # Normalize text
        text = self._strip_ansi(text)
        lines = text.splitlines()
        routes: List[Dict[str, str]] = []
        current: List[str] = []
        start_pat = re.compile(r"^\s*(.+?)\s*->\s*(.+?)(?:\s*\(score:.*)?\s*$")
        for ln in lines:
            if start_pat.match(ln):
                if current:
                    block = "\n".join(current).strip()
                    if block:
                        # Extract destination from the first line of block
                        m = start_pat.match(current[0])
                        dest_line = m.group(2).strip() if m else ""
                        routes.append({"block": block, "dest": dest_line})
                    current = []
            if ln.strip() == "":
                # keep blank lines to preserve block text, but don't accumulate leading empties
                if current:
                    current.append(ln)
                continue
            current.append(ln)
        if current:
            block = "\n".join(current).strip()
            if block:
                m = start_pat.match(current[0])
                dest_line = m.group(2).strip() if m else ""
                routes.append({"block": block, "dest": dest_line})
        return routes
    
    def _process_routes_from_output(self):
        try:
            if not self.current_meta or self.current_meta.name != 'run':
                self._clear_routes()
                return
            text = self.output.get("1.0", tk.END)
            routes = self._parse_routes(text)
            if not routes:
                self._clear_routes()
                return
            self._build_route_cards(routes)
        except Exception:
            # Don't let UI crash because of parsing issues
            self._clear_routes()
            return
    
    def _build_route_cards(self, routes: List[Dict[str, str]]):
        self._clear_routes()
        # Style for route cards
        try:
            style = ttk.Style(self)
            style.configure("RouteCard.TFrame", background=self.colors["panel"], bordercolor=self.colors["line"], relief="groove")
            style.configure("RouteCardSelected.TFrame", background=self.colors["surface"], bordercolor=self.colors["primary"], relief="solid")
            style.configure("RouteTitle.TLabel", background=self.colors["panel"], foreground=self.colors["fg"])
            style.configure("RouteBody.TLabel", background=self.colors["panel"], foreground=self.colors["muted"], wraplength=900, justify="left")
        except Exception:
            pass
        # Build cards
        for idx, rt in enumerate(routes):
            card = ttk.Frame(self.routes_frame, style="RouteCard.TFrame")
            card.grid(row=idx, column=0, sticky="ew", padx=2, pady=2)
            card.columnconfigure(0, weight=1)
            # Title (first line)
            title = rt["block"].splitlines()[0]
            lbl_title = ttk.Label(card, text=title, style="RouteTitle.TLabel")
            lbl_title.grid(row=0, column=0, sticky="w", padx=8, pady=(6,2))
            # Body (optional: show a short preview of next lines)
            body_lines = rt["block"].splitlines()[1:6]
            if body_lines:
                lbl_body = ttk.Label(card, text="\n".join(body_lines), style="RouteBody.TLabel")
                lbl_body.grid(row=1, column=0, sticky="ew", padx=8)
            # Actions row
            btn_row = ttk.Frame(card, style="RouteCard.TFrame")
            btn_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(4,8))
            btn_row.columnconfigure(0, weight=1)
            btn_row.columnconfigure(1, weight=0)
            btn_row.columnconfigure(2, weight=0)
            dest = rt.get("dest", "").strip()
            btn_copy = ttk.Button(btn_row, text="Copy Dest", command=lambda d=dest: self._copy_text(d), style="Secondary.TButton")
            btn_copy.grid(row=0, column=1, sticky="e", padx=(6,0))
            btn_swap = ttk.Button(btn_row, text="Swap to From", command=lambda d=dest: self._swap_from_to_dest(d))
            btn_swap.grid(row=0, column=2, sticky="e", padx=(6,0))
            # Click to select highlight
            def on_select(event=None, i=idx):
                self._select_route_card(i)
            for w in (card, lbl_title, btn_row):
                try:
                    w.bind("<Button-1>", on_select)
                except Exception:
                    pass
            self._route_cards.append({"frame": card, "dest": dest, "title": title})
        # Default select first
        if self._route_cards:
            self._select_route_card(0)
    
    def _select_route_card(self, index: int):
        for i, rc in enumerate(self._route_cards):
            try:
                rc["frame"].configure(style="RouteCardSelected.TFrame" if i == index else "RouteCard.TFrame")
            except Exception:
                pass
        self._selected_route_index = index
    
    def _copy_text(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass
    
    def _swap_from_to_dest(self, dest: str):
        # Ensure we're on the 'run' command
        if not self.current_meta or self.current_meta.name != 'run':
            # Switch to run command if available
            if 'run' in self.cmd_metas:
                self.cmd_var.set('run')
                self._on_command_change()
            else:
                return
        # Find the '--from' spec (dest 'starting')
        spec = None
        for s in self.current_meta.arguments + self.current_meta.switches:
            if (s.dest == 'starting') or (s.long_flag.startswith('--from')):
                spec = s
                break
        if not spec:
            return
        # Ensure selected and set value
        sel_var = self.widget_vars.get(spec, {}).get('selected')
        if sel_var is not None and not sel_var.get():
            sel_var.set(True)
            self._ensure_selected_row(spec)
        row = self._selected.get(spec)
        if row and row.get('value') is not None:
            try:
                row['value'].set(dest)
            except Exception:
                pass
        self._update_preview()
        self._save_prefs()

    # ----- Export helpers -----
    def _default_export_filename(self) -> str:
        return time.strftime("TD_%Y%m%d_%H%M%S")

    def _export_output(self):
        text = self.output.get("1.0", tk.END)
        if not text.strip():
            messagebox.showinfo("Export", "There is no output to export yet.")
            return
        # Ask for destination path and format via a standard Save dialog
        initial = self._default_export_filename()
        path = filedialog.asksaveasfilename(
            title="Export Output",
            defaultextension=".txt",
            initialfile=initial,
            filetypes=[
                ("PDF", "*.pdf"),
                ("CSV", "*.csv"),
                ("Text", "*.txt"),
                ("Flat (one line per route)", "*.flat;*.flat.txt"),
                ("Raw text", "*.raw;*.raw.txt"),
                ("All Files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".pdf"):
                self._export_pdf(self._strip_ansi(text), path)
            elif lower.endswith(".csv"):
                routes = self._parse_routes(text)
                self._export_csv(routes, path)
            elif lower.endswith(".flat") or lower.endswith(".flat.txt"):
                routes = self._parse_routes(text)
                self._export_flat(routes, path)
            elif lower.endswith(".raw") or lower.endswith(".raw.txt"):
                self._export_txt(text, path, pretty=False)
            else:
                # default to pretty text
                self._export_txt(self._strip_ansi(text), path, pretty=True)
            messagebox.showinfo("Export", f"Exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))

    def _export_txt(self, text: str, path: str, pretty: bool = True):
        header = []
        if pretty:
            header.append("Trade Dangerous GUI Export")
            header.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            header.append(f"Command: {self.preview_var.get()}")
            header.append("-" * 80)
        with open(path, "w", encoding="utf-8") as f:
            if header:
                f.write("\n".join(header) + "\n\n")
            f.write(text.rstrip() + "\n")

    def _export_flat(self, routes: List[Dict[str, str]], path: str):
        lines: List[str] = []
        lines.append(f"Trade Dangerous GUI Flat Export ({time.strftime('%Y-%m-%d %H:%M:%S')})")
        lines.append(f"Command: {self.preview_var.get()}")
        lines.append("-" * 80)
        for i, r in enumerate(routes, 1):
            title = self._strip_ansi(r.get("block", "").splitlines()[0])
            lines.append(f"{i:02d}. {title}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _export_csv(self, routes: List[Dict[str, str]], path: str):
        import csv
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "origin", "destination", "route_title", "detail_preview"]) 
            for i, r in enumerate(routes, 1):
                title = self._strip_ansi(r.get("block", "").splitlines()[0])
                parts = [p.strip() for p in title.split("->", 1)]
                origin = parts[0] if parts else ""
                dest = parts[1] if len(parts) > 1 else r.get("dest", "")
                body_lines = r.get("block", "").splitlines()[1:6]
                preview = " | ".join(self._strip_ansi(bl).strip() for bl in body_lines)
                w.writerow([i, origin, dest, title, preview])

    def _export_pdf(self, text: str, path: str):
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch
            from reportlab.pdfbase.pdfmetrics import stringWidth
        except Exception:
            raise RuntimeError("PDF export requires 'reportlab'. Install with: pip install reportlab")
        page_size = letter
        c = canvas.Canvas(path, pagesize=page_size)
        width, height = page_size
        left = 0.75 * inch
        right = width - 0.75 * inch
        top = height - 0.75 * inch
        y = top
        font_name = "Courier"
        font_size = 10
        line_h = 12
        c.setFont(font_name, font_size)
        # Header
        header = [
            "Trade Dangerous GUI Export",
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Command: {self.preview_var.get()}",
            "-" * 80,
        ]
        lines = header + text.splitlines()
        def wrap_line(s: str) -> List[str]:
            s = s.replace('\t', '    ')
            maxw = right - left
            if stringWidth(s, font_name, font_size) <= maxw:
                return [s]
            # word wrap
            out: List[str] = []
            cur = ""
            for word in s.split(" "):
                trial = (cur + (" " if cur else "") + word)
                if stringWidth(trial, font_name, font_size) <= maxw:
                    cur = trial
                else:
                    if cur:
                        out.append(cur)
                    cur = word
            if cur:
                out.append(cur)
            return out
        for raw in lines:
            for ln in wrap_line(raw):
                if y - line_h < 0.75 * inch:
                    c.showPage()
                    c.setFont(font_name, font_size)
                    y = top
                c.drawString(left, y, ln)
                y -= line_h
        c.save()

    def _show_help(self):
        # Show CLI help for the selected command into the Help tab
        if not self.current_meta:
            return
        args = [sys.executable, self.trade_py, self.current_meta.name, "-h"]

        def run_help():
            try:
                out = subprocess.check_output(
                    args,
                    cwd=self.repo_dir,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    env={**os.environ, "PYTHONIOENCODING": "UTF-8"},
                )
            except subprocess.CalledProcessError as e:
                out = e.output
            def write():
                self.help_text.delete("1.0", tk.END)
                self.help_text.insert(tk.END, out)
                self.tabs.select(1)
            self.after(0, write)

        threading.Thread(target=run_help, daemon=True).start()

    def _on_tab_changed(self, event=None):
        # Load help when Help tab is selected
        try:
            current = self.tabs.index(self.tabs.select())
        except Exception:
            return
        if current == 1:
            self._show_help()

    # ----- Run timer helpers -----
    def _format_elapsed(self, seconds: float) -> str:
        sec = max(0, int(seconds))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _tick_timer(self):
        if not getattr(self, "_timer_running", False):
            return
        elapsed = time.monotonic() - getattr(self, "_timer_start", time.monotonic())
        self.run_status_var.set(f"Running ({self._format_elapsed(elapsed)})")
        # Schedule next tick
        self._timer_job = self.after(1000, self._tick_timer)

    def _start_timer(self):
        self._timer_start = time.monotonic()
        self._timer_running = True
        # Cancel any previous scheduled job
        try:
            if getattr(self, "_timer_job", None):
                self.after_cancel(self._timer_job)
        except Exception:
            pass
        self.run_status_var.set("Running (00:00:00)")
        self._timer_job = self.after(1000, self._tick_timer)

    def _finish_timer(self):
        # Stop ticking and show final elapsed
        start = getattr(self, "_timer_start", None)
        if start is None:
            # Nothing ran; clear status
            self.run_status_var.set("Finished (00:00:00)")
            return
        self._timer_running = False
        try:
            if getattr(self, "_timer_job", None):
                self.after_cancel(self._timer_job)
        except Exception:
            pass
        elapsed = time.monotonic() - start
        self.run_status_var.set(f"Finished ({self._format_elapsed(elapsed)})")

    def _copy_preview(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.preview_var.get())
        except Exception:
            pass

    # ----- Preferences (persist CWD/DB) -----
    def _config_dir(self) -> str:
        if sys.platform.startswith('win'):
            base = os.getenv('APPDATA') or os.path.expanduser('~')
            return os.path.join(base, 'TradeDangerous')
        elif sys.platform == 'darwin':
            base = os.path.expanduser('~/Library/Application Support')
            return os.path.join(base, 'TradeDangerous')
        else:
            base = os.path.join(os.path.expanduser('~'), '.config')
            return os.path.join(base, 'TradeDangerous')

    def _prefs_path(self) -> str:
        return os.path.join(self._config_dir(), 'td_gui_prefs.json')

    def _load_prefs(self):
        try:
            p = self._prefs_path()
            if os.path.isfile(p):
                import json
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Keep raw prefs for later saves
                self._prefs = data if isinstance(data, dict) else {}
                cwd = self._prefs.get('cwd')
                db = self._prefs.get('db')
                linkly = self._prefs.get('linkly')
                detail = self._prefs.get('detail')
                quiet = self._prefs.get('quiet')
                debug = self._prefs.get('debug')
                self._restore_cmd = self._prefs.get('selected_command')
                if isinstance(cwd, str):
                    self.cwd_var.set(cwd)
                if isinstance(db, str):
                    self.db_var.set(db)
                if isinstance(linkly, str):
                    self.linkly_var.set(linkly)
                if isinstance(detail, int):
                    self.detail_var.set(detail)
                if isinstance(quiet, int):
                    self.quiet_var.set(quiet)
                if isinstance(debug, int):
                    self.debug_var.set(debug)
        except Exception:
            # Ignore preference loading errors silently
            self._prefs = {}

    def _save_prefs(self):
        if getattr(self, '_suspend_save', False):
            return
        try:
            d = self._config_dir()
            os.makedirs(d, exist_ok=True)
            import json
            # Start with previous prefs to preserve per-command states
            data = dict(getattr(self, '_prefs', {}) or {})
            # Update globals
            data.update({
                'cwd': self.cwd_var.get().strip(),
                'db': self.db_var.get().strip(),
                'linkly': self.linkly_var.get().strip(),
                'detail': int(self.detail_var.get()),
                'quiet': int(self.quiet_var.get()),
                'debug': int(self.debug_var.get()),
                'selected_command': self.cmd_var.get(),
            })
            # Update current command option states
            if self.current_meta:
                cmd_label = self.cmd_var.get()
                cmd_state = data.setdefault('commands', {}).setdefault(cmd_label, {})
                options = cmd_state.setdefault('options', {})
                for group_name, specs in self._categorize_current():
                    for spec in specs:
                        key = spec.key
                        sel_var = self.widget_vars.get(spec, {}).get('selected')
                        selected = bool(sel_var.get()) if sel_var is not None else False
                        rec = options.setdefault(key, {})
                        rec['selected'] = selected
                        if spec.is_flag:
                            wd = self._selected.get(spec)
                            rec['flag'] = bool(wd.get('flag').get()) if wd and wd.get('flag') else False
                        else:
                            wd = self._selected.get(spec)
                            rec['value'] = str(wd.get('value').get()) if wd and wd.get('value') else ''
                # Save terminal output for the active command
                try:
                    cmd_state['output'] = self.output.get("1.0", tk.END)
                except Exception:
                    pass
            self._prefs = data
            with open(self._prefs_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            # Ignore preference saving errors silently
            pass

    def _on_close(self):
        try:
            self._save_prefs()
        except Exception:
            pass
        self.destroy()

    def _apply_saved_state_for_current(self):
        """Apply saved selection and values for the current command, if available."""
        try:
            if not self.current_meta:
                return
            data = getattr(self, '_prefs', {}) or {}
            cmds = data.get('commands', {})
            state = cmds.get(self.cmd_var.get(), {})
            options = state.get('options', {})
            # Update selection states and row values
            for group_name, specs in self._categorize_current():
                for spec in specs:
                    opt = options.get(spec.key)
                    if not opt:
                        continue
                    is_required = spec in self.current_meta.arguments
                    target_sel = True if is_required else bool(opt.get('selected', False))
                    sel_var = self.widget_vars.get(spec, {}).get('selected')
                    if sel_var is not None:
                        try:
                            sel_var.set(target_sel)
                        except Exception:
                            pass
                    if target_sel:
                        self._ensure_selected_row(spec)
                        row = self._selected.get(spec)
                        if spec.is_flag:
                            try:
                                row.get('flag').set(bool(opt.get('flag', True)))
                            except Exception:
                                pass
                        else:
                            if 'value' in opt and row and row.get('value') is not None:
                                try:
                                    row.get('value').set(str(opt.get('value') or ''))
                                except Exception:
                                    pass
            # Restore saved terminal output for this command, if available
            out = state.get('output')
            if isinstance(out, str):
                try:
                    self.output.delete("1.0", tk.END)
                    self.output.insert(tk.END, out)
                    self.output.see(tk.END)
                except Exception:
                    pass
                # Rebuild route cards if this is 'run'
                if self.current_meta.name == 'run':
                    try:
                        self._process_routes_from_output()
                    except Exception:
                        pass
        except Exception:
            pass

    def _save_state_for_label(self, label: str):
        """Save the current on-screen command state under the given label without
        changing the selected command in preferences. Used when switching commands
        during the session so each command restores exactly as left.
        """
        if getattr(self, '_suspend_save', False):
            return
        if not label or not self.current_meta:
            return
        try:
            import json
            d = self._config_dir()
            os.makedirs(d, exist_ok=True)
            # Start from existing prefs
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            options = cmd_state.setdefault('options', {})
            for group_name, specs in self._categorize_current():
                for spec in specs:
                    key = spec.key
                    sel_var = self.widget_vars.get(spec, {}).get('selected')
                    selected = bool(sel_var.get()) if sel_var is not None else False
                    rec = options.setdefault(key, {})
                    rec['selected'] = selected
                    if spec.is_flag:
                        wd = self._selected.get(spec)
                        rec['flag'] = bool(wd.get('flag').get()) if wd and wd.get('flag') else False
                    else:
                        wd = self._selected.get(spec)
                        rec['value'] = str(wd.get('value').get()) if wd and wd.get('value') else ''
            # Save terminal output for this command
            try:
                cmd_state['output'] = self.output.get("1.0", tk.END)
            except Exception:
                pass
            # Keep globals and selected_command untouched here; _save_prefs will handle them
            self._prefs = data
            with open(self._prefs_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _reset_defaults(self):
        """Reset all settings to initial defaults and clear saved preferences."""
        try:
            # Remove prefs on disk
            p = self._prefs_path()
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
            self._prefs = {}
            self._restore_cmd = None
            # Reset globals
            self.cwd_var.set("")
            self.db_var.set("")
            self.linkly_var.set("")
            self.detail_var.set(0)
            self.quiet_var.set(0)
            self.debug_var.set(0)
            # Reset to initial command ordering
            all_cmds = sorted(self.cmd_metas)
            if all_cmds:
                self.cmd_var.set(all_cmds[0])
                self._on_command_change()
            self._update_preview()
            self._save_prefs()
        except Exception:
            pass

    # ----- Mouse wheel helpers -----
    def _install_global_mousewheel(self):
        # Fallback: route wheel events to nearest scrollable ancestor anywhere in the app
        self.bind_all("<MouseWheel>", self._on_global_mousewheel, add=True)
        self.bind_all("<Button-4>", self._on_global_mousewheel, add=True)  # X11 up
        self.bind_all("<Button-5>", self._on_global_mousewheel, add=True)  # X11 down

    def _bind_mousewheel_target(self, widget, target=None):
        # Bind wheel directly to a scrollable widget or route to a target (e.g., inner frame -> canvas)
        tgt = target or widget

        def _on_local_wheel(ev):
            return self._scroll_target(tgt, ev)

        widget.bind("<MouseWheel>", _on_local_wheel, add=True)
        widget.bind("<Button-4>", _on_local_wheel, add=True)
        widget.bind("<Button-5>", _on_local_wheel, add=True)

    def _on_global_mousewheel(self, ev):
        # Try to find a scrollable ancestor under the cursor and scroll it
        w = ev.widget
        # Walk up to find a widget that supports yview_scroll (Canvas/Text/Listbox/Treeview)
        visited = 0
        while w is not None and visited < 10:
            if hasattr(w, 'yview_scroll'):
                return self._scroll_target(w, ev)
            try:
                parent_path = w.winfo_parent()
                if not parent_path:
                    break
                w = w._nametowidget(parent_path)
            except Exception:
                break
            visited += 1
        return None

    def _scroll_target(self, target, ev):
        # Compute scroll direction
        delta = 0
        if hasattr(ev, 'num') and ev.num in (4, 5):
            # X11 button scroll
            delta = -1 if ev.num == 4 else 1
        else:
            # Windows and macOS: use sign of delta
            try:
                d = int(ev.delta)
            except Exception:
                d = 0
            if d != 0:
                delta = -1 if d > 0 else 1
        # Only scroll if there is overflow (content doesn't fully fit)
        try:
            if hasattr(target, 'yview'):
                first, last = target.yview()
                # If the fraction span covers the whole content, don't intercept
                if (last - first) >= 0.999:
                    return None
        except Exception:
            # If we can't determine, fall through to try scrolling
            pass

        if delta != 0:
            try:
                target.yview_scroll(delta, 'units')
                return "break"
            except Exception:
                pass
        return None

    # ----- Theming helpers -----
    def _apply_theme(self):
        c = self.colors
        # Base window and default font
        self.configure(background=c["bg"])
        try:
            default_font = tkfont.nametofont("TkDefaultFont")
            default_font.configure(size=10)
        except Exception:
            pass

        style = ttk.Style(self)
        # Use a theme that respects color configs
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Global style tweaks
        style.configure(
            ".",
            foreground=c["fg"],
            background=c["bg"],
            fieldbackground=c["surface"],
            bordercolor=c["line"],
            lightcolor=c["bg"],
            darkcolor=c["bg"],
            focuscolor=c["primary"],
        )

        # Containers / text
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabelframe", background=c["bg"], foreground=c["fg"], bordercolor=c["line"], relief="groove")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])

        # Inputs
        style.configure("TEntry", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"]) 
        try:
            style.configure("TEntry", insertcolor=c["fg"])
        except Exception:
            pass
        style.map("TEntry",
                  fieldbackground=[('focus', c["surface"])],
                  bordercolor=[('focus', c["primary"])])

        style.configure("TCombobox", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"], arrowsize=12)
        try:
            style.configure("TCombobox", insertcolor=c["fg"])
        except Exception:
            pass
        style.map("TCombobox",
                  fieldbackground=[('readonly', c["surface"])],
                  bordercolor=[('focus', c["primary"])],
                  foreground=[('disabled', c["muted"])])

        style.configure("TSpinbox", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"]) 
        style.map("TSpinbox", bordercolor=[('focus', c["primary"])])

        # Buttons
        style.configure("TButton", background=c["panel"], foreground=c["fg"], bordercolor=c["line"], focusthickness=2, focuscolor=c["primary"]) 
        style.map("TButton",
                  background=[('active', c["line"])],
                  bordercolor=[('focus', c["primary"])])

        style.configure("Accent.TButton", background=c["primary"], foreground=c["fg"], bordercolor=c["primary"], relief="flat")
        style.map("Accent.TButton", background=[('active', c["primaryActive"])])

        style.configure("Secondary.TButton", background=c["secondary"], foreground=c["fg"], bordercolor=c["secondary"], relief="flat")
        style.map("Secondary.TButton", background=[('active', c["secondaryActive"])])

        # Notebook
        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(6, 4, 6, 0))
        style.configure("TNotebook.Tab", background=c["panel"], foreground=c["fg"], padding=(12, 6), bordercolor=c["line"])
        style.map("TNotebook.Tab",
                  background=[('selected', c["surface"]), ('active', c["panel"])],
                  foreground=[('selected', c["fg"])])

        # Route card styles
        try:
            style.configure("RouteCard.TFrame", background=c["panel"], bordercolor=c["line"], relief="groove")
            style.configure("RouteCardSelected.TFrame", background=c["surface"], bordercolor=c["primary"], relief="solid")
            style.configure("RouteTitle.TLabel", background=c["panel"], foreground=c["fg"]) 
            style.configure("RouteBody.TLabel", background=c["panel"], foreground=c["muted"]) 
        except Exception:
            pass

        # Paned window / scrollbars
        style.configure("TPanedwindow", background=c["bg"], sashrelief="flat")
        style.configure("Vertical.TScrollbar", background=c["panel"], troughcolor=c["bg"], arrowcolor=c["fg"])
        style.configure("Horizontal.TScrollbar", background=c["panel"], troughcolor=c["bg"], arrowcolor=c["fg"])

        # Tk widgets option db (Text/Listbox/Scrollbar popups)
        self.option_add('*Text.background', c["surface"]) 
        self.option_add('*Text.foreground', c["fg"]) 
        self.option_add('*Text.insertBackground', c["fg"]) 
        # Entry insertion cursor color (for classic Tk widgets and some ttk themes)
        self.option_add('*Entry.insertBackground', c["fg"]) 
        self.option_add('*Text.selectBackground', c["line"]) 
        self.option_add('*Text.selectForeground', c["fg"]) 
        self.option_add('*Listbox.background', c["surface"]) 
        self.option_add('*Listbox.foreground', c["fg"]) 
        self.option_add('*Listbox.selectBackground', c["line"]) 
        self.option_add('*Listbox.selectForeground', c["fg"]) 
        self.option_add('*Scrollbar.background', c["panel"]) 
        self.option_add('*Scrollbar.activeBackground', c["panel"]) 
        self.option_add('*Scrollbar.troughColor', c["bg"]) 
        self.option_add('*Scrollbar.arrowColor', c["fg"]) 

    def _style_scrolled_text(self, widget: ScrolledText):
        c = self.colors
        try:
            widget.configure(
                bg=c["surface"],
                fg=c["fg"],
                insertbackground=c["fg"],
                highlightthickness=0,
                selectbackground=c["line"],
                selectforeground=c["fg"],
                borderwidth=0,
                relief="flat",
            )
        except Exception:
            pass

    def _browse_cwd(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.cwd_var.get() or os.getcwd())
        if d:
            self.cwd_var.set(d)

    def _browse_db(self):
        from tkinter import filedialog
        f = filedialog.askopenfilename(initialdir=os.path.expanduser("~"), filetypes=[("DB", "*.db"), ("All", "*.*")])
        if f:
            self.db_var.set(f)

    # ----- Categorization -----
    def _categorize_current(self) -> List[Tuple[str, List[OptionSpec]]]:
        if not self.current_meta:
            return []
        # Fallback: simple Required/Other
        required = list(self.current_meta.arguments)
        other = list(self.current_meta.switches)

        # Special layout for 'run'
        if self.current_meta.name == 'run':
            by_dest = {}
            for s in required + other:
                key1 = s.dest or s.long_flag.lstrip('-')
                key2 = s.long_flag.lstrip('-')
                by_dest[key1] = s
                by_dest.setdefault(key2, s)
            sects: List[Tuple[str, List[str]]] = [
                ("Required", ["capacity", "credits"]),
                ("Other", ["starting", "ending", "via", "limit", "blackMarket", "unique", "pruneScores", "shorten", "routes", "maxRoutes", "pruneHops"]),
                ("Travel", ["goalSystem", "loop", "direct", "hops", "maxJumpsPer", "maxLyPer", "emptyLyPer", "startJumps", "endJumps", "showJumps", "supply", "demand"]),
                ("Filters", ["avoid", "maxAge", "lsPenalty", "demand", "supply"]),
                ("Constraints", ["padSize", "noPlanet", "planetary", "fleet", "odyssey", "maxLs"]),
                ("Economy", ["minGainPerTon", "maxGainPerTon", "margin", "insurance"]),
                ("Display", ["checklist", "x52pro", "progress", "summary"]),
            ]
            used = set()
            result: List[Tuple[str, List[OptionSpec]]] = []
            for title, names in sects:
                specs: List[OptionSpec] = []
                for n in names:
                    s = by_dest.get(n)
                    if s and s not in used:
                        specs.append(s)
                        used.add(s)
                if specs:
                    result.append((title, specs))
            # Any remaining not categorized
            remaining = [s for s in (required + other) if s not in used]
            if remaining:
                result.append(("Misc", remaining))
            return result

        # Generic fallback
        return [("Required", required), ("Other", other)]


def main():
    app = TdGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
