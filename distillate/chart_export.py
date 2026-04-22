"""Karpathy-style experiment chart export — PNG generation via matplotlib.

Extracted from experiments.py. Generates publication-ready metric charts
with frontier lines, decision markers, and branding.
"""

import logging
import re

from distillate.experiments import classify_metric, _is_lower_better

log = logging.getLogger(__name__)


def generate_export_chart(runs: list[dict], metric: str, title: str = "",
                          log_scale: bool = False, subtitle: str = "") -> bytes:
    """Generate a minimal, centered chart PNG for sharing. Returns PNG bytes."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, findfont
    from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator

    points = []
    for i, run in enumerate(runs):
        val = run.get("results", {}).get(metric)
        if isinstance(val, (int, float)):
            points.append({"value": val, "run": run, "index": i})
    if not points:
        raise ValueError(f"No data for metric '{metric}'")

    # Ordinal x-axis (same as canvas chart — evenly spaced)
    xs = list(range(len(points)))
    ys = [p["value"] for p in points]
    lower_better = _is_lower_better(metric)

    # ── Figure ──
    fig, ax = plt.subplots(figsize=(10, 5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Font
    for fam in ("Inter", "Helvetica Neue", "Helvetica", "Arial"):
        try:
            if findfont(FontProperties(family=fam), fallback_to_default=False):
                plt.rcParams["font.family"] = fam
                break
        except Exception:
            continue

    # Open plot: left + bottom spines only
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.5)
    ax.spines["bottom"].set_linewidth(0.5)
    ax.spines["left"].set_color("#ccc")
    ax.spines["bottom"].set_color("#ccc")

    # Grid: light dashed horizontal
    ax.yaxis.grid(True, alpha=0.25, linewidth=0.4, color="#bbb", linestyle="--")
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)

    if log_scale:
        ax.set_yscale("log")

    # ── Frontier (step function, running best over ALL runs) ──
    # Running min/max across every data point, not just keeps.
    # Green dots mark any run that actually improved the frontier.
    front_xs: list = []
    front_ys: list = []
    front_set: set = set()
    best = None

    if points:
        best = points[0]["value"]
        front_xs.append(xs[0])
        front_ys.append(best)
        front_set.add(0)

        for i in range(1, len(points)):
            v = points[i]["value"]
            improved = (lower_better and v < best) or (not lower_better and v > best)
            if improved:
                best = v
                front_set.add(i)
            front_xs.append(xs[i])
            front_ys.append(best)

    if front_xs:
        # Extend right to last run
        if front_xs[-1] < xs[-1]:
            front_xs.append(xs[-1])
            front_ys.append(front_ys[-1])

    if len(front_xs) > 1:
        ax.plot(front_xs, front_ys, color="#4ade80", linewidth=2, alpha=0.6,
                zorder=2, solid_capstyle="round")

    # ── Dots: green = frontier-improving keeps, gray = everything else ──
    for i, p in enumerate(points):
        if i in front_set:
            ax.scatter(xs[i], p["value"], c="#4ade80", s=30,
                       zorder=4, edgecolors="white", linewidths=0.5)
        else:
            ax.scatter(xs[i], p["value"], c="#ccc", s=12, zorder=3,
                       edgecolors="none", alpha=0.45)

    # ── Tilted labels on frontier-improving keeps ──
    for i, p in enumerate(points):
        if i not in front_set:
            continue
        desc = p["run"].get("description", "") or p["run"].get("hypothesis", "")
        if not desc:
            continue
        if len(desc) > 24:
            desc = desc[:22] + "\u2026"
        ax.annotate(
            desc, (xs[i], p["value"]),
            textcoords="offset points", xytext=(5, -7),
            fontsize=5.5, color="#aaa", ha="left", va="top",
            rotation=-30, rotation_mode="anchor",
            zorder=5, annotation_clip=True,
        )

    # ── Axes ──
    metric_label = metric.replace("_", " ").title()
    scale_note = " (log)" if log_scale else ""
    ax.set_ylabel(f"{metric_label}{scale_note}", fontsize=9.5, color="#666", labelpad=8)
    ax.set_xlabel("Run", fontsize=9.5, color="#666", labelpad=8)
    ax.tick_params(colors="#888", labelsize=8, length=3, width=0.4)

    # X-axis: show run numbers at evenly spaced intervals (like canvas)
    n_pts = len(points)
    x_step = max(1, n_pts // 6)
    x_tick_positions = list(range(0, n_pts, x_step))
    x_tick_labels = []
    for idx in x_tick_positions:
        p = points[idx]
        rn = p["run"].get("run_number", 0)
        if rn > 0:
            x_tick_labels.append(f"#{rn}")
        else:
            m = re.match(r"(?:run_?)(\d+)", p["run"].get("name", ""))
            x_tick_labels.append(f"#{m.group(1)}" if m else f"#{idx}")
    ax.set_xticks(x_tick_positions)
    ax.set_xticklabels(x_tick_labels)

    # Y-axis ticks
    if log_scale:
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5), numticks=8))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10]))

    cat = classify_metric(metric)
    def _tick(v, _):
        if cat == "ratio":
            return f"{v * 100:g}%" if 0 <= v <= 1 else f"{v:g}"
        if cat == "loss":
            return f"{v:.2e}" if abs(v) < 0.001 else f"{v:g}"
        if cat == "count":
            if v == 0:
                return "0"
            iv = int(round(v))
            if abs(iv) >= 1e6:
                return f"{iv / 1e6:g}M"
            if abs(iv) >= 1e3:
                return f"{iv / 1e3:g}K"
            return f"{iv:,}"
        return f"{v:g}"
    ax.yaxis.set_major_formatter(FuncFormatter(_tick))

    if not log_scale and min(ys) >= 0:
        ax.set_ylim(bottom=0, top=max(ys) * 1.05)

    # ── Title (just the experiment name, top-center) ──
    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color="#222", pad=14)

    fig.tight_layout()

    # ── Branding: SVG logo + "Distillate" in brand indigo ──
    try:
        import cairosvg
        import numpy as np
        from pathlib import Path as _P
        from PIL import Image as _Img

        svg_path = _P(__file__).parent.parent / "docs" / "logo.svg"
        if not svg_path.exists():
            raise FileNotFoundError
        png_bytes = cairosvg.svg2png(url=str(svg_path), output_width=48, output_height=48)
        logo_arr = np.array(_Img.open(io.BytesIO(png_bytes)).convert("RGBA")) / 255.0
        logo_ax = fig.add_axes([0.895, 0.01, 0.02, 0.035], anchor="SE", zorder=10)
        logo_ax.imshow(logo_arr)
        logo_ax.axis("off")
        fig.text(0.92, 0.026, "Distillate", ha="left", va="center",
                 fontsize=7, color="#6366f1", alpha=0.5, fontweight="600")
    except Exception:
        fig.text(0.99, 0.01, "Distillate", ha="right", va="bottom",
                 fontsize=7, color="#6366f1", alpha=0.35, fontweight="600")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


