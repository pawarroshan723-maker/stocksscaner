"""
Design regression tests for the HTML export (export_html).

The shipped report was described as "very blurry with font-sizing and layout
problems": the old theme used a 13px base with 7–10px labels/tables, no font
smoothing, and a heatmap bar container (22px) shorter than the bars it held
(up to 40px) so bars overlapped the sector names. The redesign must not
regress:

  • readable root/base scale (html 16px, body 15px)
  • no text below 11px anywhere in the document (CSS or inline styles)
  • hi-DPI antialiasing + a real UI font stack
  • heatmap bars fit their container
  • fluid grids (no fixed 4-across cards on narrow screens)
"""

import glob
import io
import os
import re
from contextlib import redirect_stdout


def _gen_report(mod, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    e = mod.empty_entry()
    e.update({"price": 100.0, "signal": "BREAKOUT", "composite_score": 70,
              "rsi": 61.0, "rel_vol": 1.2, "atr": 2.0,
              "targets": {"target1": 110.0, "target2": 115.0, "stop": 95.0}})
    data = {"DESIGN": {"symbol": "DESIGN", "DAY": e}}
    with redirect_stdout(io.StringIO()):
        mod.export_html(data, ["DAY"])
    files = sorted(glob.glob("master_report_*.html"), key=os.path.getmtime)
    assert files, "no HTML written"
    html = open(files[-1], encoding="utf-8").read()
    os.remove(files[-1])
    return html


def test_export_html_theme_is_readable(mod, monkeypatch, tmp_path):
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "")
    html = _gen_report(mod, monkeypatch, tmp_path)
    css = html[html.index("<style>"):html.index("</style>")]

    # 1) Root + base scale: the old report sat at html-ish 13px with 9px labels.
    assert "html{font-size:16px" in css
    assert re.search(r"body\{[^}]*font-size:15px", css)

    # 2) Nothing anywhere — stylesheet or inline style attr — below 11px.
    sizes = [float(s) for s in re.findall(r"font-size:(\d+(?:\.\d+)?)px", html)]
    assert sizes, "no font sizes found"
    too_small = sorted({s for s in sizes if s < 11})
    assert not too_small, f"sub-11px text still present: {too_small}"

    # 3) Hi-DPI sharpness + real font stack (was: bare system-ui + Courier New).
    assert "-webkit-font-smoothing:antialiased" in css
    assert "Segoe UI" in css and "Cascadia Mono" in css

    # 4) Heatmap container must fit the tallest bar the markup emits (40px).
    m = re.search(r"\.sec-bars\{[^}]*height:(\d+)px", css)
    assert m, ".sec-bars rule missing"
    assert int(m.group(1)) >= 40, f"bars container {m.group(1)}px clips 40px bars"

    # 5) Fluid grids: cards auto-fit, heatmap cells responsive.
    assert "repeat(auto-fit,minmax(215px,1fr))" in css      # .cards
    assert "repeat(auto-fit,minmax(370px,1fr))" in css      # .g2
    assert "auto-fill,minmax(150px,1fr)" in css             # .sec-grid

    # 6) Tables readable + long tables scroll inside their section.
    assert re.search(r"td\{[^}]*font-size:14px", css)
    assert "overflow-x:auto" in html
