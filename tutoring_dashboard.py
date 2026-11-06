# Imports
import os
import webbrowser

# When the program is asked for another CSV, it re-runs itself as a "child" that builds the page quietly (no banner, no
# prompts, no new browser tab) while the first window stays open and tells the open page to refresh.
_child_run = bool(os.environ.get("TUTORING_DASH_CHILD"))
# Web mode: the phone app runs this same file inside the browser (Pyodide). No keyboard, threads, helper server or
# extra windows there; progress goes to a callback and only the phone page is built.
_web = bool(os.environ.get("TUTORING_WEB"))
_web_progress = None            # the web page's function(fraction, label), picked up below
if _web:
    try:
        import js as _js
        _web_progress = getattr(_js, "tutoringProgress", None)
    except ImportError:
        pass

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.io import to_html
from plotly.subplots import make_subplots
from statsmodels.tsa.seasonal import STL

### ----- Print instructions and wait for csv ----- ###

# -----------------------------------------
# PRINT INSTRUCTIONS
# -----------------------------------------
logo_text = r"""
           ╭─────────╮
           |         |
           |_       _|
            /       \
           /         \
          /           \
         /     / \     \
        /     /   \     \
       /     /     \     \
      /      ‾‾‾‾‾‾‾      \
     /    MOUNTAINEERS     \
    /      ___________      \
   /      /           \      \
 _/      /_           _\      \_
|          |         |          |
|          |         |          |
╰──────────╯         ╰──────────╯
"""

GOLD = "\033[38;5;220m"
WHITE = "\033[97m"
RESET = "\033[0m"

GAP = " " * 4  # space between logo and instructions box
LOGO_SHIFT = " "

# Instructions data structure
steps_data = [
    (
        "1",
        [
            "In TracCloud, select: Reports → Students → Visits by ??",
            "Deselect 'All Centers' and select ONLY:",
            "  • UTS Drop-in",
            "  • Drop-in (CS)",
            "  • Appointment based",
        ],
    ),
    ("2", ["Set time frame to: This Active Term"]),
    (
        "3",
        [
            "Under 'Additional Fields to Show', include ONLY:",
            "  • Student Name",
            "  • Center",
            "  • Consultant Name",
            "  • Subject / Course / Session",
        ],
    ),
    (
        "4",
        [
            "When prompted, enter the file path to your CSV.",
            "(Tip: Drag and drop the CSV into this terminal window.)",
        ],
    ),
]

# Build table lines with exact pixel-perfect width alignment
TITLE = "TUTORING CENTER DATA DASHBOARD 2.0"
col1_w = 6
col2_w = 58
inner_w = col1_w + col2_w + 1  # 65 chars inner width = 67 total line width

box_lines = []
box_lines.append(f"╭{'─' * inner_w}╮")
box_lines.append(f"│{TITLE.center(inner_w)}│")
box_lines.append(f"├{'─' * col1_w}┬{'─' * col2_w}┤")

for step_idx, (step_num, lines) in enumerate(steps_data):
  for idx, line_text in enumerate(lines):
    num_str = f"[{step_num}]" if idx == 0 else ""
    box_lines.append(f"│ {num_str:^{col1_w - 2}} │ {line_text:<{col2_w - 2}} │")
  if step_idx < len(steps_data) - 1:
    box_lines.append(f"├{'─' * col1_w}┼{'─' * col2_w}┤")

box_lines.append(f"╰{'─' * col1_w}┴{'─' * col2_w}╯")

# Format and align side-by-side with the Unicode logo
logo_lines = logo_text.strip("\n").split("\n")
logo_width = max(len(l) for l in logo_lines)
blank_logo_row = " " * (logo_width + len(LOGO_SHIFT))

logo_rows = []
for line in logo_lines:
  line = LOGO_SHIFT + line.ljust(logo_width)
  if "MOUNTAINEERS" in line:
    before, after = line.split("MOUNTAINEERS")
    logo_rows.append(f"{GOLD}{before}{WHITE}MOUNTAINEERS{GOLD}{after}{RESET}")
  else:
    logo_rows.append(f"{GOLD}{line}{RESET}")

top_offset = max(0, (len(box_lines) - len(logo_rows)) // 2)

for i, box_line in enumerate(box_lines):
  j = i - top_offset
  left = logo_rows[j] if 0 <= j < len(logo_rows) else blank_logo_row
  if not _child_run:
    print(left + GAP + box_line)

for _ in range(3):
  if not _child_run:
    print()


# =========================================================
# LOADING LINE  (one line that updates in place while the dashboard is built)
# =========================================================
class Loader:
    """A single self-updating line: spinner, stage name, progress bar, percent. Falls back to quiet plain text when the
    output is not a terminal (so redirected output and tests stay clean)."""
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    WIDTH = 26
    LABEL_W = 34

    def __init__(self):
        import sys, threading
        self.live = sys.stdout.isatty() and not _web
        self.target, self.shown, self.ceiling = 0.0, 0.0, 0.0
        self.label = "Starting"
        self.running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        import atexit, os, sys, threading
        if self.running:
            return
        self.running = True
        if not self.live:
            return
        if os.name == "nt":
            os.system("")                         # switches on colour/cursor codes in older Windows consoles
        sys.stdout.write("\033[?25l")             # hide the cursor while animating
        atexit.register(self.stop)
        prev = sys.excepthook
        def hook(*a):
            self.stop()
            prev(*a)
        sys.excepthook = hook
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def step(self, fraction, label=None, ceiling=None):
        """Move the bar toward `fraction` (0-1). `ceiling` lets it creep slowly while a long step runs."""
        with self._lock:
            self.target = max(self.target, min(1.0, fraction))
            if ceiling is not None:
                self.ceiling = ceiling
            if label:
                self.label = label
        if _web and _web_progress is not None:
            try:
                _web_progress(self.target, self.label)
            except Exception:
                pass

    def _render(self, tick):
        import shutil
        gold, white, dim = "\033[38;5;220m", "\033[97m", "\033[38;5;240m"
        n = self.WIDTH
        filled = int(round(self.shown * n))
        glint = (tick // 2) % (n + 8) - 4                    # a bright spot that sweeps along the filled part
        bar = ""
        for i in range(n):
            if i < filled:
                bar += (white if abs(i - glint) <= 1 else gold) + "█"
            elif i == filled and filled < n:
                bar += gold + "▓" if tick % 6 < 3 else gold + "▒"
            else:
                bar += dim + "░"
        spin = self.SPIN[tick % len(self.SPIN)]
        dots = "" if self.label == "Done" else "." * (tick // 5 % 4)
        text = f"{self.label}{dots}".ljust(self.LABEL_W)
        line = f" {gold}{spin}{white} {text}{gold}▕{bar}{gold}▏{white} {int(self.shown * 100):3d}%{RESET}"
        return line

    def _loop(self):
        import sys, time
        tick = 0
        while self.running:
            with self._lock:
                gap = self.target - self.shown
                if gap > 0.002:
                    self.shown += gap * 0.18
                elif self.ceiling > self.shown + 0.01:       # long step: keep inching forward so it never looks frozen
                    self.shown += 0.0012
                self.shown = min(self.shown, 1.0 if self.label == "Done" else 0.99)
                line = self._render(tick)
            sys.stdout.write("\r\033[2K" + line)
            sys.stdout.flush()
            tick += 1
            time.sleep(0.07)

    def stop(self):
        """Stop animating and clear the line (safe to call more than once)."""
        import sys
        if not self.running:
            return
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        if self.live:
            sys.stdout.write("\r\033[2K\033[?25h")
            sys.stdout.flush()

    def finish(self):
        """Fill the bar for a moment, then clear the line so the final message prints cleanly."""
        import time
        if self.running and self.live:
            self.step(1.0, "Done")
            self.ceiling = 0
            with self._lock:
                self.shown = 1.0
            time.sleep(0.6)
        self.stop()


LOADER = Loader()


# =========================================================
# SETTINGS  (edit these if the center's schedule changes)
# =========================================================
import sys
import warnings
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.forecasting.stl import STLForecast
from statsmodels.tsa.stattools import kpss

warnings.simplefilter("ignore")        # keep the terminal clean (statsmodels is chatty)

OPEN_WEEKDAYS = [0, 1, 2, 3]        # Monday-Thursday (closed Fridays). Monday = 0
WEEKDAY_NAMES = [["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i] for i in OPEN_WEEKDAYS]
TERM_START = "2026-08-24"           # first day of the term (visits before this are left out of trend plots)
END_DATE = None                     # None = last day that has data (never later than today)
STL_PERIOD = 4                      # one "season" = one open week (Mon-Thu)
MIN_VISITS_FOR_STL = 10             # fewer total visits than this -> "Too few visits" instead of a label
TREND_CUTS = (0.3, 0.6)             # strength below 0.3 = Weak, 0.3-0.6 = Moderate, above 0.6 = Strong
DIRECTION_THRESHOLD = 0.15          # trend must move more than 15% of the average day to count as Rising/Falling
FORECAST_DAYS = 12                  # forecast the next 12 open days = three open weeks (Mon-Thu)
MIN_DAYS_FOR_FORECAST = 24          # need at least this many open days of data to forecast
CLOSED_DATES = []                   # days the center is closed that fall on a normal open weekday, e.g. ["2026-11-25", "2026-11-26"]
CV_INIT = 16                        # accuracy test starts forecasting after this many open days
MIN_GAPS_RELIABLE = 5               # time-between-visits bars with fewer gaps than this are shown faded
CRLA_HOURS = 25

# ---- Return rate (does a student come back after their first visit?) ----
# Dates when school is in (month, day), the same every year, with one day of padding on each side (Fall: Aug 24 - Dec 2,
# Spring: Jan 18 - Apr 28). Visits outside these dates are ignored.
TERM_DATES = {"Fall": ((8, 23), (12, 3)), "Spring": ((1, 17), (4, 29))}
HISTORY_FILE = "history.csv"        # one big "Visits by ??" export with several years of data (any dates; extras are ignored)
RETURN_CONF = 0.60                  # confidence level for the 'likely range' and 'expected range' on the Return Rate tab

# =========================================================
# APP STATE THEME  (official brand colors and fonts)
# =========================================================
AS_GOLD = "#FFCC00"
AS_BLACK = "#010101"
AS_LGRAY = "#d6d6d7"
AS_DGRAY = "#4c4847"
AS_STONE = "#d5c99a"
AS_GRASS = "#69aa61"
AS_BRICK = "#c6602a"
AS_ROOF = "#537a6b"
AS_DGOLD = "#d7a527"
AS_LAKE = "#03659c"

FONT_BODY = "Montserrat, Arial, sans-serif"            # Heading = SemiBold (600), everything else = Medium (500)
FONT_TITLE = "Archivo Black, Arial Black, sans-serif"  # page title and subtitle
GOOGLE_FONTS = "https://fonts.googleapis.com/css2?family=Archivo+Black&family=Montserrat:wght@500;600&display=swap"

# Gold is too light to read as a line or bar on white, so data uses Lake Blue / Brick Orange / Dark Gold and
# gold is used for the page, highlights and markers. These data colors were checked for colorblind safety.
C_DROPIN = "#636EFA"      # original blue (drop-in bars)
C_APPT = "#EF553B"        # original red (appointment bars)
CENTER_COLORS = {"Drop-in": C_DROPIN, "Appointment": C_APPT}
C_INK = AS_BLACK        # actual-visit lines
C_CUM = AS_LAKE         # running-total line
C_GRAY = AS_DGRAY
GOLD_RAMP = [[0.0, "#FFF8DC"], [0.2, "#FFE680"], [0.4, "#FFCC00"], [0.6, "#D7A527"], [0.8, "#8A5A12"], [1.0, "#3B2A0A"]]


def table_header(values, size=13):
    return dict(values=values, fill_color=AS_BLACK, line_color=AS_BLACK, align="center",
                font=dict(family=FONT_BODY, color=AS_GOLD, size=size))


def table_cells(values, size=13, fill="white", height=26):
    return dict(values=values, fill_color=fill, line_color=AS_LGRAY, align="center", height=height,
                font=dict(family=FONT_BODY, color=AS_BLACK, size=size))


def apply_theme(fig):
    """Fonts, colors and hover for one figure. Hover works across the whole plot: moving the cursor anywhere
    shows every series for that day/category plus a guide line (heat maps already hover per cell)."""
    traces = [t for t in fig.data if not isinstance(t, go.Table)]
    has_heat = any(isinstance(t, go.Heatmap) for t in traces)
    horizontal = any(isinstance(t, go.Bar) and t.orientation == "h" for t in traces)
    fig.update_layout(
        font=dict(family=FONT_BODY, size=13, color=AS_DGRAY),
        title_font=dict(family=FONT_BODY, size=22, color=AS_BLACK, weight=600),
        paper_bgcolor="white", plot_bgcolor="white",
        hoverlabel=dict(bgcolor="white", bordercolor=AS_BLACK, font=dict(family=FONT_BODY, size=13, color=AS_BLACK)))
    fig.update_xaxes(gridcolor="#ececec", linecolor=AS_LGRAY, zerolinecolor=AS_LGRAY)
    fig.update_yaxes(gridcolor="#ececec", linecolor=AS_LGRAY, zerolinecolor=AS_LGRAY)
    if not traces:
        return fig
    # hoverdistance = -1 means "no cutoff": the tooltip and guide line appear wherever the cursor is in the plot and
    # snap to the nearest day/category. That is only honest when every series sits on the same x (or y) positions.
    # If series cover different ranges (past data vs a forecast, or a single "today" marker), a no-cutoff hover would
    # pull the nearest point of a far-away series into the tooltip, so those plots use a bounded distance instead
    # (wider than the gap between neighbouring points, so there are still no dead zones inside the data).
    def shared_positions(axis):
        seen = set()
        for t in traces:
            if t.hoverinfo == "skip" or getattr(t, axis, None) is None:
                continue
            seen.add(tuple(str(v) for v in getattr(t, axis)))
        return len(seen) <= 1

    spike = dict(showspikes=True, spikemode="across", spikesnap="data", spikecolor=AS_DGRAY,
                 spikethickness=1, spikedash="dot")
    only_bars = all(isinstance(t, go.Bar) or t.hoverinfo == "skip" for t in traces) and any(isinstance(t, go.Bar) for t in traces)
    if only_bars and not has_heat:
        # Plots made only of bars (consultants, weekday averages, days between visits...): hovering a bar shows that bar's
        # own name and value, instead of listing every bar at once.
        for t in [t for t in traces if isinstance(t, go.Bar)]:
            cat = "%{y}" if t.orientation == "h" else "%{x}"
            if t.hovertemplate and cat not in t.hovertemplate:
                t.hovertemplate = f"<b>{cat}</b><br>" + t.hovertemplate
        fig.update_layout(hovermode="closest")
    elif has_heat:
        fig.update_layout(hovermode="closest")          # every heat map cell already fills the plot
    elif horizontal:
        dist = -1 if shared_positions("y") else 40
        fig.update_layout(hovermode="y unified", hoverdistance=dist, spikedistance=dist)
        fig.update_yaxes(**spike)
    else:
        dist = -1 if shared_positions("x") else 40
        fig.update_layout(hovermode="x unified", hoverdistance=dist, spikedistance=dist)
        fig.update_xaxes(**spike)
        helper = fig.to_plotly_json()["layout"].get("xaxis2", {})
        if helper.get("overlaying") == "x":              # hidden helper axis used for the CRLA line
            fig.update_layout(xaxis2=dict(showspikes=False))
    return fig

GROUP_MAPPING = {
    "Math Stats": ["MAT", "STT", "WRC"],
    "Physics": ["PHY"],
    "Business Accounting Economics": ["ACC", "FIN", "ECO", "BUS", "BSC"],
    "Computer Science": ["C S"],
    "Specialized": ["ASL", "BIO", "PSY", "SOC", "SNH", "CHE", "Non"],
}
DISPLAY = {"C S": "CS", "Non": "Non-course"}


def disp(prefix):
    return DISPLAY.get(prefix, prefix)


# =========================================================
# SHARED DASHBOARD BUILDER  (the page shell used by both report types)
# =========================================================
_figstore = []       # (desktop html, phone html) for every figure; swapped into each page when it is written


def phone_copy(fig):
    """A restyled copy of a figure for narrow screens. Returns (figure, minimum width in px or 0)."""
    m = go.Figure(fig)
    kinds = {t.type for t in m.data}
    if "table" in kinds:                                # tables keep their size and scroll sideways
        m.update_layout(dragmode=False, title=dict(x=0.02, xanchor="left", font=dict(size=16)))
        return m, 760
    h = fig.layout.height or 450
    old_b = (fig.layout.margin.b or 0) if fig.layout.margin else 0
    has_legend = fig.layout.showlegend is not False and len(m.data) > 1
    m.update_layout(height=int(min(max(h * 0.85, 340), 640)), font=dict(size=11), dragmode=False,
                    title=dict(x=0.02, xanchor="left", font=dict(size=15)),
                    margin=dict(l=46, r=14, t=70, b=max(old_b, 140 if has_legend else 60)))
    if has_legend:
        m.update_layout(legend=dict(orientation="h", x=0, xanchor="left", y=-0.28, yanchor="top", font=dict(size=10)))
    if "heatmap" in kinds:
        m.update_layout(margin=dict(l=70, r=14, t=70, b=60))
        return m, 560                                   # many hour columns: scroll sideways instead of squashing
    return m, 0


def fig_html(fig, pk=None):
    """pk marks the plots that go into the print/PDF report."""
    apply_theme(fig)
    h = int(fig.layout.height or 450)
    cfg = {"responsive": True, "displaylogo": False}
    desk = to_html(fig, full_html=False, include_plotlyjs=False, config=cfg)
    pm, minw = phone_copy(fig)
    mob = to_html(pm, full_html=False, include_plotlyjs=False, config=dict(cfg, displayModeBar=False, scrollZoom=False))
    mob = mob.replace("<script>", '<script type="text/lazy">')     # drawn when the tab is opened (phones are slow)
    if minw:
        mob = ('<div style="font-size:11px;color:#4c4847;text-align:right;padding:2px 8px">swipe sideways to see all of it &rarr;</div>'
               f'<div style="min-width:{minw}px">{mob}</div>')
    _figstore.append((desk, mob))
    attr = f' data-pk="{pk}" data-h="{h}"' if pk and len(fig.data) else ""      # "no data" note plots stay out of the report
    return f'<div class="chart"{attr}>@@FIG{len(_figstore) - 1}@@</div>'


def para(text, cls="note"):
    return f'<p class="{cls}">{text}</p>' if text else ""


def subtabs(group, items):
    buttons = "".join(f'<button class="tabbtn sub" data-group="{group}" onclick="showTab(\'{group}\',\'{group}-{i}\',this)">{lbl}</button>'
                      for i, (lbl, _) in enumerate(items))
    panes = "".join(f'<div class="pane" data-group="{group}" id="{group}-{i}">{h}</div>' for i, (_, h) in enumerate(items))
    return f'<div class="tabs sub">{buttons}</div>{panes}'


PAGE_TEMPLATE = """<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>SLC Data Dashboard</title>
<link rel="icon" type="image/png" href="appLogo.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="__FONTS__" rel="stylesheet"><style>
body{font-family:Montserrat,Arial,sans-serif;font-weight:500;margin:0;background:#f2f2f1;color:#010101}
.hero{background:#010101;border-bottom:6px solid #FFCC00;text-align:center;padding:22px 16px 16px}
.hero h1{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;color:#FFCC00;font-size:30px;
  letter-spacing:.5px;margin:0;text-transform:uppercase}
.hero .sub-title{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;color:#fff;font-size:13px;
  letter-spacing:1px;margin-top:8px}
.tabs{display:flex;justify-content:center;flex-wrap:wrap;background:#4c4847;padding:6px 8px}
.tabs.sub{background:#d6d6d7}
.tabbtn{background:transparent;color:#fff;border:none;cursor:pointer;padding:9px 18px;margin:3px;
  font:600 14px Montserrat,Arial,sans-serif;border-radius:4px}
.tabbtn:hover{background:rgba(255,204,0,.28)}
.tabbtn.active{background:#FFCC00;color:#010101}
.tabbtn.sub{color:#010101;background:#fff;font-size:13px;padding:7px 14px}
.tabbtn.sub:hover{background:#FFE680}
.tabbtn.sub.active{background:#010101;color:#FFCC00}
.pane{display:none;padding:14px 16px}.pane.on{display:block}
.chart{background:#fff;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.14);margin:0 auto 18px;padding:4px 6px;max-width:1400px}
.note{max-width:1000px;margin:6px auto 22px;color:#4c4847;font-size:13px;line-height:1.6;border-left:4px solid #FFCC00;padding:2px 0 2px 14px}
.cards{display:flex;justify-content:center;flex-wrap:wrap;gap:14px;margin:6px 0 18px}
.card{background:#fff;border-radius:6px;border-top:5px solid #FFCC00;box-shadow:0 1px 6px rgba(0,0,0,.14);
  padding:14px 24px;text-align:center;min-width:180px;font-size:12px;color:#4c4847}
.card .big{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;font-size:30px;color:#010101;margin-bottom:2px}
.footer{max-width:1000px;margin:24px auto 40px;color:#4c4847;font-size:12px;line-height:1.5;padding:0 16px}
.footer b{font-weight:600;color:#010101}
.takeaways{max-width:1000px;margin:6px auto 22px;background:#fff;border-radius:6px;border-left:6px solid #FFCC00;
  box-shadow:0 1px 6px rgba(0,0,0,.14);padding:12px 22px 14px;font-size:14px;line-height:1.65}
.takeaways h3{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;font-size:16px;margin:4px 0 6px}
.takeaways ul{margin:0;padding-left:20px}.takeaways li{margin:5px 0}
.cards#statcards{flex-wrap:nowrap;max-width:1400px;margin-left:auto;margin-right:auto}
.cards#statcards .card{flex:1 1 0;min-width:0;padding:14px 12px}
.printbar{text-align:center;margin:18px 0 10px}
.printbtn{background:#FFCC00;color:#010101;border:none;cursor:pointer;padding:9px 20px;border-radius:4px;
  font:600 14px Montserrat,Arial,sans-serif}.printbtn:hover{background:#FFE680}
#printreport{display:none}
@media print{
  @page{margin:0.45in}
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body > *:not(#printreport){display:none !important}
  #printreport{display:block !important;font-family:Montserrat,Arial,sans-serif}
  #printreport .rhead{background:#010101;border-bottom:5px solid #FFCC00;text-align:center;padding:14px 10px 10px;margin-bottom:12px}
  #printreport .rhead h1{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;color:#FFCC00;font-size:22px;margin:0;text-transform:uppercase}
  #printreport .rhead div{color:#fff;font-size:11px;margin-top:5px}
  #printreport .cards{margin:0 0 12px}#printreport .card{box-shadow:none;border:1px solid #ddd;border-top:4px solid #FFCC00;padding:8px 14px;min-width:120px}
  #printreport .card .big{font-size:22px}
  #printreport .takeaways{box-shadow:none;border:1px solid #ddd;border-left:6px solid #FFCC00;margin:0 0 12px;max-width:none;font-size:12px;break-inside:avoid}
  #printreport figure{margin:0 0 14px;break-inside:avoid;page-break-inside:avoid}
  #printreport figure img{width:100%;display:block}
  #printreport .rnote{font-size:10px;color:#4c4847;margin-top:10px}
}
</style><script>
function showTab(group,id,btn){
  document.querySelectorAll('.pane[data-group="'+group+'"]').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tabbtn[data-group="'+group+'"]').forEach(b=>b.classList.remove('active'));
  var pane=document.getElementById(id);pane.classList.add('on');btn.classList.add('active');
  resizeIn(pane);
}
function resizeIn(root){
  (root||document).querySelectorAll('.js-plotly-plot').forEach(function(p){
    if(p.offsetParent!==null){Plotly.Plots.resize(p);}
  });
}
/* Plotly measures legend/label widths once and remembers them per font name. If that happened before the web font
   arrived, legend text gets clipped. Changing the font name after the fonts load forces a fresh measurement. */
function refitFonts(){
  var fam='Montserrat, Arial, Helvetica, sans-serif';
  document.querySelectorAll('.js-plotly-plot').forEach(function(p){
    Promise.resolve(Plotly.relayout(p,{'font.family':fam,'title.font.family':fam})).then(function(){
      if(p.offsetParent!==null){Plotly.Plots.resize(p);}
    });
  });
}
window.onload=function(){
  var bars=document.querySelectorAll('.tabs');
  for(var i=bars.length-1;i>=0;i--){var b=bars[i].querySelector('button');if(b){b.click();}}
  if(document.fonts&&document.fonts.load){
    Promise.all([document.fonts.load('500 13px Montserrat'),document.fonts.load('600 14px Montserrat'),
                 document.fonts.load('30px "Archivo Black"')]).then(refitFonts).catch(function(){});
  }
};
</script></head><body>
<script>__PLOTLYJS__</script>
<div class="hero"><h1>__H1__</h1>
<div class="sub-title">__SUB__</div></div>
<div class="tabs">__TOP__</div>__PANES__
<div class="footer"><b>About this data</b>__NOTES__</div>
<div id="printreport"></div>
</body></html>"""


MOBILE_CSS = """
body{-webkit-text-size-adjust:100%}
.hero{padding:14px 10px 10px;border-bottom-width:5px}.hero h1{font-size:20px}.hero .sub-title{font-size:10px;margin-top:6px}
.tabs{flex-wrap:nowrap;overflow-x:auto;justify-content:flex-start;-webkit-overflow-scrolling:touch;padding:4px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabbtn{white-space:nowrap;padding:8px 12px;font-size:13px;flex:0 0 auto;margin:2px}
.tabbtn.sub{font-size:12px;padding:6px 10px}
.pane{padding:8px 6px}
.chart{padding:2px;margin:0 0 12px;overflow-x:auto;-webkit-overflow-scrolling:touch}
.note{font-size:12px;margin:4px 4px 16px;padding-left:10px}
.cards{gap:8px;margin:4px 0 12px}.card{min-width:0 !important;flex:1 1 42%;padding:10px 8px}.card .big{font-size:22px}
.cards#statcards{flex-wrap:wrap}.cards#statcards .card{flex:1 1 42%}
#jsnote{margin:10px;padding:10px 12px;background:#fff;border-left:5px solid #FFCC00;font-size:13px;line-height:1.5}
.takeaways{padding:10px 14px 12px;font-size:13px;margin:4px 0 16px}
.footer{padding:0 10px;font-size:11px}
"""


MOBILE_JS = """
function runLazy(root){
  (root||document).querySelectorAll('script[type="text/lazy"]').forEach(function(s){
    var box=s.parentElement;
    if(box.offsetParent===null){return;}                       // its tab is not open yet
    s.type='text/done';
    try{(0,eval)(s.textContent);}
    catch(e){box.insertAdjacentHTML('beforeend','<p style="padding:8px;font-size:12px">This plot could not be drawn here ('+e+').</p>');}
  });
}
var _showTab=showTab;
showTab=function(g,i,b){_showTab(g,i,b);runLazy(document.getElementById(i));};
"""
MOBILE_NOTE = ('<div id="jsnote">If you only see tabs and nothing opens, the app you opened this file with is blocking scripts '
               '(email, messaging and file previews do that). Open the file in Chrome or Safari instead.</div>'
               "<script>document.getElementById('jsnote').style.display='none';</script>")


def write_dashboard(sections, notes, heading, subtitle, csv_path, filename):
    """sections = [(id, tab label, html)]. Writes the page next to the CSV and opens it in the browser."""
    top_buttons = "".join(f'<button class="tabbtn top" data-group="top" onclick="showTab(\'top\',\'{i}\',this)">{lbl}</button>'
                          for i, lbl, _ in sections)
    top_panes = "".join(f'<div class="pane" data-group="top" id="{i}">{h}</div>' for i, lbl, h in sections)
    notes_html = "<ul>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>"
    page_tpl = PAGE_TEMPLATE
    for folder in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd()):
        logo = os.path.join(folder, "appLogo.png") if folder else None
        if logo and os.path.exists(logo):          # embed the logo so the page keeps its tab icon wherever it is saved
            import base64
            with open(logo, "rb") as lf:
                page_tpl = page_tpl.replace('href="appLogo.png"', 'href="data:image/png;base64,' + base64.b64encode(lf.read()).decode() + '"')
            break
    page = (page_tpl.replace("__FONTS__", GOOGLE_FONTS).replace("__H1__", heading).replace("__SUB__", subtitle)
            .replace("__TOP__", top_buttons).replace("__PANES__", top_panes).replace("__NOTES__", notes_html))
    from plotly.offline import get_plotlyjs
    plotly_js = get_plotlyjs()
    import re

    def finish_page(html, phone):
        html = re.sub(r"@@FIG(\d+)@@", lambda m: _figstore[int(m.group(1))][1 if phone else 0], html)
        html = html.replace("__PLOTLYJS__", "")                       # (inserted below; keeps this replace cheap)
        marker = "<script></script>"
        if phone:
            html = re.sub(r"<!--NM-->.*?<!--/NM-->", "", html, flags=re.S)      # buttons that need the computer's helper
            html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">', 1)
            html = html.replace("</style><script>", MOBILE_CSS + "</style><script>" + MOBILE_JS, 1)
            html = html.replace('<div class="tabs">', MOBILE_NOTE + '<div class="tabs">', 1)
            html = html.replace("<title>SLC Data Dashboard</title>", "<title>SLC Data Dashboard (phone)</title>", 1)
        else:
            html = html.replace("</body>", reload_script_html() + "</body>")
        return html.replace(marker, "<script>" + plotly_js + "</script>", 1)

    page = page.replace("<script>__PLOTLYJS__</script>", "<script></script>", 1)
    page_desktop = "" if _web else finish_page(page, False)
    page_phone = finish_page(page, True)
    _figstore.clear()
    out_dir = os.environ.get("TUTORING_DASH_OUTDIR") or _shown["outdir"] or os.path.dirname(os.path.abspath(csv_path))
    out_path = os.path.join(out_dir, filename)
    phone_path = os.path.join(out_dir, filename.replace(".html", "_mobile.html"))
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page_desktop)
    except OSError:
        out_path = os.path.abspath(filename)
        phone_path = os.path.abspath(filename.replace(".html", "_mobile.html"))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page_desktop)
    try:
        with open(phone_path, "w", encoding="utf-8") as f:
            f.write(page_phone)
    except OSError:
        phone_path = None
    LOADER.finish()
    print(f"✅ Dashboard saved to: {out_path}")
    if phone_path:
        print(f"📱 Phone version saved to: {phone_path}")
    if notes:
        print(f"ℹ  {len(notes)} note{'s' if len(notes) != 1 else ''} about this data {'are' if len(notes) != 1 else 'is'} at the bottom of the dashboard (About this data).")
    if _web:
        return
    import json
    already_open = out_path in json.loads(os.environ.get("TUTORING_DASH_OPENED", "[]"))
    if _child_run:
        try:
            with open(os.environ["TUTORING_DASH_RESULT"], "w", encoding="utf-8") as f:
                json.dump({"out": out_path}, f)
        except (KeyError, OSError):
            pass
    else:
        _shown["outdir"] = os.path.dirname(out_path)
        _shown["opened"].append(out_path)
    if not already_open:                                 # a page that is already open just refreshes itself
        webbrowser.open(f"file://{out_path}")


# =========================================================
# SI COMPARISON REPORT  (TracCloud export: grades grouped by number of visits)
# Chosen automatically when the CSV you give the script is an "SI Comparison" report instead of "Visits by ??".
# The visit groups (1-2, 3-4, 5+ ...) are read from the file, so changing them in TracCloud is fine.
# =========================================================
import csv
import math

Z95 = 1.96
GRADE_COLORS = {"A": AS_LAKE, "B": "#6AA6CC", "C": AS_LGRAY, "D": "#E3A07A", "F": AS_BRICK, "W": AS_DGRAY}


def read_rows(path, limit=None):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.reader(f)):
            if limit is not None and i >= limit:
                break
            rows.append([c.strip() for c in row])
    return rows


def detect_report_kind(path):
    """'visits' = Visits by ??, 'si' = SI Comparison, 'unknown' = neither."""
    try:
        head = read_rows(path, limit=6)
    except Exception:
        return "unknown"
    flat = " ".join(" ".join(r) for r in head)
    if "Date, Time Started" in flat:
        return "visits"
    if any(r and r[0].lower() == "session" for r in head) and "group" in flat.lower():
        return "si"
    return "unknown"


def to_num(text):
    try:
        return float(str(text).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def grade_points(label):
    g = label.strip().upper()
    if not g or g[0] not in "ABCDF" or len(g) > 2:
        return None
    pts = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}[g[0]]
    if g.endswith("+") and g[0] != "A":
        pts += 0.3
    if g.endswith("-") and g[0] != "F":
        pts -= 0.3
    return pts


def wilson(k, n, z=Z95):
    if not n:
        return (float("nan"), float("nan"))
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def parse_si_report(path):
    rows = read_rows(path)

    def cell(r, i):
        return r[i] if i < len(r) else ""

    s_idx = next(i for i, r in enumerate(rows) if r and r[0].lower() == "session")
    top = rows[s_idx - 1] if s_idx > 0 else []
    h_idx = next(i for i in range(s_idx, len(rows)) if rows[i] and rows[i][0].lower() == "grade")
    cols = [i for i, c in enumerate(rows[h_idx]) if c.lower() == "number"]
    groups = []
    for k, i in enumerate(cols):
        sess, tp = cell(rows[s_idx], i), cell(top, i)
        if k == 0:
            role, label = "control", "No visits"
        elif "total" in sess.lower():
            role, label = "total", "All tutored students"
        elif sess == "" and "non" in tp.lower() and "+" in tp:
            role, label = "all", "Everyone"
        elif sess:
            role, label = "bucket", f"{sess.replace('-', '–')} visits"
        else:
            continue
        groups.append(dict(role=role, label=label, col=i))
    n_row = next(r for r in rows[s_idx:h_idx] if r and r[0].lower().startswith("total n"))
    for g in groups:
        g["n"] = to_num(cell(n_row, g["col"]))

    grades, combined, gpa_rows, ungraded = [], [], {}, None
    for r in rows[h_idx + 1:]:
        label = r[0] if r else ""
        low = label.lower()
        if label == "" or low == "summary":
            if grades or combined:
                break
            continue
        vals = [to_num(cell(r, g["col"])) for g in groups]
        if low.startswith("combined"):
            combined.append((label, vals))
        elif "grade point average" in low or "gpa" in low or "standard deviation" in low:
            gpa_rows[label] = vals
        elif low.startswith("ungraded"):
            ungraded = vals
        elif not combined:
            grades.append((label, vals))

    summary, in_sum = {}, False
    for r in rows:
        if r and r[0].lower() == "summary":
            in_sum = True
            continue
        if in_sum and r and r[0]:
            if r[0].lower().startswith("withdrawals / visits"):
                break
            vals = [c for c in r[1:] if c != ""]
            if vals:
                summary[r[0]] = vals
    return dict(groups=groups, grades=grades, combined=combined, gpa=gpa_rows, ungraded=ungraded, summary=summary)


def run_si_dashboard(path):
    try:
        P = parse_si_report(path)
    except Exception as e:
        LOADER.stop()
        print(f"❌ ERROR: I recognized an SI Comparison report but could not read its layout ({e}).")
        sys.exit(1)
    groups, notes = P["groups"], []
    shown = [g for g in groups if g["role"] in ("control", "bucket", "total")]
    buckets = [g for g in groups if g["role"] == "bucket"]
    control = next((g for g in groups if g["role"] == "control"), None)
    total_g = next((g for g in groups if g["role"] == "total"), None)
    all_g = next((g for g in groups if g["role"] == "all"), None)
    if control is None or not buckets:
        LOADER.stop()
        print("❌ ERROR: This SI report needs a no-visit group and at least one visit group.")
        sys.exit(1)
    gi = {id(g): k for k, g in enumerate(groups)}

    def col_vals(vals, g):
        return vals[gi[id(g)]] if id(g) in gi else None

    # ---------- GPA (mean, spread, range) per group ----------
    rep = next((v for l, v in P["gpa"].items() if "no withdrawals" in l.lower() and "12" not in l
                and "standard" not in l.lower()), None)
    sd_row = next((v for l, v in P["gpa"].items() if "standard" in l.lower()), None)
    for g in groups:
        k = gi[id(g)]
        pts_counts = [(grade_points(l), v[k]) for l, v in P["grades"] if grade_points(l) is not None and v[k]]
        n_g = sum(c for _, c in pts_counts)
        mean_calc = sum(p * c for p, c in pts_counts) / n_g if n_g else float("nan")
        sd_calc = math.sqrt(sum(c * (p - mean_calc) ** 2 for p, c in pts_counts) / (n_g - 1)) if n_g > 1 else float("nan")
        g["graded"] = n_g
        g["gpa"] = rep[k] if rep and rep[k] is not None else mean_calc
        g["sd"] = sd_row[k] if sd_row and sd_row[k] is not None else sd_calc
        g["se"] = g["sd"] / math.sqrt(n_g) if n_g > 1 else float("nan")
        if rep and rep[k] is not None and abs(rep[k] - mean_calc) > 0.03:
            notes.append(f"For '{g['label']}' the GPA recomputed from the grade counts ({mean_calc:.2f}) differs from the "
                         f"GPA in the file ({rep[k]:.2f}); the file's value is shown and the ranges are approximate.")
        pass_k = P["combined"][0][1][k] if P["combined"] else None
        w_k = next((v[k] for l, v in P["grades"] if l.strip().upper() == "W"), None)
        g["pass"], g["w"] = pass_k, w_k
    n_all = sum(g["n"] or 0 for g in groups if g["role"] in ("control", "bucket"))
    n_treated = (total_g["n"] if total_g and total_g["n"] else sum(g["n"] or 0 for g in buckets))

    def short(g):
        return f"{g['label']}<br>({int(g['n']):,} students)"

    # ---------- Overview ----------
    def find_sum(*subs):
        for l, v in P["summary"].items():
            if all(s in l.lower() for s in subs):
                return v
        return None

    sess = find_sum("mean number", "sessions attended")
    hours = find_sum("total contact hours")
    size = find_sum("mean size", "attended")
    cards = [(f"{int(n_treated):,}", f"Students with at least one visit ({n_treated / n_all:.1%} of graded students)")]
    if sess:
        cards.append((sess[0], "Average visits per student who used tutoring"))
    if hours:
        cards.append((f"{to_num(hours[0]):,.0f}", "Total hours students spent in tutoring"))
    if size:
        cards.append((size[0], "Average students per session (1.0 = one-on-one)"))
    cards_html = '<div class="cards">' + "".join(
        f'<div class="card"><div class="big">{a}</div><div>{b}</div></div>' for a, b in cards) + "</div>"

    fig = go.Figure(go.Bar(
        x=[g["label"] for g in buckets], y=[(g["n"] or 0) / n_treated for g in buckets], marker=dict(color=AS_LAKE),
        marker_line=dict(width=2, color="white"),
        text=[f"{(g['n'] or 0) / n_treated:.0%}<br>{int(g['n'] or 0):,} students" for g in buckets], textposition="outside",
        hovertemplate="%{x}: %{y:.0%} of students who used tutoring<extra></extra>"))
    fig.update_layout(title=dict(text="How Often Students Who Used Tutoring Came"), height=430,
                      yaxis=dict(title="Share of students who used tutoring", tickformat=".0%",
                                 range=[0, max((g["n"] or 0) / n_treated for g in buckets) * 1.25]),
                      xaxis=dict(title="Visits in the report period"), margin=dict(l=70, r=40, t=80, b=60))
    overview = cards_html + fig_html(fig) + para(
        "<b>How to read this report.</b> It compares groups of students, so it can show whether grades differ but not "
        "why. Students who come to tutoring are not a random sample: they may be in harder courses, have weaker "
        "preparation, or be more motivated, and any of those can move grades either way. A gap between students who "
        "visited and students who did not is therefore <b>not</b> evidence for or against tutoring. The fairest "
        "comparison in this report is between visit groups (for example 1–2 visits vs 5+), and even that has wide "
        "ranges when a group is small.")

    # ---------- Grade mix ----------
    n_g = len(shown)
    ncols = min(3, n_g)
    nrows = -(-n_g // ncols)
    fig = make_subplots(rows=nrows, cols=ncols, specs=[[{"type": "domain"}] * ncols for _ in range(nrows)],
                        subplot_titles=[f"{short(g)}" for g in shown], vertical_spacing=0.14)
    for i, g in enumerate(shown):
        labs, cnts, cols = [], [], []
        for label, vals in P["grades"]:
            c = col_vals(vals, g) or 0
            if c > 0:
                labs.append(label); cnts.append(c); cols.append(GRADE_COLORS.get(label.strip().upper()[:1], AS_STONE))
        fig.add_trace(go.Pie(
            labels=labs, values=cnts, sort=False, direction="clockwise", marker=dict(colors=cols, line=dict(color="white", width=2)),
            text=[f"{l}<br>{c / sum(cnts):.0%}" if c / sum(cnts) >= 0.04 else "" for l, c in zip(labs, cnts)], textinfo="text",
            textposition="inside", insidetextorientation="horizontal", showlegend=(i == 0),
            hovertemplate="%{label}: %{percent} (%{value:,.0f} students)<extra></extra>", name=short(g)),
            row=i // ncols + 1, col=i % ncols + 1)
    fig.update_layout(title=dict(text="Grades by Number of Visits"), height=330 * nrows + 160,
                      legend=dict(orientation="h", yanchor="bottom", y=1.09, xanchor="right", x=1.0), margin=dict(l=40, r=40, t=150, b=40))
    mix_note = ("Each pie is one group of students and the slices add up to 100%. Blue shades are the higher grades, orange "
                "shades the lower ones, and W is a withdrawal.  Slices under 4% are not labeled; hover over them for the exact figure. Compare the visit groups with each other first; the 'No visits' pie "
                "covers every other graded student and is not a matched comparison.")
    ung = P["ungraded"]
    if ung:
        u_c, u_t = col_vals(ung, control), (col_vals(ung, total_g) if total_g else None)
        if u_c and u_t is not None:
            notes.append(f"{u_c:,.0f} enrollments in the no-visit group ({u_c / (u_c + control['n']):.0%} of that group) and "
                         f"{u_t:,.0f} among tutored students are ungraded (audit, pass/fail, incomplete and similar) and "
                         "left out of every grade figure here.")

    # ---------- GPA with ranges ----------
    def dot_figure(title, values, los, his, yaxis, hover_fmt):
        f = go.Figure(go.Scatter(
            x=[short(g) for g in shown], y=values, mode="markers", name="Average",
            error_y=dict(type="data", symmetric=False, array=[h - v for h, v in zip(his, values)],
                         arrayminus=[v - l for v, l in zip(values, los)], color=AS_BLACK, thickness=2, width=9),
            marker=dict(size=15, symbol="diamond", color=AS_GOLD, line=dict(width=2, color=AS_BLACK)),
            customdata=np.stack([los, his], axis=-1),
            hovertemplate=hover_fmt + "<extra></extra>"))
        f.update_layout(title=dict(text=title), yaxis=yaxis, height=480, showlegend=False,
                        margin=dict(l=80, r=40, t=80, b=80))
        return f

    ref = buckets[0]
    rows_t = []
    for g in shown:
        if g is control:
            rows_t.append((g["label"], f"{int(g['n']):,}", f"{g['gpa']:.2f}", f"{g['gpa'] - Z95 * g['se']:.2f} to {g['gpa'] + Z95 * g['se']:.2f}",
                           "Not a like-for-like comparison", ""))
        elif g is total_g:
            rows_t.append((g["label"], f"{int(g['n']):,}", f"{g['gpa']:.2f}", f"{g['gpa'] - Z95 * g['se']:.2f} to {g['gpa'] + Z95 * g['se']:.2f}", "", ""))
        elif g is ref:
            rows_t.append((g["label"], f"{int(g['n']):,}", f"{g['gpa']:.2f}", f"{g['gpa'] - Z95 * g['se']:.2f} to {g['gpa'] + Z95 * g['se']:.2f}",
                           "Starting point", ""))
        else:
            d = g["gpa"] - ref["gpa"]
            se = math.sqrt(g["se"] ** 2 + ref["se"] ** 2)
            lo, hi = d - Z95 * se, d + Z95 * se
            verdict = "Higher" if lo > 0 else "Lower" if hi < 0 else "Too close to call"
            rows_t.append((g["label"], f"{int(g['n']):,}", f"{g['gpa']:.2f}", f"{g['gpa'] - Z95 * g['se']:.2f} to {g['gpa'] + Z95 * g['se']:.2f}",
                           f"{d:+.2f} ({lo:+.2f} to {hi:+.2f})", verdict))
    gp = make_subplots(rows=2, cols=1, row_heights=[0.60, 0.40], vertical_spacing=0.10,
                       specs=[[{"type": "xy"}], [{"type": "table"}]])
    base_fig = dot_figure("", [g["gpa"] for g in shown], [g["gpa"] - Z95 * g["se"] for g in shown],
                          [g["gpa"] + Z95 * g["se"] for g in shown], dict(), "%{x}<br>Average GPA: %{y:.2f}<br>Likely range: %{customdata[0]:.2f} to %{customdata[1]:.2f}")
    gp.add_trace(base_fig.data[0], row=1, col=1)
    gp.update_yaxes(title_text="Average GPA (4.0 scale)", range=[0, 4.2], dtick=0.5, row=1, col=1)
    gp.add_trace(go.Table(
        header=table_header(["Group", "Students", "Average GPA", "Likely range", f"Compared with {ref['label']}", "What it means"], 12),
        cells=table_cells([[r[i] for r in rows_t] for i in range(6)], 12)), row=2, col=1)
    gp.update_layout(title=dict(text="Average GPA by Number of Visits"), height=820, showlegend=False,
                     margin=dict(l=80, r=40, t=80, b=40))
    bsd = [g["sd"] for g in buckets if np.isfinite(g["sd"])]
    pooled = float(np.mean(bsd)) if bsd else 1.1
    need = {d: int(2 * ((Z95 + 0.84) * pooled / d) ** 2) + 1 for d in (0.1, 0.2, 0.3)}
    smallest = min(buckets, key=lambda g: g["graded"])
    gpa_note = (
        "Each diamond is a group's average GPA and the black line is the range the true average probably falls in (95%). "
        "When two lines overlap a lot, the groups cannot be told apart. 'Too close to call' means the difference could "
        "easily be zero. To reliably detect a GPA difference of 0.1, 0.2 or 0.3 between two visit groups you would need "
        f"roughly {need[0.1]:,}, {need[0.2]:,} or {need[0.3]:,} students in each; the smallest group here "
        f"('{smallest['label']}') has {int(round(smallest['graded'])):,}.")
    gpa_how = (
        "<b>How the table is worked out.</b> <b>Average GPA</b> is built from the grade counts (A=4, B=3, C=2, D=1, F=0; "
        "withdrawals left out), so it matches TracCloud's number. <b>Likely range</b> answers: if we could re-run this year "
        "many times with similar students, where would the average usually land? A small group can swing a lot from luck, so "
        "its range is wide; a big group's range is narrow. It is the average plus or minus about two 'standard errors' "
        "(1.96 x the spread of grades / the square root of the number of students), which covers the true average about 95 "
        "times out of 100. <b>Compared with</b> subtracts the starting group's average and gives the same kind of range for "
        "that gap. If the range includes 0, the gap could be chance, so the last column says 'Too close to call'; "
        "'Higher' or 'Lower' appears only when the whole range is on one side of 0. The range only measures chance. It "
        "does not account for the groups being different kinds of students, so it cannot say tutoring caused a difference.")
    pass_how = (
        "<b>How the table is worked out.</b> <b>Passing</b> is the share of graded students who earned the grades listed "
        "above. <b>Likely range</b> is the 95% Wilson interval, a standard range for a percentage: the share you would "
        "usually see if this year were repeated with similar students. It is wide for small groups and stays sensibly "
        "between 0% and 100% even when a group is tiny or has no withdrawals at all.")

    # ---------- Passing and withdrawals ----------
    pass_def = P["combined"][0][0].replace("Combined", "").strip().replace(",", ", ") if P["combined"] else "passing grades"
    pg = [g for g in shown if g["pass"] is not None]
    ranges = [wilson(g["pass"], g["n"]) for g in pg]
    pf = make_subplots(rows=2, cols=1, row_heights=[0.62, 0.38], vertical_spacing=0.12,
                       specs=[[{"type": "xy"}], [{"type": "table"}]])
    tmp = go.Scatter(
        x=[short(g) for g in pg], y=[g["pass"] / g["n"] for g in pg], mode="markers",
        error_y=dict(type="data", symmetric=False, array=[h - g["pass"] / g["n"] for (l, h), g in zip(ranges, pg)],
                     arrayminus=[g["pass"] / g["n"] - l for (l, h), g in zip(ranges, pg)], color=AS_BLACK, thickness=2, width=9),
        marker=dict(size=15, symbol="diamond", color=AS_GOLD, line=dict(width=2, color=AS_BLACK)),
        customdata=np.array([[l, h] for l, h in ranges]),
        hovertemplate="%{x}<br>Passing: %{y:.1%}<br>Likely range: %{customdata[0]:.1%} to %{customdata[1]:.1%}<extra></extra>")
    pf.add_trace(tmp, row=1, col=1)
    pf.update_yaxes(title_text=f"Share earning {pass_def}", tickformat=".0%", range=[0, 1.02], row=1, col=1)
    pf.add_trace(go.Table(
        header=table_header(["Group", "Students", "Passing", "Likely range"], 12),
        cells=table_cells([[g["label"] for g in pg], [f"{int(g['n']):,}" for g in pg],
                           [f"{g['pass'] / g['n']:.1%}" for g in pg], [f"{l:.1%} to {h:.1%}" for l, h in ranges]], 12)), row=2, col=1)
    pf.update_layout(title=dict(text="Share of Students Earning a Passing Grade"), height=760, showlegend=False,
                     margin=dict(l=80, r=40, t=80, b=40))
    wg = [g for g in shown if g["w"] is not None]
    parts = [para(f"Passing means a grade of {pass_def}; withdrawals count as not passing, as in your report. Same reading "
                  "rule as the GPA chart: overlapping lines mean the groups cannot be told apart.")]
    wfig = None
    if wg:
        wr = [wilson(g["w"], g["n"]) for g in wg]
        wfig = go.Figure(go.Scatter(
            x=[short(g) for g in wg], y=[g["w"] / g["n"] for g in wg], mode="markers",
            error_y=dict(type="data", symmetric=False, array=[h - g["w"] / g["n"] for (l, h), g in zip(wr, wg)],
                         arrayminus=[g["w"] / g["n"] - l for (l, h), g in zip(wr, wg)], color=AS_BLACK, thickness=2, width=9),
            marker=dict(size=15, symbol="diamond", color=AS_GOLD, line=dict(width=2, color=AS_BLACK)),
            customdata=np.stack([[g["w"] for g in wg], [l for l, h in wr], [h for l, h in wr]], axis=-1),
            hovertemplate="%{x}<br>Withdrew: %{y:.1%} (%{customdata[0]:,.0f} students)<br>Likely range: "
                          "%{customdata[1]:.1%} to %{customdata[2]:.1%}<extra></extra>"))
        wfig.update_layout(title=dict(text="Share of Students Who Withdrew"), height=460, showlegend=False,
                           yaxis=dict(title="Share who withdrew", tickformat=".1%", range=[-max(h for l, h in wr) * 0.06, max(h for l, h in wr) * 1.08]),
                           margin=dict(l=80, r=40, t=80, b=80))
        parts += [fig_html(wfig), para(
            "Be careful reading this one. A student who withdraws early has little chance to visit tutoring, so low "
            "withdrawal among frequent visitors is partly built in. It does not show that tutoring prevents withdrawal.")]

    sections = [("overview", "Overview", overview), ("mix", "Grade Mix", fig_html(fig) + para(mix_note)),
                ("gpa", "Average GPA", fig_html(gp) + para(gpa_note) + para(gpa_how)),
                ("pass", "Passing and Withdrawals", fig_html(pf) + parts[0] + para(pass_how) + "".join(parts[1:]))]
    notes = [f"Everyone with no visits is one group of {int(control['n']):,} graded enrollments; it is not matched to the "
             "tutored students by course, preparation or term."] + notes + [
        "Groups of students are the same as the visit groups chosen in the TracCloud report options; change them there and "
        "re-export to see different cut-offs."]
    return sections, notes


# =========================================================
# GET CSV FILE FROM USER
# =========================================================
def ask_for_csv():
    if len(sys.argv) > 1:                       # path passed on the command line (handy for testing)
        return sys.argv[1].strip(' "\'')
    while True:
        path = input("Enter the path to your CSV file: ").strip(' "\'')
        if not os.path.exists(path):
            print(f"❌ ERROR: Could not find a file at '{path}'. Please try again.\n")
        elif not path.lower().endswith(".csv"):
            print("❌ ERROR: The file must be a .csv file. Please try again.\n")
        else:
            print("✅ File found!\n")
            return path


# =========================================================
# RETURN RATE  (share of first-time students who come back)
# =========================================================
def term_of(dates):
    """('Fall' | 'Spring' | None, year) from the middle date of a file."""
    mid = dates.sort_values().iloc[len(dates) // 2]
    if mid.month >= 8:
        return "Fall", int(mid.year)
    if mid.month <= 5:
        return "Spring", int(mid.year)
    return None, int(mid.year)


def term_window(season, year):
    (m1, d1), (m2, d2) = TERM_DATES[season]
    return pd.Timestamp(year, m1, d1), pd.Timestamp(year, m2, d2)


def return_stats(frame, season, year, days_in=None):
    """(returned, counted) for one term: first-time students that term, and how many visited again on a later day.
    days_in limits the term to its first N days so a half-finished term can be compared with past terms at the same point."""
    a, b = term_window(season, year)
    if days_in is not None:
        b = min(b, a + pd.Timedelta(days=days_in))
    if "Student" not in frame.columns:
        return 0, 0
    f = frame.dropna(subset=["Student"])
    f = f[(f["Date"] >= a) & (f["Date"] <= b)][["Student", "Date"]].drop_duplicates()
    n_days = f.groupby("Student")["Date"].nunique()
    return int((n_days >= 2).sum()), int(len(n_days))


def find_history_file():
    here = [os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd(), getattr(sys, "_MEIPASS", None)]
    for folder in here:
        if folder and os.path.exists(os.path.join(folder, HISTORY_FILE)):
            return os.path.join(folder, HISTORY_FILE)
    return None


HISTORY_PROBLEM = []                 # filled with a plain-English message if history.csv cannot be used


def read_history_frame():
    """history.csv as a table, or None (with a message in HISTORY_PROBLEM) if it is missing or not a visits export."""
    path = find_history_file()
    if not path:
        return None, None
    try:
        d = pd.read_csv(path)
        if "Date, Time Started" not in d.columns:
            raise ValueError("it has no 'Date, Time Started' column")
        if "Student" not in d.columns and not HISTORY_PROBLEM:
            HISTORY_PROBLEM.append(f"{HISTORY_FILE} has no Student column, so Return Rate can't use past terms. "
                                   "Use the full export (with Student) for that tab.")
    except Exception as error:
        if not HISTORY_PROBLEM:
            HISTORY_PROBLEM.append(f"{HISTORY_FILE} could not be used ({str(error)[:90]}), so past years are left out. "
                                   "Replace it with a 'Visits by ??' export.")
        return None, path
    return d, path


def load_history(days_in):
    """{(season, year): (returned, counted)} for every past term found in history.csv."""
    d, path = read_history_frame()
    if d is None:
        return {}, path
    d["Start"] = pd.to_datetime(d["Date, Time Started"], format="%Y-%m-%d, %I:%M%p", errors="coerce")
    d = d.dropna(subset=["Start"]).copy()
    d["Date"] = d["Start"].dt.normalize()
    out = {}
    for yr in sorted(d["Date"].dt.year.unique()):
        for season in TERM_DATES:
            r, n = return_stats(d, season, int(yr), days_in)
            if n:
                out[(season, int(yr))] = (r, n)
    return out, path


def predict_rate(points, year, conf=RETURN_CONF):
    """points: [(year, returned, n)]. Straight-line trend through past rates, with a range for a new year."""
    from scipy import stats as st
    xs = np.array([p[0] for p in points], float)
    ys = np.array([p[1] / p[2] for p in points], float)
    k = len(xs)
    if k < 3:
        return None
    slope, icpt, *_ = st.linregress(xs, ys)
    resid = ys - (icpt + slope * xs)
    s = float(np.sqrt((resid ** 2).sum() / (k - 2)))
    sxx = float(((xs - xs.mean()) ** 2).sum())
    t = st.t.ppf(0.5 + conf / 2, k - 2)

    def at(x):
        mid = icpt + slope * x
        half = t * s * np.sqrt(1 + 1 / k + (x - xs.mean()) ** 2 / sxx)
        return float(mid), float(max(0, mid - half)), float(min(1, mid + half))
    return dict(at=at, slope=float(slope), k=k, pred=at(year))


def return_rate_section(df, last_complete):
    """Builds the 'Return Rate' tab: this term's rate vs a trend-adjusted expectation from past same-season terms."""
    from scipy import stats as st
    zc = float(st.norm.ppf(0.5 + RETURN_CONF / 2))
    pct = f"{RETURN_CONF:.0%}"
    notes = []
    sem, yr = term_of(df["Date"])
    if not df["Student"].notna().any():
        return para("This file has no student names, so return rates cannot be worked out."), notes
    if sem is None:
        return para("This looks like a summer file; return rates are only compared for Fall and Spring."), notes
    name = f"{sem} {yr}"
    w_start, w_end = term_window(sem, yr)
    days_in = max(0, (min(pd.Timestamp(last_complete), w_end) - w_start).days)
    complete = pd.Timestamp(last_complete) >= w_end
    cur_r, cur_n = return_stats(df, sem, yr, days_in)
    hist, hpath = load_history(days_in)
    if HISTORY_PROBLEM:
        notes.append(HISTORY_PROBLEM[0])
    elif hpath is None:
        notes.append(f"{HISTORY_FILE} was not found next to the script, so the Return Rate tab has no past terms to compare with.")
    seasons = {}
    for (s_, y_), (r_, n_) in hist.items():
        seasons.setdefault(s_, []).append((y_, r_, n_))
    for s_ in seasons:
        seasons[s_].sort()
    wil = lambda k, n: wilson(k, n, zc)
    when = "the full term" if complete else f"the first {days_in} days of the term (the same point as this term so far)"

    def season_fig(season, current):
        pts = [p for p in seasons.get(season, []) if not (current and p[0] == yr)]
        if not pts:
            return None, pts, None
        pred = predict_rate(pts, yr if current else max(p[0] for p in pts) + 1)
        fig = make_subplots(rows=2, cols=1, row_heights=[0.62, 0.38], vertical_spacing=0.12,
                            specs=[[{"type": "xy"}], [{"type": "table"}]])
        years = [p[0] for p in pts]
        rates = [p[1] / p[2] for p in pts]
        ci = [wil(p[1], p[2]) for p in pts]
        xs_all = years + ([yr] if current else [])
        lo_x, hi_x = min(xs_all) - 0.5, max(xs_all) + 0.5
        if pred:
            line_x = [lo_x, hi_x]
            m0, l0, h0 = pred["at"](line_x[0]); m1, l1, h1 = pred["at"](line_x[1])
            fig.add_trace(go.Scatter(x=line_x + line_x[::-1], y=[h0, h1, l1, l0], fill="toself", mode="none",
                                     fillcolor="rgba(255,204,0,0.25)", name=f"Expected range ({pct})", hoverinfo="skip"), row=1, col=1)
            fig.add_trace(go.Scatter(x=line_x, y=[m0, m1], mode="lines", line=dict(color=AS_DGRAY, dash="dash", width=2),
                                     name="Trend line", hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=years, y=rates, mode="markers", name="Past terms",
            error_y=dict(type="data", symmetric=False, array=[h - r for r, (l, h) in zip(rates, ci)],
                         arrayminus=[r - l for r, (l, h) in zip(rates, ci)], color=AS_LAKE, thickness=2, width=7),
            marker=dict(size=12, color=AS_LAKE, line=dict(width=2, color="white")),
            customdata=np.stack([[p[1] for p in pts], [p[2] for p in pts]], axis=-1),
            hovertemplate=season + " %{x}<br>Returned: %{y:.1%} (%{customdata[0]:,.0f} of %{customdata[1]:,.0f})<extra></extra>"), row=1, col=1)
        cur_rate = cur_r / cur_n if cur_n else None
        if current and cur_n:
            lo, hi = wil(cur_r, cur_n)
            fig.add_trace(go.Scatter(
                x=[yr], y=[cur_rate], mode="markers", name="This term",
                error_y=dict(type="data", symmetric=False, array=[hi - cur_rate], arrayminus=[cur_rate - lo],
                             color=AS_BLACK, thickness=2, width=7),
                marker=dict(size=16, symbol="diamond", color=AS_GOLD, line=dict(width=2, color=AS_BLACK)),
                hovertemplate=f"{season} {yr} (so far)<br>Returned: %{{y:.1%}} ({cur_r} of {cur_n})<extra></extra>"), row=1, col=1)
        allv = rates + [l for l, h in ci] + [h for l, h in ci] + ([cur_rate] if current and cur_n else [])
        if pred:
            allv += [pred["at"](lo_x)[1], pred["at"](hi_x)[2]]
        fig.update_yaxes(title_text="Share of first-time students who came back", tickformat=".0%",
                         range=[max(0, min(allv) - 0.08), min(1, max(allv) + 0.08)], row=1, col=1)
        fig.update_xaxes(tickmode="array", tickvals=xs_all, ticktext=[str(x) for x in xs_all], range=[lo_x, hi_x], row=1, col=1)
        rows = [[str(p[0]) for p in pts], [f"{p[2]:,}" for p in pts], [f"{p[1]:,}" for p in pts],
                [f"{r:.1%}" for r in rates], [f"{l:.1%} to {h:.1%}" for l, h in ci]]
        if current:
            rows[0].append(f"{yr} (so far)"); rows[1].append(f"{cur_n:,}"); rows[2].append(f"{cur_r:,}")
            rows[3].append(f"{cur_rate:.1%}" if cur_n else "n/a")
            rows[4].append(f"{wil(cur_r, cur_n)[0]:.1%} to {wil(cur_r, cur_n)[1]:.1%}" if cur_n else "n/a")
            if pred:
                pm, pl, ph = pred["pred"]
                rows[0].append(f"{yr} predicted"); rows[1].append("-"); rows[2].append("-")
                rows[3].append(f"{pm:.1%}"); rows[4].append(f"{pl:.1%} to {ph:.1%}")
        fig.add_trace(go.Table(header=table_header(["Term", "Students counted", "Came back", "Return rate", f"Likely range ({pct})"], 12),
                               cells=table_cells(rows, 12)), row=2, col=1)
        fig.update_layout(title=dict(text=f"{season}: Share of First-Time Students Who Came Back"), height=800,
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=80, r=40, t=110, b=40))
        return fig, pts, pred

    how = (f"<b>How this is measured.</b> A student counts as having <b>returned</b> if they visit on at least two different days "
           f"in the term. Only school-in dates are used (Fall {TERM_DATES['Fall'][0][0]}/{TERM_DATES['Fall'][0][1]} to "
           f"{TERM_DATES['Fall'][1][0]}/{TERM_DATES['Fall'][1][1]}, Spring {TERM_DATES['Spring'][0][0]}/{TERM_DATES['Spring'][0][1]} to "
           f"{TERM_DATES['Spring'][1][0]}/{TERM_DATES['Spring'][1][1]}, including one extra day each side). Past terms are measured over {when}, "
           "so a half-finished term is not penalized for students who have not had time to come back yet. <b>Likely range</b> is "
           f"the margin of error for a percentage at {pct} confidence (Wilson interval). A {pct} range is deliberately narrow, "
           f"so about {1 - RETURN_CONF:.0%} of ordinary terms will land outside it by chance alone; treat 'higher' or 'lower' as a "
           "hint to look into, not proof that something changed.")
    items = []
    order = [sem]
    for season in order:
        current = season == sem
        fig, pts, pred = season_fig(season, current)
        label = season
        if fig is None:
            items.append((label, para(f"No past {season} terms were found in {HISTORY_FILE}.")))
            continue
        text = ""
        cards_html = ""
        if current:
            if not cur_n:
                text = para(f"No first-time students were found for {name} yet.")
            elif pred is None:
                text = para(f"{name} so far: {cur_r / cur_n:.1%} of {cur_n:,} first-time students came back. At least 3 past "
                            f"{season} terms are needed to estimate a trend and say whether this is high or low; {len(pts)} found so far.")
            else:
                mid, plo, phi = pred["pred"]
                rate = cur_r / cur_n
                diff = (rate - mid) * 100
                if rate > phi:
                    verdict = f"<b>Higher than expected.</b> {rate:.1%} is above the expected range ({plo:.1%} to {phi:.1%})."
                elif rate < plo:
                    verdict = f"<b>Lower than expected.</b> {rate:.1%} is below the expected range ({plo:.1%} to {phi:.1%})."
                else:
                    verdict = (f"<b>About what was expected.</b> {rate:.1%} is {'above' if diff >= 0 else 'below'} the trend line but "
                               f"inside the expected range ({plo:.1%} to {phi:.1%}).")
                word = "Higher" if rate > phi else "Lower" if rate < plo else "As expected"
                cards_html = ('<div class="cards">' + "".join(f'<div class="card"><div class="big">{a}</div><div>{b}</div></div>' for a, b in [
                    (f"{plo:.1%} to {phi:.1%}", f"Predicted range for {name} ({pct})"),
                    (f"{rate:.1%}", f"{name} so far ({cur_r:,} of {cur_n:,} students)"),
                    (word, "Compared with the prediction" if word == "As expected" else "than the predicted range")]) + "</div>")
                direction = "rising" if pred["slope"] > 0 else "falling"
                text = para(f"{name} so far: {cur_r / cur_n:.1%} of {cur_n:,} first-time students came back. Past {season} terms have been "
                            f"{direction} by about {abs(pred['slope']) * 100:.1f} points per year, so the trend predicts <b>{mid:.1%}</b> this "
                            f"year ({diff:+.1f} points from the prediction). {verdict}")
                if pred["k"] <= 4:
                    text += para(f"Only {pred['k']} past {season} terms are available, so the trend itself is uncertain. "
                                 "It becomes more useful as more terms are added.")
        else:
            text = para(f"Past {season} terms only (the current term is {name}).")
        items.append((label, cards_html + fig_html(fig, pk="return" if not items else None) + text + para(how)))
    return items[0][1], notes


# =========================================================
# "OPEN IN BETTER FORECASTING" BUTTON  (local helper, runs only on this computer)
# =========================================================
import atexit
import http.server
import secrets
import subprocess
import time
import threading
from pathlib import Path

FORECAST_APP_PATH = None            # full path to the forecasting app, or None to look for it next to this program
FORECAST_APP_NAMES = ["Tutoring Forecast.exe", "Tutoring Forecast", "Tutoring Forecast.app", "tutoring_forecast_app.py"]
FORECAST_AUTO_RUN = True            # True = start the forecast as soon as the app opens; False = just load the file
_launch = dict(csv=None, port=None, token=None, run=0)
_shown = dict(outdir=None, opened=[])     # where the page lives and which pages are already open in the browser


def program_folders():
    """Folders to look in for files that ship with the program (works as a script and as a PyInstaller exe)."""
    folders = [os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd(), getattr(sys, "_MEIPASS", None)]
    if getattr(sys, "frozen", False):
        folders.insert(0, os.path.dirname(sys.executable))
    return [f for f in folders if f]


def find_forecast_app():
    if FORECAST_APP_PATH and os.path.exists(FORECAST_APP_PATH):
        return FORECAST_APP_PATH
    for folder in program_folders():
        for name in FORECAST_APP_NAMES:
            candidate = os.path.join(folder, name)
            if os.path.exists(candidate):
                return candidate
    return None


def make_forecast_input(csv_path):
    """Past years (history.csv, if present) plus this export, duplicates removed, written to the forecast app's own folder."""
    parts = [pd.read_csv(csv_path)]
    hist_frame, hist = read_history_frame()
    if hist_frame is not None:
        parts.insert(0, hist_frame)
    else:
        hist = None
    merged = pd.concat(parts, ignore_index=True).drop_duplicates()
    out_dir = Path.home() / ".tutoring_forecast"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "dashboard_input.csv"
    merged.to_csv(out, index=False)
    return str(out), len(merged), bool(hist)


def latest_app_chart():
    """The forecast chart the forecasting app saved after it was opened from this program (None if it was not)."""
    if not _launch.get("launched_at"):
        return None
    path = Path.home() / ".tutoring_forecast" / "latest_forecast_chart.png"
    try:
        if path.stat().st_mtime >= _launch["launched_at"] - 1:
            return path.read_bytes()
    except OSError:
        pass
    return None


def launch_forecast_app():
    """Returns a short message for the page. Never raises."""
    try:
        app = find_forecast_app()
        if not app:
            return "Could not find the forecasting app. Put 'Tutoring Forecast' in the same folder as this program."
        data, n_rows, used_history = make_forecast_input(_launch["csv"])
        if app.lower().endswith(".py"):
            if getattr(sys, "frozen", False):
                return "This program cannot start a .py file. Use the 'Tutoring Forecast' program instead."
            cmd = [sys.executable, app, data]
        elif app.lower().endswith(".app") and sys.platform == "darwin":
            cmd = ["open", "-n", "-a", app, "--args", data]
        else:
            cmd = [app, data]
        if FORECAST_AUTO_RUN:
            cmd.append("--run")
        subprocess.Popen(cmd, cwd=os.path.dirname(app) or None)
        _launch["launched_at"] = time.time()
        extra = "" if used_history else " (history.csv was not found, so only this term's data was sent)"
        return f"ok: sent {n_rows:,} visits (this term plus past years){extra}"
    except Exception as error:
        return f"Something went wrong: {str(error)[:150]}"


def start_launch_server(csv_path):
    """A tiny web server that only answers this computer (127.0.0.1) and only for a secret token. The button on the
    dashboard page calls it; it can do exactly one thing: open the forecasting app with this CSV."""
    _launch["csv"] = os.path.abspath(csv_path)
    if _web:
        return False
    if _child_run:                                   # the first window already runs the helper; just point at it
        try:
            _launch["port"] = int(os.environ["TUTORING_DASH_PORT"])
            _launch["token"] = os.environ["TUTORING_DASH_TOKEN"]
            _launch["run"] = int(os.environ.get("TUTORING_DASH_RUN", "0"))
        except (KeyError, ValueError):
            pass
        return bool(_launch["port"])
    _launch["token"] = secrets.token_urlsafe(16)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            tok = self.path.split("=", 1)[1] if "=" in self.path else ""
            ok = secrets.compare_digest(tok, _launch["token"])
            if ok and self.path.startswith("/chart?"):           # the chart the forecasting app made after it was opened from here
                png = latest_app_chart()
                if png is None:
                    self.send_response(404)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
                return
            if ok and self.path.startswith("/version?"):         # the open page asks this to know when to refresh itself
                body = str(_launch["run"]).encode("utf-8")
            else:
                ok = ok and self.path.startswith("/launch?")
                body = (launch_forecast_app() if ok else "Not allowed").encode("utf-8")
            self.send_response(200 if ok else 403)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError:
        return False
    _launch["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return True


def launch_button_html():
    if not _launch["port"]:
        return ""
    return (
        '<!--NM--><div class="cards" style="margin:14px auto 22px"><div class="card" style="min-width:420px;text-align:center">'
        '<button class="tabbtn" style="background:#FFCC00;color:#010101;font-size:15px" onclick="launchForecast()">'
        'Open in Better Forecasting</button>'
        '<div id="launchmsg" style="margin-top:8px;font-size:13px;color:#4c4847">Opens the forecasting app with this data '
        '(plus your past years) and starts the forecast.</div></div></div>'
        '<script>function launchForecast(){var m=document.getElementById("launchmsg");m.textContent="Opening...";'
        f'fetch("http://127.0.0.1:{_launch["port"]}/launch?token={_launch["token"]}")'
        '.then(function(r){return r.text()}).then(function(t){m.textContent=t.indexOf("ok")===0?"Opened the forecasting app with "+t.slice(4):t})'
        '.catch(function(){m.textContent="Could not reach the dashboard program. It has to stay open (the black window) '
        'for this button to work. Reopen it and try again."});}</script><!--/NM-->')


def reload_script_html():
    """Added to every page: while the program window is open, refresh this page when a newer CSV has been processed."""
    if not _launch["port"]:
        return ""
    return ('<script>(function(){var me=%d;function chk(){fetch("http://127.0.0.1:%d/version?token=%s")'
            '.then(function(r){return r.text()}).then(function(t){if(parseInt(t,10)!==me){location.reload();}})'
            '.catch(function(){});}setInterval(chk,2500);})();</script>' % (_launch["run"], _launch["port"], _launch["token"]))


def pause_for_enter():
    if _child_run or _web:
        return
    if getattr(sys, "frozen", False):        # double-clicked program: keep the window open so messages can be read
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, KeyboardInterrupt):
            pass


def run_another_csv(path):
    """Build the dashboard for another CSV in a quiet child run, writing over the page that is already open."""
    import json, tempfile
    result = os.path.join(tempfile.gettempdir(), f"tutoring_dash_result_{os.getpid()}.json")
    try:
        os.remove(result)
    except OSError:
        pass
    env = dict(os.environ, TUTORING_DASH_CHILD="1", TUTORING_DASH_PORT=str(_launch["port"]), TUTORING_DASH_TOKEN=_launch["token"],
               TUTORING_DASH_RUN=str(_launch["run"] + 1), TUTORING_DASH_OUTDIR=_shown["outdir"] or "",
               TUTORING_DASH_OPENED=json.dumps(_shown["opened"]), TUTORING_DASH_RESULT=result,
               PYINSTALLER_RESET_ENVIRONMENT="1")
    cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else [os.path.abspath(__file__)]) + [path]
    try:
        code = subprocess.call(cmd, env=env)
    except OSError as e:
        print(f"❌ ERROR: could not start the next run ({e}).")
        return
    if code != 0:
        print("That file could not be used, so the open dashboard was left as it was.")
        return
    _launch["csv"] = os.path.abspath(path)
    _launch["run"] += 1                                  # the open page notices this and refreshes itself
    try:
        with open(result, encoding="utf-8") as f:
            out = json.load(f).get("out")
        if out and out not in _shown["opened"]:
            _shown["opened"].append(out)
        os.remove(result)
    except (OSError, ValueError):
        pass
    print("The open dashboard page refreshes by itself.")


def wait_before_exit():
    """Keep the window (and the helper the open page talks to) alive. Another CSV can be entered; Enter alone closes.
    Returns True if it asked something, so the caller does not ask a second 'press Enter'."""
    if _child_run or _web:
        return True
    if not _launch["port"]:
        return False
    while True:
        try:
            answer = input("\nEnter the path to another CSV to update the dashboard, or just press Enter to close: ")
        except (EOFError, KeyboardInterrupt):
            return True
        path = answer.strip(' "\'')
        if not path:
            return True
        if not os.path.exists(path):
            print(f"❌ ERROR: Could not find a file at '{path}'. Please try again.")
        elif not path.lower().endswith(".csv"):
            print("❌ ERROR: The file must be a .csv file. Please try again.")
        else:
            print("✅ File found!\n")
            run_another_csv(path)


csv_path = ask_for_csv()

# Pick the right dashboard for the file: "Visits by ??" (this script's main job) or "SI Comparison" (grades by visits).
_kind = detect_report_kind(csv_path)
if _kind == "si":
    # SI Comparison exports do not contain the date range, so ask for it (optional) and show it in the page header.
    if len(sys.argv) > 2:
        _dates = sys.argv[2].strip()
    else:
        print("This report does not say which dates it covers.")
        _dates = "" if _web else input("Enter the date range or term it covers, e.g. Aug 2025 - May 2026 (press Enter to skip): ").strip()
    LOADER.start()
    LOADER.step(0.35, "Building the effectiveness dashboard", ceiling=0.9)
    si_sections, si_notes = run_si_dashboard(csv_path)
    _sub = f"Data covers {_dates}" if _dates else f"SI Comparison report: {os.path.basename(csv_path)}"
    if _dates:
        si_notes = [f"Date range: {_dates} (typed in when the dashboard was made; the report file itself does not store dates)."] + si_notes
    start_launch_server(csv_path)
    write_dashboard(si_sections, si_notes, "Tutoring Effectiveness", _sub, csv_path, "tutoring_effectiveness.html")
    if not wait_before_exit():
        pause_for_enter()
    sys.exit(0)
elif _kind == "unknown":
    LOADER.stop()
    print("❌ ERROR: I don't recognize this file. Please use a 'Visits by ??' export or an 'SI Comparison' report.")
    pause_for_enter()
    sys.exit(1)

# =========================================================
# LOAD & CLEAN (done once, shared by every plot)
# =========================================================
LOADER.start()
LOADER.step(0.04, "Reading your file", ceiling=0.1)
df = None
for _skip in range(0, 8):                      # tolerate a few junk lines above the real column names
    try:
        _try = pd.read_csv(csv_path, skiprows=_skip)
    except Exception:
        continue
    if "Date, Time Started" in _try.columns:
        df = _try
        break
if df is None:
    LOADER.stop()
    print("❌ ERROR: I couldn't find the 'Date, Time Started' column in that file. Please export it again from TracCloud "
          "(Reports -> Students -> Visits by ??).")
    pause_for_enter()
    sys.exit(1)
_col_notes = []
for _col in ("Student", "Consultant", "Center", "Subject/Course/Section", "Visit Time Hours"):
    if _col not in df.columns:                 # an optional column was left out of the export: carry on without it
        df[_col] = np.nan
        _col_notes.append(f"The file has no '{_col}' column, so anything that needs it is skipped.")
n_rows_file = len(df)

df["Start"] = pd.to_datetime(df["Date, Time Started"], format="%Y-%m-%d, %I:%M%p", errors="coerce")
n_bad_dates = int(df["Start"].isna().sum())
df = df.dropna(subset=["Start"]).copy()
if df.empty:
    LOADER.stop()
    print("❌ ERROR: none of the dates in that file could be read. The dates should look like 2026-08-24, 2:00pm.")
    pause_for_enter()
    sys.exit(1)
df["Date"] = df["Start"].dt.normalize()
LOADER.step(0.12, "Cleaning the data", ceiling=0.2)

n_missing_hours = int(df["Visit Time Hours"].isna().sum())
df["Hours"] = df["Visit Time Hours"].fillna(0)

# The data site lists the CS room separately, but it is all drop-in
df["Type"] = np.where(df["Center"].astype(str).str.contains("Appointment", case=False), "Appointment", "Drop-in")

# Subject = first three characters of the course code. MAT1035 is treated as a business course.
course = df["Subject/Course/Section"].astype(str).str.strip()
course = course.str.replace(r"^MAT\s?1035", "BUS1035", regex=True)
df["Prefix"] = course.str[:3]


def to_group(prefix):
    for name, prefixes in GROUP_MAPPING.items():
        if prefix in prefixes:
            return name
    return "Specialized"          # anything not listed above (KIN, CIS, LAW...) belongs in Specialized


df["Group"] = df["Prefix"].apply(to_group)

# --- Time window ---
today = pd.Timestamp.today().normalize()
start = pd.Timestamp(TERM_START)
last_data = df["Date"].max()
end = pd.Timestamp(END_DATE) if END_DATE else min(today, last_data)
last_complete = end - pd.Timedelta(days=1) if end == today else end   # today may still be in progress

all_days = pd.date_range(start, end)
open_days = all_days[all_days.dayofweek.isin(OPEN_WEEKDAYS)]
complete_days = open_days[open_days <= last_complete]

in_term = df[(df["Date"] >= start) & (df["Date"] <= end)]
d = in_term[in_term["Date"].dt.dayofweek.isin(OPEN_WEEKDAYS)].copy()          # visits on open days
d_done = d[d["Date"] <= last_complete]                                          # finished open days only
n_before_start = int((df["Date"] < start).sum())
has_people = df["Student"].notna().any()
has_consultants = df["Consultant"].notna().any()

notes = list(_col_notes)
if n_before_start:
    notes.append(f"{n_before_start} visits happened before the term start ({start:%b %d}) and are left out of the visit plots.")
if n_bad_dates:
    notes.append(f"{n_bad_dates} rows had a date that could not be read and were skipped.")
if n_missing_hours:
    notes.append(f"{n_missing_hours} visits had no recorded length and count as 0 hours in the hours plots (they still count as visits).")
if end < today:
    notes.append(f"The file's last visit is {end:%b %d}; days after that are not shown, so they are not mistaken for zero-visit days.")
if end == today:
    notes.append("Today's visits may still be coming in, so today is drawn as an open circle and is left out of weekly totals, averages and forecasts.")
notes.append("Days with no visits (breaks, holidays) are shown as 0 visits in the subject plots and forecasts, "
             "and are hidden from the overall daily visits plot.")
LOADER.step(0.2, "Drawing the plots", ceiling=0.3)     # the notes are kept for the page footer ("About this data")


def make_note_fig(title, message, height=420):
    fig = go.Figure()
    fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
                       font=dict(size=16, color=AS_DGRAY))
    fig.update_layout(title=dict(text=title, font=dict(size=22)), height=height,
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


def daily_counts(frame, days):
    return frame.groupby("Date").size().reindex(days, fill_value=0).astype(float)


# =========================================================
# PLAIN-LANGUAGE TREND LABELS (STL)
# =========================================================
def strength_label(x):
    if x < TREND_CUTS[0]:
        return "Weak"
    if x < TREND_CUTS[1]:
        return "Moderate"
    return "Strong"


def stl_summary(ts, total_visits):
    """Returns (trend strength label, trend direction label, seasonal strength label)."""
    if total_visits < MIN_VISITS_FOR_STL or len(ts) < 3 * STL_PERIOD:
        return ("Too few visits", "Too few visits", "Too few visits")
    try:
        res = STL(ts.values, period=STL_PERIOD, robust=True).fit()
    except Exception:
        return ("Not available", "Not available", "Not available")
    T, S, R = res.trend, res.seasonal, res.resid
    var_t, var_s = np.var(T + R), np.var(S + R)
    f_t = max(0, 1 - np.var(R) / var_t) if var_t > 0 else 0
    f_s = max(0, 1 - np.var(R) / var_s) if var_s > 0 else 0
    change = T[-STL_PERIOD:].mean() - T[:STL_PERIOD].mean()      # visits/day, first open week -> last open week
    base = ts.mean()
    if base > 0 and change > DIRECTION_THRESHOLD * base:
        direction = f"Rising (up about {abs(change):.1f} visits per day)"
    elif base > 0 and change < -DIRECTION_THRESHOLD * base:
        direction = f"Falling (down about {abs(change):.1f} visits per day)"
    else:
        direction = "Flat (no clear change)"
    return (strength_label(f_t), direction, strength_label(f_s))


def visits_figure(ts, title, labels, ymax=None, cummax=None, partial_last=False):
    """Daily visits (left axis) and running total (right axis) on one plot, plain-language table below."""
    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.80, 0.20], vertical_spacing=0.13,
        specs=[[{"secondary_y": True}], [{"type": "table"}]])
    x = ts.index
    fig.add_trace(go.Scatter(
        x=x, y=ts.values, mode="lines+markers", name="Visits per day (left axis)",
        line=dict(width=2, color=C_INK), marker=dict(size=9, color=AS_GOLD, line=dict(width=1.5, color=C_INK)),
        hovertemplate="Visits per day: %{y:.0f}<extra></extra>"), row=1, col=1, secondary_y=False)
    if partial_last:
        fig.add_trace(go.Scatter(
            x=[x[-1]], y=[ts.iloc[-1]], mode="markers", name="Today (day not finished)",
            marker=dict(size=12, color="white", line=dict(width=2, color=C_INK)), hoverinfo="skip"),
            row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(
        x=x, y=ts.cumsum().values, mode="lines", name="Total visits so far (right axis)",
        line=dict(width=3, color=C_CUM, dash="dot"),
        hovertemplate="Total visits so far: %{y:.0f}<extra></extra>"), row=1, col=1, secondary_y=True)
    fig.add_trace(go.Table(
        header=table_header(["Trend Strength", "Trend Direction", "Seasonal Strength"]),
        cells=table_cells([[labels[0]], [labels[1]], [labels[2]]], 13, height=30)), row=2, col=1)
    # the plot shows only the days in 'ts' (open days, holidays removed, plus weekend days that had visits);
    # every other calendar day is squeezed out of the axis so there are no empty gaps
    skipped = pd.date_range(ts.index.min(), ts.index.max()).difference(ts.index) if len(ts) else []
    breaks = [dict(values=[t.strftime("%Y-%m-%d") for t in skipped])] if len(skipped) else []
    hidden_note = ("Days with no visits (breaks, holidays) and weekend days with no visits are hidden from this plot.<br>"
                   if len(skipped) else "")
    fig.update_xaxes(rangebreaks=breaks, tickformat="%b %d", hoverformat="%A, %b %d",
                     row=1, col=1)
    fig.update_yaxes(title_text="Visits per day", range=[0, ymax if ymax else max(ts.max() * 1.15, 3)],
                     title_font=dict(color=AS_BLACK), tickfont=dict(color=AS_BLACK), row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Total visits so far", range=[0, cummax if cummax else max(ts.sum() * 1.05, 3)],
                     showgrid=False, tickmode="auto", nticks=6, title_font=dict(color=AS_LAKE),
                     tickfont=dict(color=AS_LAKE), row=1, col=1, secondary_y=True)
    fig.add_annotation(
        text=(f"{hidden_note}Two scales: the solid black line uses the left axis, the dotted blue line uses the right axis.<br>"
              f"Labels: Weak is below {TREND_CUTS[0]}, Moderate is {TREND_CUTS[0]} to {TREND_CUTS[1]}, "
              f"Strong is above {TREND_CUTS[1]}.<br>"
              f"Direction compares the first and last open week of the smoothed trend; 'Flat' means "
              f"it moved less than {int(DIRECTION_THRESHOLD * 100)}% of the average day."),
        xref="paper", yref="paper", x=0, y=-0.12, showarrow=False, align="left", xanchor="left",
        font=dict(size=11, color=AS_DGRAY))
    fig.update_layout(title=dict(text=title), height=720, margin=dict(l=70, r=70, t=90, b=110),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


# =========================================================
# FIGURES
# =========================================================
figs = {}

# ---------- Overall daily + cumulative ----------
# Days shown on the daily plots: every open weekday except holidays (no visits at all, whole center), plus any
# Friday-Sunday that had visits. The trend labels are still worked out from the regular open days only,
# because the seasonal pattern they use repeats every open week.
daily_all = in_term.groupby("Date").size().reindex(all_days, fill_value=0)
weekend_with_visits = [t for t in all_days if t.dayofweek not in OPEN_WEEKDAYS and daily_all[t] > 0]
holiday_days = [t for t in open_days if daily_all[t] == 0 and t <= last_complete]
plot_days = pd.DatetimeIndex(sorted((set(open_days) | set(weekend_with_visits)) - set(holiday_days)))
ts_all = daily_counts(in_term, plot_days)
ts_open = ts_all[ts_all.index.dayofweek.isin(OPEN_WEEKDAYS)]
partial_last_fc = bool(end == today and len(open_days) and open_days[-1] == today)
ts_fc = daily_counts(d, open_days)       # regular open days only (zeros kept): this is what the forecasts learn from
partial_last = bool(end == today and len(plot_days) and plot_days[-1] == today)
figs["overall"] = visits_figure(
    ts_all, "Daily and Total Visits", stl_summary(ts_open[ts_open.index <= last_complete], int(ts_open[ts_open.index <= last_complete].sum())),
    partial_last=partial_last)

# ---------- Weekly totals ----------
week_starts = pd.date_range(start - pd.Timedelta(days=start.dayofweek), end, freq="7D")
d_weeks = d.assign(Week=d["Date"] - pd.to_timedelta(d["Date"].dt.dayofweek, unit="D"))
weekly = (d_weeks.groupby(["Week", "Type"]).size().unstack(fill_value=0)
          .reindex(week_starts, fill_value=0).reindex(columns=["Drop-in", "Appointment"], fill_value=0))
weekly["Total"] = weekly.sum(axis=1)
weekly["Complete"] = (weekly.index + pd.Timedelta(days=OPEN_WEEKDAYS[-1])) <= last_complete

fig = go.Figure()
shapes = ["" if c else "/" for c in weekly["Complete"]]
for name in ["Drop-in", "Appointment"]:
    fig.add_trace(go.Bar(
        x=weekly.index, y=weekly[name], name=name, marker=dict(color=CENTER_COLORS[name], pattern=dict(shape=shapes)),
        marker_line=dict(width=2, color="white"),
        hovertemplate=name + ": %{y} visits<extra></extra>"))
fig.add_trace(go.Scatter(
    x=weekly.index, y=weekly["Total"], mode="markers+text", text=weekly["Total"].astype(int).astype(str), name="Total",
    marker=dict(size=1, opacity=0),          # invisible marker: keeps the tooltip from showing a stray "Aa" text icon
    textposition="top center", showlegend=False, textfont=dict(family=FONT_BODY, size=13, color=AS_BLACK),
    hovertemplate="Total: %{y:.0f} visits<extra></extra>"))
fig.update_layout(
    title=dict(text="Visits per Week"), barmode="stack", height=480,
    xaxis=dict(title="Week starting (Monday)", tickformat="%b %d", tickvals=weekly.index, hoverformat="Week of %b %d"),
    yaxis=dict(title="Visits", range=[0, max(weekly["Total"].max() * 1.15, 3)]),
    legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=60, r=40, t=90, b=60))
if (~weekly["Complete"]).any():
    fig.add_annotation(text="Striped bar = week still in progress (not a full week yet)", xref="paper", yref="paper",
                       x=1, y=1.12, showarrow=False, xanchor="right", font=dict(size=12, color=AS_DGRAY))
figs["weekly"] = fig

# ---------- Per-subject daily + cumulative ----------
in_plot = in_term[in_term["Date"].isin(plot_days) & (in_term["Date"] <= last_complete)]
subject_totals = in_plot.groupby("Prefix").size().sort_values(ascending=False)
subject_series = {p: daily_counts(in_term[in_term["Prefix"] == p], plot_days) for p in subject_totals.index}
g_max = max([s.max() for s in subject_series.values()] + [1]) + 1
g_cum = max([s.sum() for s in subject_series.values()] + [1]) + 5
subject_figs = []
for p, s in subject_series.items():
    done = s[(s.index <= last_complete) & s.index.dayofweek.isin(OPEN_WEEKDAYS)]
    f = visits_figure(s, f"Daily and Total Visits - {disp(p)}", stl_summary(done, int(done.sum())),
                      ymax=g_max, cummax=g_cum, partial_last=partial_last)
    f.update_layout(height=760)
    subject_figs.append((f"{disp(p)} ({int(subject_totals[p])})", f))

# ---------- Daily forecast: three methods ----------
SEASON = STL_PERIOD                      # one season = one open week (4 open days)
METHOD_COLORS = {"ets": AS_LAKE, "arima": AS_BRICK, "stl": AS_DGOLD}
fc_tabs = []                             # (tab label, figure, note)


CLOSED_TS = pd.DatetimeIndex(pd.to_datetime(CLOSED_DATES)) if CLOSED_DATES else pd.DatetimeIndex([])


def prep(train):
    """Training series for the forecasting methods. An open weekday with zero visits is almost always a holiday or break
    (the center was closed, not 'quiet'), so it is replaced by that weekday's typical value instead of being learned as a
    real drop. Days listed in CLOSED_DATES are treated the same way."""
    tr = np.asarray(train, float).copy()
    idx = ts_fit.index[:len(tr)]
    ok = ~((tr == 0) | np.asarray(idx.isin(CLOSED_TS)))
    if not ok.any() or ok.all():
        return tr
    dow = np.asarray(idx.dayofweek)
    for i in np.where(~ok)[0]:
        same = ok & (dow == dow[i])
        tr[i] = tr[same].mean() if same.any() else tr[ok].mean()
    return tr


def weekday_factors(clean_tr, idx):
    """How busy each weekday usually is compared with the average day (1.0 = average)."""
    dow = np.asarray(idx.dayofweek)
    base = clean_tr.mean() or 1.0
    return {wd: max(float(clean_tr[dow == wd].mean()) / base, 1e-3) if (dow == wd).any() else 1.0 for wd in range(7)}


def ets_fit(train, kind):
    """Exponential smoothing on the weekday-adjusted series (each day divided by how busy its weekday usually is)."""
    kw = {"SES": dict(trend=None), "Holt": dict(trend="add"),
          "Damped": dict(trend="add", damped_trend=True)}[kind]
    clean_tr = prep(train)
    idx = ts_fit.index[:len(clean_tr)]
    wf = weekday_factors(clean_tr, idx)
    adj = clean_tr / np.array([wf[d] for d in idx.dayofweek])
    return ETSModel(pd.Series(adj), error="add", **kw).fit(disp=False), wf


def ets_forecast(train, kind, future_dates):
    res, wf = ets_fit(train, kind)
    mult = np.array([wf[d] for d in pd.DatetimeIndex(future_dates).dayofweek])
    return np.asarray(res.forecast(len(future_dates))) * mult


def auto_sarima(train, max_pq=2, max_PQ=1):
    """Seasonally differenced ARIMA: one seasonal difference (D=1) at the open-week length, regular
    differencing chosen with a KPSS test, then the (p,q,Q) with the lowest AICc. Fitted on the cleaned series
    (closed days replaced); no seasonal AR term, which is not stable with only a few weeks of data."""
    tr = prep(train)
    seasonal_diff = tr[SEASON:] - tr[:-SEASON]
    try:
        d = 1 if kpss(seasonal_diff, regression="c", nlags="auto")[1] < 0.05 else 0
    except Exception:
        d = 0
    best = None
    for p in range(max_pq + 1):
        for q in range(max_pq + 1):
            for Q in range(max_PQ + 1):
                try:
                    res = SARIMAX(tr, order=(p, d, q), seasonal_order=(0, 1, Q, SEASON)).fit(disp=False, maxiter=100)
                    aicc = res.aicc
                except Exception:
                    continue
                if np.isfinite(aicc) and (best is None or aicc < best[0]):
                    best = (aicc, (p, d, q), (0, 1, Q, SEASON), res)
    return best


def stl_naive_fit(train):
    """STL decomposition; the weekly pattern is forecast with a seasonal naive (repeat the latest week) and the
    seasonally adjusted part with simple exponential smoothing, ARIMA(0,1,1)."""
    return STLForecast(pd.Series(prep(train)), ARIMA, model_kwargs=dict(order=(0, 1, 1)),
                       period=SEASON, robust=True).fit()


def cv_metrics(y_all, forecaster):
    """Time series cross-validation: refit on days 1..t-1, forecast day t, for every t from CV_INIT on."""
    actual, guess = [], []
    for t in range(CV_INIT, len(y_all)):
        try:
            f = max(float(np.asarray(forecaster(y_all[:t]))[0]), 0.0)
        except Exception:
            f = np.nan
        if y_all[t] == 0 or ts_fit.index[t] in CLOSED_TS:      # a closed day cannot be 'predicted'; leave it out of the score
            continue
        actual.append(y_all[t])
        guess.append(f)
    e = np.array(actual) - np.array(guess)
    ok = ~np.isnan(e)
    if not ok.any():
        return None
    return dict(ME=float(e[ok].mean()), RMSE=float(np.sqrt((e[ok] ** 2).mean())), MAE=float(np.abs(e[ok]).mean()),
                N=int(ok.sum()))


ts_fit = ts_fc[ts_fc.index <= last_complete]
yv = ts_fit.values.astype(float)
n_fit = len(yv)

if n_fit < MIN_DAYS_FOR_FORECAST:
    fc_tabs.append(("Forecast", make_note_fig(
        "Daily Forecast", f"Not enough open days yet (need {MIN_DAYS_FOR_FORECAST}, have {n_fit})."), ""))
else:
    LOADER.step(0.3, "Testing the forecast methods", ceiling=0.4)
    H = FORECAST_DAYS
    fdates, cur = [], last_complete + pd.Timedelta(days=1)
    while len(fdates) < H:
        if cur.dayofweek in OPEN_WEEKDAYS:
            fdates.append(cur)
        cur += pd.Timedelta(days=1)
    fdates = pd.DatetimeIndex(fdates)

    def make_frame(mean, lo80, hi80, lo95, hi95):
        keep = (~fdates.isin(CLOSED_TS)).astype(float)         # days listed in CLOSED_DATES are forecast as 0
        mean, lo80, hi80, lo95, hi95 = [np.asarray(x, float) * keep for x in (mean, lo80, hi80, lo95, hi95)]
        return pd.DataFrame({"mean": np.maximum(np.asarray(mean, float), 0), "lo80": np.maximum(np.asarray(lo80, float), 0),
                             "hi80": np.maximum(np.asarray(hi80, float), 0), "lo95": np.maximum(np.asarray(lo95, float), 0),
                             "hi95": np.maximum(np.asarray(hi95, float), 0)}, index=fdates)

    # --- 1. Exponential smoothing: SES vs Holt vs damped Holt, chosen by cross-validation ---
    ets_kinds = {"SES": "Simple smoothing (SES)", "Holt": "Holt (trend)", "Damped": "Damped Holt (fading trend)"}
    acc = {}
    for k in ets_kinds:
        acc[ets_kinds[k]] = cv_metrics(yv, lambda tr, k=k: ets_forecast(tr, k, ts_fit.index[len(tr):len(tr) + 1]))
    scored = {k: acc[ets_kinds[k]]["RMSE"] for k in ets_kinds if acc[ets_kinds[k]]}
    best_kind = min(scored, key=scored.get) if scored else "SES"
    ets_res, ets_wf = ets_fit(yv, best_kind)
    ets_mult = np.array([ets_wf[d] for d in fdates.dayofweek])
    p80 = ets_res.get_prediction(start=n_fit, end=n_fit + H - 1).summary_frame(alpha=0.20)
    p95 = ets_res.get_prediction(start=n_fit, end=n_fit + H - 1).summary_frame(alpha=0.05)
    fc_ets = make_frame(p80["mean"] * ets_mult, p80["pi_lower"] * ets_mult, p80["pi_upper"] * ets_mult,
                        p95["pi_lower"] * ets_mult, p95["pi_upper"] * ets_mult)

    # --- 2. Seasonally differenced ARIMA ---
    sar = auto_sarima(yv, 2, 1)
    fc_sar = None
    if sar is not None:
        sres = sar[3]
        fcs = sres.get_forecast(H)
        c80, c95 = np.asarray(fcs.conf_int(alpha=0.20)), np.asarray(fcs.conf_int(alpha=0.05))
        fc_sar = make_frame(np.asarray(fcs.predicted_mean), c80[:, 0], c80[:, 1], c95[:, 0], c95[:, 1])
        acc["Seasonal ARIMA"] = cv_metrics(yv, lambda tr: auto_sarima(tr, 1, 1)[3].forecast(1))

    # --- 3. STL decomposition + seasonal naive / naive ---
    fc_stl = None
    try:
        stl_res = stl_naive_fit(yv)
        q80 = stl_res.get_prediction(start=n_fit, end=n_fit + H - 1).summary_frame(alpha=0.20)
        q95 = stl_res.get_prediction(start=n_fit, end=n_fit + H - 1).summary_frame(alpha=0.05)
        fc_stl = make_frame(q80["mean"], q80["mean_ci_lower"], q80["mean_ci_upper"],
                            q95["mean_ci_lower"], q95["mean_ci_upper"])
        acc["STL + naive"] = cv_metrics(yv, lambda tr: stl_naive_fit(tr).forecast(1))
    except Exception:
        pass

    acc["Simple average (last 4 weeks)"] = cv_metrics(yv, lambda tr: [float(np.mean(prep(tr)[-4 * len(OPEN_WEEKDAYS):]))])
    methods = [("Exponential smoothing", "ets", fc_ets)]
    if fc_sar is not None:
        methods.append(("Seasonal ARIMA", "arima", fc_sar))
    if fc_stl is not None:
        methods.append(("STL + naive", "stl", fc_stl))

    def day_label(t):
        return f"{t:%a %b %d}"

    def history_traces(fig, row, col):
        fig.add_trace(go.Scatter(
            x=ts_fit.index, y=ts_fit.values, mode="lines+markers", name="Actual visits",
            line=dict(width=2, color=C_INK), marker=dict(size=8, color=AS_GOLD, line=dict(width=1.5, color=C_INK)),
            hovertemplate="Actual visits: %{y:.0f}<extra></extra>"), row=row, col=col)
        if partial_last_fc:
            fig.add_trace(go.Scatter(
                x=[ts_fc.index[-1]], y=[ts_fc.iloc[-1]], mode="markers", name="Today (day not finished)",
                marker=dict(size=11, color="white", line=dict(width=2, color=C_INK)),
                hovertemplate="Today so far: %{y:.0f}<extra></extra>"), row=row, col=col)

    def forecast_traces(fig, name, key, fc, row, col, with_range=False):
        color = METHOD_COLORS[key]
        fx = list(fc.index)
        fig.add_trace(go.Scatter(                         # dashed link from the last actual day (no hover)
            x=[ts_fit.index[-1], fx[0]], y=[yv[-1], float(fc["mean"].iloc[0])], mode="lines",
            line=dict(width=3, color=color, dash="dash"), showlegend=False, hoverinfo="skip"), row=row, col=col)
        if with_range:
            custom = np.stack([fc["lo80"], fc["hi80"]], axis=-1)
            tmpl = name + ": about %{y:.0f} visits (likely %{customdata[0]:.0f} to %{customdata[1]:.0f})<extra></extra>"
        else:
            custom, tmpl = None, name + ": about %{y:.0f} visits<extra></extra>"
        fig.add_trace(go.Scatter(
            x=fx, y=fc["mean"], mode="lines+markers", name=name, customdata=custom,
            line=dict(width=3, color=color, dash="dash"), marker=dict(size=7, color=color),
            hovertemplate=tmpl), row=row, col=col)

    def style_axes(fig, row, col):
        fig.update_xaxes(rangebreaks=[dict(bounds=["fri", "mon"])], tickformat="%b %d", hoverformat="%A, %b %d",
                         row=row, col=col)
        fig.update_yaxes(title_text="Visits per day", rangemode="tozero", row=row, col=col)

    # --- Compare tab ---
    fig = make_subplots(
        rows=2, cols=2, row_heights=[0.44, 0.56], vertical_spacing=0.08, horizontal_spacing=0.04,
        specs=[[{"type": "xy", "colspan": 2}, None], [{"type": "table"}, {"type": "table"}]])
    history_traces(fig, 1, 1)
    for label, key, fc in methods:
        forecast_traces(fig, label, key, fc, 1, 1)
    style_axes(fig, 1, 1)
    cols = {"Day": [day_label(t) for t in fdates] + ["Total"]}
    for label, key, fc in methods:
        cols[label] = [f"{v:.0f}" for v in fc["mean"]] + [f"{fc['mean'].sum():.0f}"]
    fig.add_trace(go.Table(header=table_header(list(cols), 12),
                           cells=table_cells(list(cols.values()), 12, height=24)), row=2, col=1)
    acc_rows = [(k, v) for k, v in acc.items() if v]
    best_overall = min(acc_rows, key=lambda kv: kv[1]["RMSE"])[0] if acc_rows else None
    shade = ["#FFF1B3" if k == best_overall else "white" for k, _ in acc_rows]
    fig.add_trace(go.Table(
        header=table_header(["Method", "Average miss", "RMSE", "Bias"], 12),
        cells=table_cells([[k for k, _ in acc_rows], [f"{v['MAE']:.1f}" for _, v in acc_rows],
                           [f"{v['RMSE']:.1f}" for _, v in acc_rows], [f"{v['ME']:+.1f}" for _, v in acc_rows]],
                          12, fill=[shade] * 4, height=24)), row=2, col=2)
    fig.update_layout(title=dict(text=f"Daily Visits Forecast: Next {H} Open Days"), height=1020,
                      margin=dict(l=70, r=40, t=120, b=40), legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    n_folds = acc_rows[0][1]["N"] if acc_rows else 0
    compare_note = (
        f"Each method forecasts the next {H} open days (three open weeks, Monday to Thursday). The table on the right "
        f"is a past-accuracy test: for each of the {n_folds} days since day {CV_INIT + 1}, every method was refit using "
        "only the earlier days and asked to guess that day (one day ahead). 'Average miss' is how many visits off its "
        "guess typically was, RMSE is similar but punishes big misses harder, and Bias is positive when a method tends "
        "to guess too low. The highlighted row had the lowest RMSE. Daily visits are noisy, so expect misses of several "
        "visits a day from any method, and a method that wins a short test is not guaranteed to keep winning. The "
        "'Simple average' row is the bar to beat: it just predicts the average of the last four open weeks every day. "
        "Days with zero visits on a normal open weekday are treated as closed (holiday or break): they are left out of "
        "what the methods learn from and out of the accuracy score. To give the forecast a known closed day, add it to "
        "CLOSED_DATES in the script settings. Exam weeks and other busy or quiet periods are not known to the methods.")
    fc_tabs.append(("Compare methods", fig, compare_note))

    # --- One tab per method, with 80% and 95% ranges ---
    def method_fig(label, key, fc):
        f = make_subplots(rows=1, cols=2, column_widths=[0.68, 0.32], horizontal_spacing=0.04,
                          specs=[[{"type": "xy"}, {"type": "table"}]])
        fx = list(fc.index)
        r, g, b = [int(METHOD_COLORS[key][i:i + 2], 16) for i in (1, 3, 5)]
        for lo, hi, nm, a in [("lo95", "hi95", "95% range", 0.16), ("lo80", "hi80", "80% range", 0.32)]:
            f.add_trace(go.Scatter(x=fx + fx[::-1], y=list(fc[hi]) + list(fc[lo][::-1]), mode="lines", fill="toself",
                                   fillcolor=f"rgba({r},{g},{b},{a})", line=dict(width=0), name=nm, hoverinfo="skip"),
                        row=1, col=1)
        history_traces(f, 1, 1)
        forecast_traces(f, "Forecast", key, fc, 1, 1, with_range=True)
        style_axes(f, 1, 1)
        f.add_trace(go.Table(
            header=table_header(["Day", "Expected visits", "Likely range (80%)"], 12),
            cells=table_cells([[day_label(t) for t in fx], [f"{v:.0f}" for v in fc["mean"]],
                               [f"{a:.0f} to {b:.0f}" for a, b in zip(fc["lo80"], fc["hi80"])]], 12, height=24)),
            row=1, col=2)
        f.update_layout(title=dict(text=f"{label}: Daily Visits Forecast"), height=640,
                        margin=dict(l=70, r=40, t=120, b=50), legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        return f

    def num(x):
        return f"{x:.2f}" if x is not None and np.isfinite(x) else "not used"

    ets_note = (
        "Exponential smoothing gives the most weight to the most recent days. Each day is first divided by how busy its "
        "weekday usually is, so Mondays and Thursdays are not mistaken for rises and falls, and the weekday pattern is put "
        "back on the forecast. Three versions were tested with the same "
        "past-accuracy test as in the Compare tab: simple smoothing (SES, a flat level), Holt (level plus a straight-line "
        f"trend) and damped Holt (a trend that fades out). The one with the lowest RMSE, {ets_kinds[best_kind]}, is "
        f"shown here. Its settings: level smoothing (alpha) = {num(getattr(ets_res, 'smoothing_level', None))}, "
        f"trend smoothing (beta) = {num(getattr(ets_res, 'smoothing_trend', None))}, "
        f"damping (phi) = {num(getattr(ets_res, 'damping_trend', None))}. "
        "An alpha near 0 means the model treats all days about equally instead of chasing the latest day; "
        "an alpha near 1 means it follows the latest day closely.")
    fc_tabs.append(("Exponential smoothing", method_fig("Exponential smoothing", "ets", fc_ets), ets_note))
    if fc_sar is not None:
        (pp, dd, qq), (PP, DD, QQ, ss) = sar[1], sar[2]
        arima_note = (
            f"This method compares each day with the same weekday one week earlier (a seasonal difference over the "
            f"{SEASON} open days in a week), then models what is left. The settings were chosen automatically: "
            f"ARIMA({pp},{dd},{qq})({PP},{DD},{QQ})[{ss}], where the {SEASON} in brackets is the number of open days "
            "per week. It tends to repeat the weekly pattern it saw recently.")
        fc_tabs.append(("Seasonal ARIMA", method_fig("Seasonal ARIMA", "arima", fc_sar), arima_note))
    if fc_stl is not None:
        stl_note = (
            "This method splits the daily visits into a repeating weekly pattern, a smooth trend and leftover noise. "
            "It repeats the most recent weekly pattern (seasonal naive) and projects the weekday-adjusted level with "
            "simple exponential smoothing instead of a pure naive. A pure naive copies the last day, so one busy or quiet "
            "day would carry straight into the whole forecast; smoothing averages over recent days. Repeating only the "
            "latest week's pattern still makes this the noisiest of the three while only a few weeks of data exist.")
        fc_tabs.append(("STL + naive", method_fig("STL + naive", "stl", fc_stl), stl_note))

    # --- Forecast report card: how would each method have done in past weeks? ---
    # At the end of each past week, refit every method using only the data up to that point, forecast the next open
    # week, then compare with what really happened. Same procedures as the forecast tabs (ARIMA uses the smaller
    # search grid to keep run time short).
    BENCH_DAYS = 4 * len(OPEN_WEEKDAYS)                       # benchmark: average of the previous four open weeks
    week_len = len(OPEN_WEEKDAYS)
    origins = [i for i in range(CV_INIT, n_fit - week_len + 1)
               if ts_fit.index[i].dayofweek == OPEN_WEEKDAYS[0]]
    if len(origins) >= 1:
        LOADER.step(0.34, "Testing the forecast methods", ceiling=0.4)
        names = {"ets": "Exponential smoothing", "arima": "Seasonal ARIMA", "stl": "STL + naive", "bench": "Simple average"}
        bt = {k: [] for k in names}
        bt_dates, bt_actual, bt_week = [], [], []
        for _k, i in enumerate(origins):
            LOADER.step(0.34 + 0.5 * _k / len(origins), "Testing the forecast methods", ceiling=0.34 + 0.5 * (_k + 1) / len(origins))
            train = yv[:i]
            preds = {}
            try:
                scores = {k: (cv_metrics(train, lambda tr, k=k: ets_forecast(tr, k, ts_fit.index[len(tr):len(tr) + 1])) or {}).get("RMSE", np.inf)
                          for k in ets_kinds}
                kind = min(scores, key=scores.get) if np.isfinite(min(scores.values())) else "SES"
                preds["ets"] = np.asarray(ets_forecast(train, kind, ts_fit.index[i:i + week_len]), float)
            except Exception:
                preds["ets"] = np.full(week_len, np.nan)
            try:
                preds["arima"] = np.asarray(auto_sarima(train, 1, 1)[3].forecast(week_len), float)
            except Exception:
                preds["arima"] = np.full(week_len, np.nan)
            try:
                preds["stl"] = np.asarray(stl_naive_fit(train).forecast(week_len), float)
            except Exception:
                preds["stl"] = np.full(week_len, np.nan)
            preds["bench"] = np.full(week_len, float(np.mean(prep(train)[-BENCH_DAYS:])))
            for k in names:
                bt[k].extend(np.maximum(preds[k], 0))
            bt_dates.extend(ts_fit.index[i:i + week_len])
            bt_actual.extend(yv[i:i + week_len])
            bt_week.extend([ts_fit.index[i]] * week_len)
        LOADER.step(0.86, "Building the forecast report card", ceiling=0.9)
        bt_actual = np.array(bt_actual, float)
        bt = {k: np.array(v, float) for k, v in bt.items()}
        bt_week = pd.DatetimeIndex(bt_week)

        open_mask = (bt_actual > 0) & ~np.asarray(pd.DatetimeIndex(bt_dates).isin(CLOSED_TS))
        card = {}
        for k in names:
            err = (bt_actual - bt[k])[open_mask]                       # closed days (0 visits) are not scored
            wk_err = [abs(bt_actual[(bt_week == w) & open_mask].sum() - bt[k][(bt_week == w) & open_mask].sum())
                      for w in sorted(set(bt_week))]
            card[k] = dict(day=float(np.nanmean(np.abs(err))), week=float(np.nanmean(wk_err)), bias=float(np.nanmean(err)))
        ranked = sorted([k for k in names if k != "bench"], key=lambda k: card[k]["day"])
        n_weeks = len(origins)

        def verdict(k):
            if k == "bench":
                return "Reference point"
            if not np.isfinite(card[k]["day"]):
                return "Not available"
            ratio = card[k]["day"] / card["bench"]["day"] if card["bench"]["day"] else np.inf
            return "Better than average" if ratio < 0.97 else "Worse than average" if ratio > 1.03 else "About the same as average"

        fig = make_subplots(
            rows=2, cols=2, row_heights=[0.55, 0.45], vertical_spacing=0.10, horizontal_spacing=0.04,
            specs=[[{"type": "xy", "colspan": 2}, None], [{"type": "table"}, {"type": "table"}]])
        fig.add_trace(go.Scatter(
            x=bt_dates, y=bt_actual, mode="lines+markers", name="What actually happened",
            line=dict(width=3, color=C_INK), marker=dict(size=8, color=AS_GOLD, line=dict(width=1.5, color=C_INK)),
            hovertemplate="%{y:.0f} visits<extra>What actually happened</extra>"), row=1, col=1)
        styles = {"ets": METHOD_COLORS["ets"], "arima": METHOD_COLORS["arima"], "stl": METHOD_COLORS["stl"], "bench": C_GRAY}
        for k in names:
            fig.add_trace(go.Scatter(
                x=bt_dates, y=bt[k], mode="lines+markers", name=names[k],
                line=dict(width=2, color=styles[k], dash="dot" if k == "bench" else "dash"), marker=dict(size=6, color=styles[k]),
                hovertemplate="about %{y:.0f} visits<extra>" + names[k] + "</extra>"), row=1, col=1)
        fig.update_xaxes(rangebreaks=[dict(bounds=["fri", "mon"])], tickformat="%b %d", hoverformat="%A, %b %d",
                         row=1, col=1)
        fig.update_yaxes(title_text="Visits per day", rangemode="tozero", row=1, col=1)
        order = ranked + ["bench"]
        fig.add_trace(go.Table(
            header=table_header(["Method", "Typical miss per day", "Typical miss per week", "Verdict"], 12),
            cells=table_cells([[names[k] for k in order], [f"{card[k]['day']:.1f} visits" for k in order],
                               [f"{card[k]['week']:.0f} visits" for k in order], [verdict(k) for k in order]], 12)),
            row=2, col=1)
        wk_list = sorted(set(bt_week))
        wk_rows = [[f"{w:%b %d}" for w in wk_list], [f"{bt_actual[bt_week == w].sum():.0f}" for w in wk_list]]
        for k in ["ets", "arima", "stl"]:
            wk_rows.append([f"{bt[k][bt_week == w].sum():.0f}" for w in wk_list])
        fig.add_trace(go.Table(
            header=table_header(["Week starting", "Actual visits", "Exp. smoothing", "Seasonal ARIMA", "STL + naive"], 12),
            cells=table_cells(wk_rows, 12)), row=2, col=2)
        fig.update_layout(title=dict(text="Forecast Report Card: Past Weeks", font=dict(size=22)), height=900,
                          margin=dict(l=70, r=40, t=120, b=40),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        best = ranked[0]
        beat = [names[k] for k in ranked if verdict(k) == "Better than average"]
        card_note = (
            f"For each of the last {n_weeks} full week{'s' if n_weeks != 1 else ''}, every method was given only the data "
            "from before that week, asked to forecast the week, and then compared with what really happened. "
            "'Typical miss' is the average gap between the forecast and reality. 'Simple average' just predicts the "
            f"average of the previous four weeks every day; a method has to beat it to be worth using. "
            f"Of the three forecasting methods, {names[best]} had the smallest daily miss ({card[best]['day']:.1f} visits, "
            f"versus {card['bench']['day']:.1f} for the simple average). "
            + (f"{', '.join(beat)} beat the simple average. " if beat else "None of the methods clearly beat the simple average yet. ")
            + f"Only {n_weeks} week{'s have' if n_weeks != 1 else ' has'} been tested, so treat this as a rough guide, not a "
            "final ranking; it gets more reliable as the term goes on.")
        fc_tabs.append(("Report card", fig, card_note))

# ---------- Weekday averages ----------
n_per_weekday = pd.Series(complete_days.dayofweek).value_counts()          # open days only (used by the heat maps)
ALL_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
term_days = pd.date_range(start, last_complete) if last_complete >= start else pd.DatetimeIndex([])
n_all_weekday = pd.Series(term_days.dayofweek).value_counts()
in_term_done = in_term[in_term["Date"] <= last_complete]
shown_days = [wd for wd in range(7) if wd in OPEN_WEEKDAYS or (in_term_done["Date"].dt.dayofweek == wd).any()]
extra_days = [ALL_DAY_NAMES[wd] for wd in shown_days if wd not in OPEN_WEEKDAYS]
weekend_note = ""
if extra_days:
    weekend_note = (f" {', '.join(extra_days)} {'is' if len(extra_days) == 1 else 'are'} not regular open days, but "
                    "visits happened on them, so they are included. Their bars are averaged over every one of those "
                    "days this term, including days with no visits, so they look low next to regular open days.")
rows = []
for wd in shown_days:
    nd = int(n_all_weekday.get(wd, 0))
    for t in ["Drop-in", "Appointment"]:
        sub = in_term_done[(in_term_done["Date"].dt.dayofweek == wd) & (in_term_done["Type"] == t)]
        rows.append(dict(Weekday=ALL_DAY_NAMES[wd], Type=t, Days=nd,
                         AvgHours=sub["Hours"].sum() / nd if nd else 0, AvgVisits=len(sub) / nd if nd else 0))
wd_df = pd.DataFrame(rows)
fig = go.Figure()
for t in ["Drop-in", "Appointment"]:
    s = wd_df[wd_df["Type"] == t]
    fig.add_trace(go.Bar(
        x=s["Weekday"], y=s["AvgHours"], name=t, marker=dict(color=CENTER_COLORS[t]), marker_line=dict(width=2, color="white"),
        customdata=np.stack([s["AvgVisits"], s["Days"]], axis=-1),
        hovertemplate=t + ": %{y:.1f} hours and %{customdata[0]:.1f} visits per day (average of %{customdata[1]} days)"
                      "<extra></extra>"))
fig.update_layout(barmode="group", height=500, title=dict(text="Average Tutoring Hours per Day, by Weekday"),
                  xaxis_title="Weekday", yaxis_title="Average hours per day", legend=dict(orientation="h", y=1.1, x=0),
                  margin=dict(l=60, r=40, t=90, b=60))
figs["weekday"] = fig

# ---------- Heat maps (averages) ----------
weeks_done = max(len(complete_days) / len(OPEN_WEEKDAYS), 1e-9)


def _zmax(m):
    """Largest value of a heat-map table; 1 when the table is empty so an all-blank column can't crash the page."""
    return float(m.values.max()) if m.size else 1.0


def slot_label(ts):
    return ts.strftime("%I:%M %p").lstrip("0")


if len(d_done):
    d_done = d_done.assign(Slot=d_done["Start"].dt.floor("30min"))
    lo_h, hi_h = int(d_done["Slot"].dt.hour.min()), int(d_done["Slot"].dt.hour.max())
    slots = pd.date_range(f"2000-01-01 {lo_h:02d}:00", f"2000-01-01 {hi_h:02d}:30", freq="30min")
    time_order = [slot_label(t) for t in slots]
    d_done["Time"] = d_done["Slot"].apply(slot_label)
    greens = [[0.0, "#e8f5e9"], [0.2, "#66bb6a"], [0.4, "#43a047"], [0.6, "#2e7d32"], [0.8, "#1b5e20"], [1.0, "#0f4412"]]

    # subject x time of day
    d_done["SubjectLabel"] = d_done["Prefix"].apply(disp)
    mat = (d_done.groupby(["SubjectLabel", "Time"]).size().unstack(fill_value=0)
           .reindex(columns=time_order, fill_value=0))
    mat = mat.loc[sorted(mat.index, key=str.upper)] / weeks_done
    fig = go.Figure(go.Heatmap(
        z=mat.values, x=mat.columns, y=mat.index, colorscale=greens, zmin=0, zmax=_zmax(mat),
        colorbar=dict(title="Avg visits<br>per week", outlinewidth=0, thickness=14), xgap=1, ygap=1,
        hovertemplate="%{y} visits starting at %{x}<br>%{z:.2f} per week on average<extra></extra>"))
    fig.update_layout(title=dict(text="When Each Subject Is Busy (average visits per week)"),
                      yaxis=dict(title="Subject", type="category", autorange="reversed"),
                      xaxis=dict(title="Time the visit started"),
                      height=min(700, max(450, 28 * len(mat) + 170)), margin=dict(l=90, r=40, t=80, b=90))
    figs["heat_subject"] = fig

    # weekday x time of day
    d_done["WeekdayLabel"] = d_done["Date"].dt.dayofweek.map(dict(zip(OPEN_WEEKDAYS, WEEKDAY_NAMES)))
    mat2 = (d_done.groupby(["WeekdayLabel", "Time"]).size().unstack(fill_value=0)
            .reindex(index=WEEKDAY_NAMES, columns=time_order, fill_value=0))
    days_each = np.array([max(int(n_per_weekday.get(wd, 0)), 1) for wd in OPEN_WEEKDAYS])
    mat2 = mat2.div(days_each, axis=0)
    fig = go.Figure(go.Heatmap(
        z=mat2.values, x=mat2.columns, y=mat2.index, colorscale=greens, zmin=0, zmax=_zmax(mat2),
        colorbar=dict(title="Avg visits<br>per day", outlinewidth=0, thickness=14), xgap=1, ygap=1,
        hovertemplate="%{y} at %{x}<br>%{z:.2f} visits starting per day on average<extra></extra>"))
    fig.update_layout(title=dict(text="Busiest Days and Times (average visits per day)"),
                      yaxis=dict(autorange="reversed"), xaxis=dict(title="Time the visit started"),
                      height=420, margin=dict(l=90, r=40, t=80, b=90))
    figs["heat_weekday"] = fig

    # same plot for drop-in only (blue) and appointment-based only (red), on one shared colour scale so they compare fairly
    mats_t = {}
    for typ in ("Drop-in", "Appointment"):
        sub = d_done[d_done["Type"] == typ]
        m = (sub.groupby(["WeekdayLabel", "Time"]).size().unstack(fill_value=0)
             .reindex(index=WEEKDAY_NAMES, columns=time_order, fill_value=0)).div(days_each, axis=0)
        mats_t[typ] = m
    zmax_t = max(_zmax(m) for m in mats_t.values()) or 1.0
    for typ, base, key, word in (("Drop-in", C_DROPIN, "heat_weekday_dropin", "Drop-in"),
                                 ("Appointment", C_APPT, "heat_weekday_appt", "Appointment-based")):
        m = mats_t[typ]
        fig = go.Figure(go.Heatmap(
            z=m.values, x=m.columns, y=m.index, colorscale=[[0.0, "#ffffff"], [1.0, base]], zmin=0, zmax=zmax_t,
            colorbar=dict(title="Avg visits<br>per day", outlinewidth=0, thickness=14), xgap=1, ygap=1,
            hovertemplate="%{y} at %{x}<br>%{z:.2f} " + word.lower() + " visits starting per day on average<extra></extra>"))
        fig.update_layout(title=dict(text=f"{word} Only: Busiest Days and Times (average visits per day)"),
                          yaxis=dict(autorange="reversed"), xaxis=dict(title="Time the visit started"),
                          height=420, margin=dict(l=90, r=40, t=80, b=90))
        figs[key] = fig
heat_note = ("These show when visits start, not how many tutors were working. A quiet slot can mean few students "
             "or few tutors on shift; staffing data would be needed to tell which.")

# ---------- Returning students ----------
if has_people:
    v = df.dropna(subset=["Student"]).sort_values("Start")
    v = v[v["Date"] >= start]
    v["Gap"] = v.groupby(["Student", "Prefix"])["Start"].diff().dt.total_seconds() / 86400
    gaps = v.dropna(subset=["Gap"])
    if len(gaps):
        agg = gaps.groupby("Prefix").agg(Mean=("Gap", "mean"), Median=("Gap", "median"),
                                         Gaps=("Gap", "size"), Students=("Student", "nunique"))
        agg.index = [disp(i) for i in agg.index]
        agg = agg.sort_values("Mean")
        overall = pd.DataFrame([dict(Mean=gaps["Gap"].mean(), Median=gaps["Gap"].median(), Gaps=len(gaps),
                                     Students=gaps["Student"].nunique())], index=["All subjects"])
        agg = pd.concat([agg, overall])
        colors = [AS_LAKE if g >= MIN_GAPS_RELIABLE else "rgba(3,101,156,0.35)" for g in agg["Gaps"]]
        colors[-1] = C_GRAY
        labels = [f"{i}" + ("" if g >= MIN_GAPS_RELIABLE else " *") for i, g in zip(agg.index, agg["Gaps"])]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=labels, x=agg["Mean"], orientation="h", name="Average days between visits", marker=dict(color=colors),
            marker_line=dict(width=2, color="white"),
            customdata=np.stack([agg["Median"], agg["Gaps"], agg["Students"]], axis=-1),
            hovertemplate="Average: %{x:.1f} days<br>Typical (median): %{customdata[0]:.1f} days"
                          "<br>Based on %{customdata[1]} repeat visits from %{customdata[2]} students<extra></extra>"))
        fig.add_trace(go.Scatter(
            y=labels, x=agg["Median"], mode="markers", name="Typical gap (median)",
            marker=dict(symbol="diamond", size=11, color="white", line=dict(width=2, color=AS_BLACK)), hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            y=labels, x=np.maximum(agg["Mean"], agg["Median"]) + agg["Mean"].max() * 0.02, mode="text",
            text=[f"{m:.1f}" for m in agg["Mean"]], textposition="middle right", showlegend=False, hoverinfo="skip",
            textfont=dict(family=FONT_BODY, size=12, color=AS_BLACK)))
        fig.update_layout(
            title=dict(text="Days Between Visits for Returning Students"),
            xaxis=dict(title="Days from one visit to that student's next visit in the same subject", rangemode="tozero"),
            yaxis=dict(autorange="reversed"), height=max(450, 32 * len(agg) + 180),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=110, r=60, t=120, b=70))
        figs["gaps"] = fig
        gaps_note = (f"Only students with two or more visits in the same subject are included. * = fewer than {MIN_GAPS_RELIABLE} "
                     "repeat visits, so treat that bar as a rough guess. The diamond is the typical (median) gap; where it sits "
                     "well left of the bar, a few long gaps (like a break) are pulling the average up.")
    else:
        figs["gaps"] = make_note_fig("Days Between Visits for Returning Students", "No student has two visits in the same subject yet.")
        gaps_note = ""

    # first consultant for returning students
    rep = df.dropna(subset=["Student", "Consultant"])
    rep = rep[rep["Student"].duplicated(keep=False)]
    first = rep.loc[rep.groupby("Student")["Start"].idxmin()]
    cc = first.groupby(["Consultant", "Type"]).size().reset_index(name="Count")
    for t, key in [("Drop-in", "first_dropin"), ("Appointment", "first_appt")]:
        s = cc[cc["Type"] == t]
        if s.empty:
            figs[key] = make_note_fig(f"First Consultant for Returning Students - {t}", "No data yet.")
            continue
        order = s.sort_values("Count", ascending=False)["Consultant"].tolist()
        f = px.bar(s, x="Consultant", y="Count", color="Type", color_discrete_map=CENTER_COLORS,
                   title=f"First Consultant for Returning Students - {t}")
        f.update_traces(marker_line=dict(width=2, color="white"),
                        hovertemplate="%{y} returning students first met them<extra></extra>")
        f.update_layout(height=520, showlegend=False, yaxis_title="Returning students",
                        margin=dict(l=60, r=40, t=70, b=140), xaxis=dict(categoryorder="array", categoryarray=order))
        figs[key] = f
else:
    msg = "This file has no student names, so this plot can't be built."
    figs["gaps"] = make_note_fig("Days Between Visits for Returning Students", msg)
    figs["first_dropin"] = make_note_fig("First Consultant for Returning Students", msg)
    figs["first_appt"] = make_note_fig("First Consultant for Returning Students - Appointment", msg)
    gaps_note = ""

# ---------- Consultant hours ----------
consultant_figs = []
if has_consultants:
    cdf = df.dropna(subset=["Consultant"])

    def hours_fig(frame, title, crla=False):
        piv = frame.pivot_table(index="Consultant", columns="Type", values="Hours", aggfunc="sum", fill_value=0)
        piv = piv.reindex(columns=["Drop-in", "Appointment"], fill_value=0)
        piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
        f = go.Figure()
        for t in ["Drop-in", "Appointment"]:
            f.add_trace(go.Bar(x=piv.index, y=piv[t], name=t, marker=dict(color=CENTER_COLORS[t]),
                               marker_line=dict(width=2, color="white"),
                               hovertemplate=t + ": %{y:.1f} hours<extra></extra>"))
        if crla:
            f.add_trace(go.Scatter(x=[0, 1], y=[CRLA_HOURS, CRLA_HOURS], xaxis="x2", mode="lines", name="CRLA hour requirement",
                                   line=dict(color=AS_DGRAY, dash="dot", width=2), hoverinfo="skip", visible="legendonly"))
            f.update_layout(xaxis2=dict(overlaying="x", range=[0, 1], visible=False, fixedrange=True))
        f.update_layout(barmode="stack", height=560, title=dict(text=title), yaxis_title="Total hours",
                        legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=60, r=40, t=90, b=140))
        return f

    consultant_figs.append(("All", hours_fig(cdf, "All Consultant Hours", crla=True)))
    for g in GROUP_MAPPING:
        sub = cdf[cdf["Group"] == g]
        if len(sub):
            consultant_figs.append((g, hours_fig(sub, f"{g} Consultant Hours")))
else:
    consultant_figs.append(("All", make_note_fig("Consultant Hours", "This file has no consultant names, so this plot can't be built.")))

TUTOR_LOOKUP_CSS = """<style>
.tl{max-width:1000px;margin:6px auto 24px;font-size:14px;line-height:1.55}
.tl .box{background:#fff;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.14);padding:14px 20px 16px;margin:0 0 14px;border-left:6px solid #FFCC00}
.tl h3{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;font-size:16px;margin:2px 0 8px}
.tl input[type=text]{width:100%;box-sizing:border-box;padding:10px 12px;font:15px Montserrat,Arial,sans-serif;border:2px solid #cfcfcf;border-radius:4px}
.tl input[type=text]:focus{outline:none;border-color:#FFCC00}
.tl .hint{font-size:12px;color:#4c4847;margin-top:6px}
.tl .chips{display:flex;flex-wrap:wrap;gap:5px;margin:6px 0}
.tl .chip{border:1px solid #bbb;background:#fff;border-radius:14px;padding:3px 10px;font:12px Montserrat,Arial,sans-serif;cursor:pointer}
.tl .chip.on{background:#FFCC00;border-color:#FFCC00;font-weight:600}
.tl .wk{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 4px}
.tl .wk .chip{font-size:13px;padding:5px 14px}
.tl .small{font-size:12px;background:none;border:none;text-decoration:underline;cursor:pointer;color:#4c4847;padding:2px 6px}
.tl table{border-collapse:collapse;width:100%;font-size:13px}
.tl th{text-align:left;font-weight:600;border-bottom:2px solid #FFCC00;padding:5px 8px;white-space:nowrap}
.tl td{padding:5px 8px;border-bottom:1px solid #eee;white-space:nowrap}
.tl tr.me td{background:#fff4c2;font-weight:600}
.tl tr.go{cursor:pointer}.tl tr.go:hover td{background:#faf6e3}
.tl .scorebox{display:flex;flex-wrap:wrap;align-items:center;gap:18px 28px}
.tl .big{font-family:'Archivo Black','Arial Black',sans-serif;font-size:54px;line-height:1}.tl .big span{font-size:22px;color:#4c4847}
.tl .bar{background:#eee;border-radius:4px;height:12px;overflow:hidden;min-width:120px}.tl .bar i{display:block;height:100%;background:#FFCC00}
.tl .comp{display:grid;grid-template-columns:minmax(150px,230px) 1fr 330px;gap:4px 12px;align-items:center;margin:7px 0}
.tl .comp .lab{font-weight:600}.tl .comp .val{font-size:12px;color:#4c4847}
.tl details{margin-top:8px}.tl summary{cursor:pointer;font-weight:600}
.tl .wrow{display:grid;grid-template-columns:minmax(150px,230px) 1fr 34px;gap:4px 12px;align-items:center;font-size:12px;margin:2px 0}
.tl .scroll{overflow-x:auto}
.tl .warn{color:#8a5a00;font-size:12px;margin:4px 0}
@media(max-width:600px){.tl .comp{grid-template-columns:1fr}.tl .wrow{grid-template-columns:1fr 1fr 30px}.tl .big{font-size:44px}}
</style>"""

TUTOR_LOOKUP_JS = r"""<script>
(function(){
var D=__DATA__, MIN_DAYS=5, WNAMES=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
var T=D.tutors, DAYS=D.days, G=D.groups, rows=D.rows, nT=T.length, nD=DAYS.length;
var dayNum=DAYS.map(function(s){var p=s.split('-');return Date.UTC(+p[0],+p[1]-1,+p[2])/86400000;});
var dayWk=DAYS.map(function(s){var p=s.split('-');return (new Date(Date.UTC(+p[0],+p[1]-1,+p[2])).getUTCDay()+6)%7;});
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function dlabel(i){var p=DAYS[i].split('-');return MON[+p[1]-1]+' '+(+p[2]);}
var COMPS=[
 {k:'hours',lab:'Tutoring hours per workday',w:25,fmt:function(v,m){return v.toFixed(1)+' hours a day (days with no visits count as 0)';}},
 {k:'visitdays',lab:'Days with at least one visit',w:20,fmt:function(v,m){return v+' of '+m.nd+' workdays had a visit';}},
 {k:'repeat',lab:'Students who came back to them',w:20,fmt:function(v){return Math.round(v*100)+'% saw this tutor on 2+ days';}},
 {k:'other',lab:'Students who went on to another tutor',w:20,fmt:function(v){return Math.round(v*100)+'% saw a different tutor within 2 weeks';}},
 {k:'range',lab:'Range of subjects',w:15,fmt:function(v){return Math.round(v*100)+'% of subject areas covered';}}
];
// ---- index the visit rows ----
var perT=[],sv={};
for(var t=0;t<nT;t++){perT.push({});}
rows.forEach(function(r){
  var t=r[0],d=r[1],s=r[2],h=r[3],g=r[4],m=r[5];
  var o=perT[t][d]||(perT[t][d]={n:0,h:0,st:{},g:{},min:m,max:m});
  o.n++;o.h+=h;if(s>=0){o.st[s]=1;}o.g[g]=(o.g[g]||0)+1;if(m<o.min){o.min=m;}if(m>o.max){o.max=m;}
  if(s>=0){(sv[s]=sv[s]||{})[d+'|'+t]=1;}
});
var svl={};Object.keys(sv).forEach(function(s){svl[s]=Object.keys(sv[s]).map(function(k){var p=k.split('|');return [dayNum[+p[0]],+p[1]];});});
var lastNum=dayNum[nD-1],cutoff=lastNum-14,MINV=3,boardOpen=true;
function span(t){var ds=Object.keys(perT[t]).map(Number);return ds.length?[Math.min.apply(null,ds),Math.max.apply(null,ds)]:null;}
// a weekday counts as one the tutor works when they have at least MINV visits on it in total, across every week
function weekdayTotals(t){var w=[0,0,0,0,0,0,0];Object.keys(perT[t]).forEach(function(d){w[dayWk[+d]]+=perT[t][d].n;});return w;}
// workdays = every day the center was open, on those weekdays, between the tutor's first and last visit (zero-visit days included)
function workedDays(t){
  var sp=span(t);if(!sp){return [];}
  var tot=weekdayTotals(t),out=[];
  for(var d=sp[0];d<=sp[1];d++){if(tot[dayWk[d]]>=MINV){out.push(d);}}
  return out;
}
function leftOut(t){var tot=weekdayTotals(t),r=[];for(var w=0;w<7;w++){if(tot[w]>0&&tot[w]<MINV){r.push(WNAMES[w]+' ('+tot[w]+')');}}return r;}
function vis(t,d){return perT[t][d]||null;}
// ---- the five measures for one tutor on a set of days ----
function measures(t,days){
  var nd=days.length;if(!nd){return null;}
  var hrs=0,vd=0,tv=0,st={},sess=0,back=0,gc={},m={};
  days.forEach(function(d){
    var o=vis(t,d);if(!o){return;}
    hrs+=o.h;tv+=o.n;vd++;
    Object.keys(o.st).forEach(function(s){
      (st[s]=st[s]||[]).push(d);
      if(dayNum[d]<=cutoff){sess++;var L=svl[s]||[];for(var i=0;i<L.length;i++){if(L[i][1]!==t&&L[i][0]>dayNum[d]&&L[i][0]<=dayNum[d]+14){back++;break;}}}
    });
    Object.keys(o.g).forEach(function(g){gc[g]=(gc[g]||0)+o.g[g];});
  });
  m.hours=hrs/nd;m.visitdays=vd;
  var ns=Object.keys(st).length;
  m.repeat=ns>=3?Object.keys(st).filter(function(s){return st[s].length>=2;}).length/ns:null;
  m.other=sess>=5?back/sess:null;
  m.range=Object.keys(gc).filter(function(g){return gc[g]>=3;}).length/Math.max(G.length,1);
  m.nd=nd;m.totalVisits=tv;m.totalHours=hrs;m.students=ns;
  return m;
}
var eligible=[],full={};
function rebuild(){
  eligible=[];full={};
  for(var t=0;t<nT;t++){var w=workedDays(t);full[t]=measures(t,w);if(w.length>=MIN_DAYS){eligible.push(t);}}
}
rebuild();
function weights(){var w={};COMPS.forEach(function(c){w[c.k]=c.w;});return w;}
function pct(v,list){var less=0,eq=0;list.forEach(function(x){if(x<v-1e-9){less++;}else if(Math.abs(x-v)<=1e-9){eq++;}});return list.length?(less+0.5*eq)/list.length:0.5;}
function scoreOf(m,self){
  var W=weights(),num=0,den=0,parts=[];
  COMPS.forEach(function(c){
    var v=m[c.k];if(v===null||v===undefined){parts.push({c:c,v:null,p:null});return;}
    var peers=[];eligible.forEach(function(t){if(t!==self&&full[t]&&full[t][c.k]!==null){peers.push(full[t][c.k]);}});
    var p=peers.length>=3?pct(v,peers):null;
    parts.push({c:c,v:v,p:p});
    if(p!==null&&W[c.k]>0){num+=W[c.k]*p;den+=W[c.k];}
  });
  return {score:den?10*num/den:null,parts:parts};
}
// ---- page ----
var el=function(id){return document.getElementById(id);};
var cur=null,sel={};
var dl=el('tl-names');T.forEach(function(n){var o=document.createElement('option');o.value=n;dl.appendChild(o);});
function norm(s){return s.toLowerCase().replace(/\s+/g,' ').trim();}
function findTutor(q){
  q=norm(q);if(!q){return -1;}
  var ex=-1,part=[];
  T.forEach(function(n,i){var x=norm(n);if(x===q){ex=i;}else if(x.indexOf(q)>=0){part.push(i);}});
  if(ex>=0){return ex;}
  return part.length===1?part[0]:-1;
}
function pickTutor(t){
  cur=t;sel={};workedDays(t).forEach(function(d){sel[d]=1;});
  el('tl-input').value=T[t];render();
}
el('tl-minv').addEventListener('change',function(){
  var v=Math.max(1,Math.min(50,Math.round(+this.value||3)));this.value=v;MINV=v;rebuild();
  if(cur!==null){sel={};workedDays(cur).forEach(function(d){sel[d]=1;});}render();
});
el('tl-input').addEventListener('input',function(){
  var t=findTutor(this.value);
  if(t>=0){pickTutor(t);}
  else{cur=null;render();var q=norm(this.value);
    var n=q?T.filter(function(x){return norm(x).indexOf(q)>=0;}).length:0;
    el('tl-msg').textContent=q?(n>1?n+' tutors match. Keep typing the first and last name.':n===0?'No tutor with that name in this file.':''):'';}
});
function selDays(){return Object.keys(sel).map(Number).sort(function(a,b){return a-b;});}
function ordinal(n){var s=['th','st','nd','rd'],v=n%100;return n+(s[(v-20)%10]||s[v]||s[0]);}
function median(a){a=a.slice().sort(function(x,y){return x-y;});var n=a.length;return n?(n%2?a[(n-1)/2]:(a[n/2-1]+a[n/2])/2):0;}
function clock(m){var h=Math.floor(m/60),mm=m%60;return ((h+11)%12+1)+(mm?':'+(mm<10?'0':'')+mm:'')+(h<12?' am':' pm');}
function render(){
  var box=el('tl-result');
  if(cur===null){box.innerHTML='';renderBoard();return;}
  var all=workedDays(cur),days=selDays(),h='',lo=leftOut(cur);
  h+='<div class="box"><h3>'+T[cur]+': schedule</h3><div class="hint">A weekday counts as one they work when they have at least '+MINV+' visits on it in total. Every open day of that weekday then counts, including days with no visits. Tap a weekday to include or leave out all of those days, or tap single dates. The score uses the highlighted days.'+(lo.length?' Not counted as workdays: '+lo.join(', ')+' (too few visits).':'')+'</div><div class="wk">';
  if(!all.length){h+='<div class="warn">No weekday has '+MINV+' or more visits for this tutor.</div>';}
  for(var w=0;w<7;w++){
    var mine=all.filter(function(d){return dayWk[d]===w;});if(!mine.length){continue;}
    var on=mine.filter(function(d){return sel[d];}).length;
    h+='<button class="chip'+(on===mine.length?' on':'')+'" data-wk="'+w+'">'+WNAMES[w]+' ('+on+'/'+mine.length+')</button>';
  }
  h+='<button class="small" data-all="1">all days</button><button class="small" data-none="1">none</button></div>';
  h+='<div class="scroll"><table><tr><th>Weekday</th><th>Workdays</th><th>With a visit</th><th>Visits per workday</th><th>Usual visit times</th></tr>';
  for(var w2=0;w2<7;w2++){
    var md=all.filter(function(d){return dayWk[d]===w2;});if(!md.length){continue;}
    var withv=md.filter(function(d){return vis(cur,d);});
    var v=md.reduce(function(a,d){return a+(vis(cur,d)?vis(cur,d).n:0);},0)/md.length;
    h+='<tr><td>'+WNAMES[w2]+'</td><td>'+md.length+'</td><td>'+withv.length+'</td><td>'+v.toFixed(1)+'</td><td>'+(withv.length?clock(Math.round(median(withv.map(function(d){return vis(cur,d).min;}))))+' to '+clock(Math.round(median(withv.map(function(d){return vis(cur,d).max;})))):'')+'</td></tr>';
  }
  h+='</table></div><div class="chips">';
  all.forEach(function(d){var o=vis(cur,d);h+='<button class="chip'+(sel[d]?' on':'')+'" data-d="'+d+'" title="'+(o?o.n+' visits':'no visits')+'">'+dlabel(d)+' ('+WNAMES[dayWk[d]]+(o?'':', 0')+')</button>';});
  h+='</div></div>';
  var m=measures(cur,days);
  if(!m){h+='<div class="box"><b>Pick at least one day to see a score.</b></div>';box.innerHTML=h;bind();renderBoard();return;}
  var sc=scoreOf(m,cur),s=sc.score;
  var board=eligible.filter(function(t){return t!==cur;}).map(function(t){return scoreOf(full[t],t).score;}).filter(function(x){return x!==null;});
  var rank=s===null?null:board.filter(function(x){return x>s;}).length+1;
  h+='<div class="box"><h3>'+T[cur]+': tutor score</h3><div class="scorebox"><div class="big">'+(s===null?'n/a':s.toFixed(1))+'<span> / 10</span></div><div>';
  if(s!==null){h+='<div><b>'+ordinal(rank)+'</b> of '+(board.length+1)+' tutors</div>';}
  h+='<div>'+m.nd+' workday'+(m.nd===1?'':'s')+' counted ('+m.visitdays+' with a visit) &middot; '+m.totalVisits+' visits &middot; '+m.totalHours.toFixed(1)+' tutoring hours &middot; '+m.students+' different students</div>';
  if(m.nd<MIN_DAYS){h+='<div class="warn">Only '+m.nd+' workday'+(m.nd===1?'':'s')+' counted. A score from fewer than '+MIN_DAYS+' is a rough guide.</div>';}
  if(eligible.length<4){h+='<div class="warn">Fewer than 4 tutors have '+MIN_DAYS+'+ workdays yet, so scores are not reliable.</div>';}
  h+='</div></div>';
  sc.parts.forEach(function(p){
    h+='<div class="comp"><div class="lab">'+p.c.lab+'</div><div>';
    h+=p.p===null?'<div class="hint">not enough data</div>':'<div class="bar"><i style="width:'+Math.round(p.p*100)+'%"></i></div>';
    h+='</div><div class="val">'+(p.v===null?'':p.c.fmt(p.v,m))+(p.p===null?'':' &middot; better than '+Math.round(p.p*100)+'% of tutors')+'</div></div>';
  });
  h+='<details><summary>What counts, and how much</summary><div class="hint">Each measure is turned into where the tutor sits among the other tutors (better than half of them = 5 out of 10). The five are averaged using the weights below. Change them to see how the score moves.</div>';
  COMPS.forEach(function(c){h+='<div class="wrow"><div>'+c.lab+'</div><input type="range" min="0" max="50" step="5" value="'+c.w+'" data-w="'+c.k+'"><div>'+c.w+'</div></div>';});
  h+='<div class="hint" style="margin-top:8px"><b>What this does not measure:</b> how well anyone teaches. Everything comes from sign-ins, so a tutor on slow hours or in harder subjects can look lower. Treat the score as one input next to conversations and observation.</div></details></div>';
  box.innerHTML=h;bind();renderBoard();
}
function renderBoard(){
  var rowsB=eligible.map(function(t){var m=(t===cur&&selDays().length)?measures(t,selDays()):full[t];return {t:t,m:m,s:scoreOf(m,t).score};}).filter(function(x){return x.s!==null;});
  rowsB.sort(function(a,b){return b.s-a.s;});
  var h='<div class="box"><details'+(boardOpen?' open':'')+'><summary><b>All tutor rankings ('+rowsB.length+')</b></summary><div class="hint">Tutors with at least '+MIN_DAYS+' workdays this term. Tap a row to open that tutor.</div><div class="scroll"><table><tr><th>#</th><th>Tutor</th><th>Score</th><th>Workdays</th><th>Visits</th><th>Hours</th></tr>';
  rowsB.forEach(function(r,i){h+='<tr class="go'+(r.t===cur?' me':'')+'" data-t="'+r.t+'"><td>'+(i+1)+'</td><td>'+T[r.t]+'</td><td>'+r.s.toFixed(1)+'</td><td>'+r.m.nd+'</td><td>'+r.m.totalVisits+'</td><td>'+r.m.totalHours.toFixed(1)+'</td></tr>';});
  if(!rowsB.length){h+='<tr><td colspan="6">Not enough workdays yet.</td></tr>';}
  else{
    var avg=function(f){return rowsB.reduce(function(a,r){return a+f(r);},0)/rowsB.length;};
    h+='<tr><td></td><td><i>Average tutor</i></td><td>'+avg(function(r){return r.s;}).toFixed(1)+'</td><td>'+avg(function(r){return r.m.nd;}).toFixed(0)+'</td><td>'+avg(function(r){return r.m.totalVisits;}).toFixed(0)+'</td><td>'+avg(function(r){return r.m.totalHours;}).toFixed(1)+'</td></tr>';
  }
  h+='</table></div></details></div>';
  el('tl-board').innerHTML=h;
  var dt=el('tl-board').querySelector('details');if(dt){dt.addEventListener('toggle',function(){boardOpen=dt.open;});}
  el('tl-board').querySelectorAll('tr.go').forEach(function(r){r.addEventListener('click',function(){pickTutor(+r.getAttribute('data-t'));window.scrollTo({top:el('tl-input').getBoundingClientRect().top+window.pageYOffset-80,behavior:'smooth'});});});
}
function bind(){
  var b=el('tl-result');
  b.querySelectorAll('[data-d]').forEach(function(x){x.addEventListener('click',function(){var d=+x.getAttribute('data-d');if(sel[d]){delete sel[d];}else{sel[d]=1;}render();});});
  b.querySelectorAll('[data-wk]').forEach(function(x){x.addEventListener('click',function(){
    var w=+x.getAttribute('data-wk'),mine=workedDays(cur).filter(function(d){return dayWk[d]===w;});
    var allOn=mine.every(function(d){return sel[d];});mine.forEach(function(d){if(allOn){delete sel[d];}else{sel[d]=1;}});render();});});
  var a=b.querySelector('[data-all]');if(a){a.addEventListener('click',function(){workedDays(cur).forEach(function(d){sel[d]=1;});render();});}
  var n=b.querySelector('[data-none]');if(n){n.addEventListener('click',function(){sel={};render();});}
  b.querySelectorAll('input[data-w]').forEach(function(x){x.addEventListener('input',function(){
    COMPS.forEach(function(c){if(c.k===x.getAttribute('data-w')){c.w=+x.value;}});
    x.nextElementSibling.textContent=x.value;
    var keep=document.activeElement&&document.activeElement.getAttribute('data-w');
    render();var again=el('tl-result').querySelector('details');if(again){again.open=true;}
    var back=el('tl-result').querySelector('input[data-w="'+keep+'"]');if(back){back.focus();}});});
}
render();
})();
</script>"""


def tutor_lookup_html():
    """The 'Tutor lookup' subtab: type a tutor's name, choose which days to count, get a score out of 10 and a ranking."""
    import json as _json
    if not has_consultants:
        return ""
    tdf = in_term[(in_term["Date"] <= last_complete) & in_term["Consultant"].notna()].copy()
    tdf["Consultant"] = tdf["Consultant"].astype(str).str.strip()
    if tdf["Consultant"].nunique() < 2:
        return ""
    tutors = sorted(tdf["Consultant"].unique())
    days = sorted(tdf["Date"].dt.strftime("%Y-%m-%d").unique())
    groups = sorted(tdf["Group"].unique())
    t_idx = {n: i for i, n in enumerate(tutors)}
    d_idx = {n: i for i, n in enumerate(days)}
    g_idx = {n: i for i, n in enumerate(groups)}
    students = {}
    out_rows = []
    for name, day, who, hrs, grp, start in zip(tdf["Consultant"], tdf["Date"].dt.strftime("%Y-%m-%d"), tdf["Student"],
                                               tdf["Hours"], tdf["Group"], tdf["Start"]):
        sid = -1
        if pd.notna(who):
            sid = students.setdefault(str(who).strip(), len(students))
        out_rows.append([t_idx[name], d_idx[day], sid, round(float(hrs), 2), g_idx[grp], int(start.hour * 60 + start.minute)])
    data = _json.dumps(dict(tutors=tutors, days=days, groups=groups, rows=out_rows), separators=(",", ":")).replace("</", "<\\/")
    body = ('<div class="tl"><div class="box"><h3>Tutor score lookup</h3>'
            '<input type="text" id="tl-input" list="tl-names" placeholder="Type a tutor\'s first and last name" autocomplete="off">'
            '<datalist id="tl-names"></datalist><div class="hint" id="tl-msg"></div>'
            '<div class="hint">Shows when and how often the tutor worked, then a score out of 10 compared with the other tutors this term.</div>'
            '<div class="hint">A weekday needs at least <input type="number" id="tl-minv" value="3" min="1" max="50" style="width:56px"> visits in total (across all weeks) to count as one this tutor works. Fewer than that is probably a picked-up shift.</div></div>'
            '<div id="tl-result"></div><div id="tl-board"></div></div>')
    return TUTOR_LOOKUP_CSS + body + TUTOR_LOOKUP_JS.replace("__DATA__", data)


# =========================================================
# BUILD THE DASHBOARD PAGE (one HTML file, tabs across the top)
# =========================================================
def stat_cards():
    total = int(len(d_done))
    per_day = total / len(complete_days) if len(complete_days) else 0
    wk = weekly[weekly["Complete"]]["Total"]
    if len(wk) >= 2:
        wk_txt = f"{int(wk.iloc[-1])} vs {int(wk.iloc[-2])}"
        wk_lbl = "Last full week vs the week before"
    else:
        wk_txt, wk_lbl = "-", "Last full week vs the week before"
    cards = [(f"{total:,}", "Visits this term"), (f"{len(complete_days)}", "Open days so far"),
             (f"{per_day:.1f}", "Average visits per open day"), (wk_txt, wk_lbl)]
    return '<div class="cards" id="statcards">' + "".join(f'<div class="card"><div class="big">{a}</div><div>{b}</div></div>' for a, b in cards) + "</div>"


def plain_english_takeaways():
    """A few plain sentences about the data, built from the numbers. Every line is skipped when its data is missing."""
    lines = []
    try:
        per_day = d_done.groupby("Date").size().reindex(complete_days, fill_value=0)
        busy_days = per_day[per_day > 0]                       # closed days (breaks, holidays) should not drag averages down
        total = int(len(d_done))
        if total and len(busy_days):
            lines.append(f"So far this term the center has had <b>{total:,} visits</b> over {len(busy_days)} days with visits, "
                         f"about <b>{total / len(busy_days):.0f} a day</b>.")
        wk = weekly[weekly["Complete"]]["Total"]
        if len(wk) >= 2 and wk.iloc[-2] > 0:
            now_, before = int(wk.iloc[-1]), int(wk.iloc[-2])
            change = (now_ - before) / before * 100
            wk_start = wk.index[-1]
            if abs(change) < 5:
                trend = "about the same as"
            else:
                trend = f"{'up' if change > 0 else 'down'} {abs(change):.0f}% from"
            lines.append(f"The last full week (starting {wk_start:%b %d}) had <b>{now_} visits</b>, {trend} the week before ({before}).")
        # the same numbers as the Busy Times > Weekday averages plot (average per day over every day of that weekday this term)
        wk_tot = wd_df.groupby("Weekday")[["AvgHours", "AvgVisits"]].sum()
        wk_tot = wk_tot.loc[[ALL_DAY_NAMES[w] for w in OPEN_WEEKDAYS if ALL_DAY_NAMES[w] in wk_tot.index]]
        wk_tot = wk_tot[wk_tot["AvgHours"] > 0]
        if len(wk_tot) >= 2:
            hi, lo = wk_tot["AvgHours"].idxmax(), wk_tot["AvgHours"].idxmin()
            lines.append(f"<b>{hi}s are the busiest day</b>: about {wk_tot['AvgHours'][hi]:.0f} hours of tutoring and "
                         f"{wk_tot['AvgVisits'][hi]:.0f} visits on an average {hi}. <b>{lo}s are the quietest</b> "
                         f"({wk_tot['AvgHours'][lo]:.0f} hours, {wk_tot['AvgVisits'][lo]:.0f} visits). "
                         "Hours and visits are both shown on the Busy Times tab (hover over a bar).")
        if len(busy_days) >= 5:
            top = busy_days.idxmax()
            lines.append(f"The single busiest day was <b>{top:%A, %b %d}</b> with {int(busy_days.max())} visits.")
        hours = d_done["Start"].dt.hour.value_counts()
        if len(hours) >= 3 and hours.sum() >= 30:
            h = int(hours.idxmax())
            def clock(x):
                return f"{(x - 1) % 12 + 1} {'am' if x < 12 else 'pm'}"
            lines.append(f"Visits start most often between <b>{clock(h)} and {clock(h + 1)}</b> "
                         f"({hours.max() / hours.sum() * 100:.0f}% of all visits).")
        groups = d_done["Group"].value_counts()
        if len(groups) >= 2:
            lines.append(f"<b>{groups.index[0]}</b> is the most common subject area ({groups.iloc[0] / groups.sum() * 100:.0f}% of visits), "
                         f"followed by {groups.index[1]} ({groups.iloc[1] / groups.sum() * 100:.0f}%).")
        types = d_done["Type"].value_counts()
        if len(types) == 2 and total:
            lines.append(f"<b>{types.get('Drop-in', 0) / total * 100:.0f}%</b> of visits are drop-in and "
                         f"{types.get('Appointment', 0) / total * 100:.0f}% are by appointment.")
        if has_people:
            ppl = d_done.dropna(subset=["Student"]).groupby("Student")["Date"].nunique()
            if len(ppl) >= 20:
                lines.append(f"<b>{(ppl >= 2).mean() * 100:.0f}% of students</b> have come back on a different day at least once this term "
                             f"({len(ppl):,} different students so far).")
    except Exception:
        pass
    if not lines:
        return ""
    return '<div class="takeaways" id="takeaways"><h3>Key takeaways</h3><ul>' + "".join(f"<li>{l}</li>" for l in lines) + "</ul></div>"


PRINT_JS = """<script>
async function appChart(){
  try{var r=await fetch("http://127.0.0.1:__PORT__/chart?token=__TOKEN__");if(!r.ok){return null;}
    var b=await r.blob();return await new Promise(function(res){var f=new FileReader();f.onload=function(){res(f.result);};f.readAsDataURL(b);});
  }catch(e){return null;}
}
async function printReport(){
  var btn=document.getElementById('printbtn'),msg=document.getElementById('printmsg');
  btn.disabled=true;msg.textContent='Preparing the report...';
  var order=['weekly','daily','weekday','heat','return','gaps','FORECAST','consult'],html='',skipped=[];
  try{
    var title=document.querySelector('.hero h1').textContent,sub=document.querySelector('.hero .sub-title').textContent;
    html+='<div class="rhead"><h1>'+title+'</h1><div>'+sub+' &middot; printed '+new Date().toLocaleDateString()+'</div></div>';
    var cards=document.getElementById('statcards');if(cards){html+=cards.outerHTML;}
    var tk=document.getElementById('takeaways');if(tk){html+=tk.outerHTML;}
    for(var k=0;k<order.length;k++){
      if(order[k]==='FORECAST'){
        var u=await appChart();
        if(u){html+='<figure><img src="'+u+'"></figure>';}else{skipped.push('the forecasting app chart (run a forecast from the "Open in Better Forecasting" button first)');}
        continue;
      }
      var el=document.querySelector('[data-pk="'+order[k]+'"]');if(!el){continue;}
      var gd=el.querySelector('.js-plotly-plot');if(!gd){continue;}
      var h=parseInt(el.getAttribute('data-h'))||450;
      var url=await Plotly.toImage(gd,{format:'png',width:1100,height:h,scale:2});
      html+='<figure><img src="'+url+'"></figure>';
    }
    document.getElementById('printreport').innerHTML=html;
    msg.textContent=skipped.length?('Not included: '+skipped.join('; ')+'.'):'';
    await new Promise(function(r){setTimeout(r,250);});
    window.print();
  }catch(e){msg.textContent='Could not prepare the report ('+e+').';}
  btn.disabled=false;
}
window.addEventListener('afterprint',function(){var r=document.getElementById('printreport');if(r){r.innerHTML='';}});
</script>"""


def print_button_html():
    if not _launch["port"]:                  # the report needs the helper only for the app's chart; the rest works without it
        js = PRINT_JS.replace("http://127.0.0.1:__PORT__/chart?token=__TOKEN__", "data:,")
    else:
        js = PRINT_JS.replace("__PORT__", str(_launch["port"])).replace("__TOKEN__", _launch["token"])
    return ('<!--NM--><div class="printbar"><button class="printbtn" id="printbtn" onclick="printReport()">'
            'Print / save as PDF report</button><div id="printmsg" style="font-size:12px;color:#4c4847;margin-top:6px"></div></div>'
            + js + '<!--/NM-->')


LOADER.step(0.93, "Putting the dashboard together", ceiling=0.98)
sections = []     # (id, label, html)
start_launch_server(csv_path)


sections.append(("overview", "Overview", stat_cards() + fig_html(figs["weekly"], pk="weekly") +
                 fig_html(figs["overall"], pk="daily") + plain_english_takeaways() + print_button_html()))
subject_note = para("Every subject uses the same vertical scale, so bar heights and lines can be compared fairly between tabs. "
                    "The number in parentheses is the subject's total visits so far.")
sections.append(("subjects", "Subjects",
                 subtabs("subj", [(lbl, fig_html(f) + subject_note) for lbl, f in subject_figs])))
sections.append(("forecast", "Forecast",
                 launch_button_html() +
                 subtabs("fc", [(lbl, fig_html(f) + para(note)) for lbl, f, note in fc_tabs])))
busy = [("Weekday averages", fig_html(figs["weekday"], pk="weekday") +
         para("Each bar is the total for that weekday divided by how many of that weekday have happened this term, "
              "so weekdays that occurred more often are not inflated." + weekend_note))]
if "heat_weekday" in figs:
    busy.append(("Day and time", fig_html(figs["heat_weekday"], pk="heat") + fig_html(figs["heat_weekday_dropin"]) +
                 fig_html(figs["heat_weekday_appt"]) +
                 para(heat_note + " The two plots under the main one split the same visits into drop-in (blue) and "
                      "appointment-based (red); both use the same colour scale, so a darker square means more visits in either one.")))
    busy.append(("Subject and time", fig_html(figs["heat_subject"]) + para(heat_note)))
sections.append(("busy", "Busy Times", subtabs("busy", busy)))
_ret_html, _ret_notes = ("", [])
if has_people:
    _ret_html, _ret_notes = return_rate_section(df, last_complete)
    notes.extend(_ret_notes)
_ret_tabs = [("Days between visits", fig_html(figs["gaps"], pk="gaps") + para(gaps_note)),
             ("First consultant", fig_html(figs["first_dropin"]) + fig_html(figs["first_appt"]))]
if has_people:
    _ret_tabs.append(("Return rate", _ret_html))
sections.append(("returning", "Returning Students", subtabs("retn", _ret_tabs)))
_cons_tabs = [(lbl, fig_html(f, pk="consult" if lbl == "All" else None)) for lbl, f in consultant_figs]
_lookup = tutor_lookup_html() if has_consultants else ""
if _lookup:
    _cons_tabs.append(("Tutor lookup", _lookup))
sections.append(("consultants", "Consultants", subtabs("cons", _cons_tabs)))

write_dashboard(sections, notes, "Tutoring Center Dashboard", f"Term start {start:%b %d, %Y} to {end:%b %d, %Y}",
                csv_path, "tutoring_dashboard.html")
if not wait_before_exit():
    pause_for_enter()
