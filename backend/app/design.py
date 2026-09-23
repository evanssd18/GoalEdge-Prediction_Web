"""Site design: the colours, hover effects and layout the operator chooses.

The whole visual identity of the site is one small set of CSS custom properties
(see the ``:root`` block in ``frontend/styles.css``) plus a handful of layout
choices. This module is the single place that says which of those an operator is
allowed to change, what the defaults are, and what a valid value looks like.

Three things are worth stating plainly:

**Why the tokens live server-side.** A per-browser ``localStorage`` theme (which
the site already had for light/dark) changes the site for one reader. An operator
changing the brand green expects every visitor to see it, so the chosen values
are stored in the database and served from ``GET /api/site/design``. The
light/dark switch stays client-side and independent -- this layer does not
replace it, it sits on top.

**Why the token list is fixed rather than free-form.** Accepting arbitrary
``--var`` names would let an operator define a variable nothing reads (so the
change silently does nothing) or shadow an internal one with no way to see the
damage. The list below is exactly the set the stylesheet consumes, and every
value is validated as a CSS colour before it is stored.

**Why colours are validated rather than escaped.** These values are interpolated
into a ``<style>`` element, where HTML escaping is meaningless and the only
thing that matters is that the value cannot end the declaration or the block. A
strict colour grammar rejects ``red; } body { display: none`` at the door, so the
stylesheet can never be broken by a value that reached the database.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# colour grammar
# --------------------------------------------------------------------------

#: #rgb, #rgba, #rrggbb, #rrggbbaa
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
#: rgb()/rgba()/hsl()/hsla() with only digits, separators and a percent sign
_FUNC = re.compile(
    r"^(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/]+\)$",
    re.IGNORECASE,
)
#: a bare named colour, e.g. `rebeccapurple`. Deliberately no `currentColor` or
#: `var()`: a token whose value is another token is not something the editor can
#: preview, and `inherit` in a `:root` block resolves to nothing.
_NAMED = re.compile(r"^[a-zA-Z]{3,30}$")


def is_valid_colour(value: Any) -> bool:
    """True when ``value`` is a single, safe CSS colour.

    Strict on purpose. The check exists to guarantee the value cannot terminate
    the declaration or the ``<style>`` block it is written into, so anything
    carrying ``;``, ``}``, ``/*`` or a quote is rejected -- as is ``var(--x)``,
    which would let one token silently alias another.
    """
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 64:
        return False
    if any(ch in v for ch in ";}<>\"'\\\n\r"):
        return False
    if "var(" in v.lower() or "url(" in v.lower() or "expression" in v.lower():
        return False
    return bool(_HEX.match(v) or _FUNC.match(v) or _NAMED.match(v))


# --------------------------------------------------------------------------
# the token registry
# --------------------------------------------------------------------------

#: Palettes the operator can recolour, in the order the editor groups them.
#: ``key`` is the CSS custom property name without the leading ``--``.
TOKEN_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "brand",
        "label": "Brand & accents",
        "note": "The colours a visitor reads as GoalEdge.",
        "tokens": (
            {"key": "accent", "label": "Primary accent", "default": "#7a84ff"},
            {"key": "accent-dim", "label": "Accent (pressed)", "default": "#626ce0"},
            {"key": "accent-soft", "label": "Accent wash", "default": "rgba(122, 132, 255, 0.25)"},
            {"key": "amber", "label": "Secondary / highlight", "default": "#dbaa3f"},
            {"key": "amber-soft", "label": "Highlight wash", "default": "rgba(219, 170, 63, 0.15)"},
            {"key": "link", "label": "Link", "default": "#7a84ff"},
            {"key": "link-hover", "label": "Link (hover)", "default": "#9aa2ff"},
        ),
    },
    {
        "key": "surface",
        "label": "Surfaces",
        "note": "Page, cards and dividers in dark mode.",
        "tokens": (
            {"key": "bg", "label": "Page background", "default": "#000000"},
            {"key": "bg-soft", "label": "Background (alt)", "default": "#171c1f"},
            {"key": "surface", "label": "Card surface", "default": "#171c1f"},
            {"key": "surface-2", "label": "Inset surface", "default": "#0d1114"},
            {"key": "border", "label": "Border", "default": "#272c32"},
            {"key": "border-soft", "label": "Border (subtle)", "default": "rgba(255, 255, 255, 0.08)"},
        ),
    },
    {
        "key": "text",
        "label": "Text",
        "note": "Content colours in dark mode.",
        "tokens": (
            {"key": "text", "label": "Body text", "default": "#ecedef"},
            {"key": "text-dim", "label": "Secondary text", "default": "rgba(255, 255, 255, 0.75)"},
            {"key": "text-faint", "label": "Faint text", "default": "rgba(255, 255, 255, 0.5)"},
        ),
    },
    {
        "key": "state",
        "label": "Status colours",
        "note": "Used by badges, edges and alerts. Keeping these distinct matters: "
                "a win and a loss must not be the same colour.",
        "tokens": (
            {"key": "green", "label": "Positive / win", "default": "#46c252"},
            {"key": "danger", "label": "Negative / loss", "default": "#e35c47"},
            {"key": "warn", "label": "Warning", "default": "#dbaa3f"},
            {"key": "info", "label": "Information", "default": "#57b6f5"},
        ),
    },
)

#: ``{key: default}`` for every editable token, flattened.
TOKEN_DEFAULTS: dict[str, str] = {
    t["key"]: t["default"] for g in TOKEN_GROUPS for t in g["tokens"]
}

#: Every token this module will accept a value for.
TOKEN_KEYS: frozenset[str] = frozenset(TOKEN_DEFAULTS)


# --------------------------------------------------------------------------
# hover templates
# --------------------------------------------------------------------------

#: A hover style is a named row of CSS applied to interactive elements. Each is
#: written as plain declarations rather than a stylesheet so the editor can show
#: what it will do, and so nothing here can contain a selector.
HOVER_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "key": "lift",
        "label": "Lift",
        "note": "Card rises slightly and its shadow deepens. The default.",
        "css": "transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.28);",
    },
    {
        "key": "glow",
        "label": "Glow",
        "note": "Accent-coloured halo, no movement.",
        "css": "box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent);",
    },
    {
        "key": "tint",
        "label": "Tint",
        "note": "Background shifts toward the accent. Quietest option.",
        "css": "background: color-mix(in srgb, var(--accent) 12%, var(--surface));",
    },
    {
        "key": "border",
        "label": "Border light-up",
        "note": "Border takes the accent colour.",
        "css": "border-color: var(--accent);",
    },
    {
        "key": "scale",
        "label": "Scale up",
        "note": "Grows very slightly. Can blur text on fractional zoom.",
        "css": "transform: scale(1.015);",
    },
    {
        "key": "none",
        "label": "None",
        "note": "No hover effect at all.",
        "css": "",
    },
)

HOVER_KEYS: frozenset[str] = frozenset(h["key"] for h in HOVER_TEMPLATES)

#: How strongly a row reacts on hover. A separate axis from the template so an
#: operator can pick \"subtle glow\" without a second template existing.
HOVER_INTENSITIES: tuple[dict[str, str], ...] = (
    {"key": "off", "label": "Off", "scale": "0"},
    {"key": "subtle", "label": "Subtle", "scale": "0.55"},
    {"key": "normal", "label": "Normal", "scale": "1"},
    {"key": "strong", "label": "Strong", "scale": "1.5"},
)

HOVER_INTENSITY_KEYS: frozenset[str] = frozenset(h["key"] for h in HOVER_INTENSITIES)


# --------------------------------------------------------------------------
# layout templates
# --------------------------------------------------------------------------

#: Layout is a small enumeration rather than free-form CSS, because the
#: stylesheet has to know how to arrange the page: an operator picking an unknown
#: value would get an unstyled grid, not a layout. Each key maps to rules under
#: ``html[data-layout="<key>"]`` in styles.css.
LAYOUT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "key": "comfortable",
        "label": "Comfortable",
        "note": "Roomy cards, wide gutters. The default.",
    },
    {
        "key": "compact",
        "label": "Compact",
        "note": "Denser rows and tighter padding, for scanning many fixtures.",
    },
    {
        "key": "wide",
        "label": "Wide",
        "note": "Board stretches to the viewport. Best on a large monitor.",
    },
    {
        "key": "centered",
        "label": "Centered",
        "note": "Narrow measure, centred. Easiest to read on a laptop.",
    },
)

LAYOUT_KEYS: frozenset[str] = frozenset(l["key"] for l in LAYOUT_TEMPLATES)

#: Card corner treatment.
RADIUS_TEMPLATES: tuple[dict[str, str], ...] = (
    {"key": "sharp", "label": "Sharp", "value": "4px"},
    {"key": "rounded", "label": "Rounded", "value": "10px"},
    {"key": "pill", "label": "Very rounded", "value": "18px"},
)

RADIUS_KEYS: frozenset[str] = frozenset(r["key"] for r in RADIUS_TEMPLATES)


# --------------------------------------------------------------------------
# a whole design
# --------------------------------------------------------------------------

def default_design() -> dict[str, Any]:
    """The design the site ships with -- today's hardcoded look."""
    return {
        "colours": dict(TOKEN_DEFAULTS),
        "hover_template": "tint",
        "hover_intensity": "subtle",
        "layout": "comfortable",
        "radius": "rounded",
    }


def template_payload() -> dict[str, Any]:
    """Everything the editor needs to render its controls, and nothing else."""
    return {
        "token_groups": TOKEN_GROUPS,
        "hover_templates": HOVER_TEMPLATES,
        "hover_intensities": HOVER_INTENSITIES,
        "layout_templates": LAYOUT_TEMPLATES,
        "radius_templates": RADIUS_TEMPLATES,
    }


def _pick(value, allowed, fallback):
    """The value when it is one of ``allowed``, otherwise ``fallback``.

    An unrecognised value is ignored rather than replacing a working setting, so
    a typo cannot quietly undo an operator's choice.
    """
    return value if isinstance(value, str) and value in allowed else fallback


def normalise(raw, base=None):
    """Coerce anything into a valid design, falling back to ``base`` per field.

    Never raises and never rejects a whole payload because one field is wrong.
    The important half is what an INVALID value falls back *to*:

    * ``base`` -- the design already stored -- when one is given; or
    * the shipped default, when there is nothing to preserve.

    That distinction fixes a real bug. Dropping straight to the default meant a
    hostile or mistyped colour silently reverted a token the operator had already
    saved: a bad accent value on an existing purple site did not fail, it quietly
    turned the brand green and reported a successful save. An invalid value must
    be IGNORED, never reverted.
    """
    base = base if base is not None else default_design()
    design = {
        "colours": dict(base.get("colours") or {}),
        "hover_template": base.get("hover_template"),
        "hover_intensity": base.get("hover_intensity"),
        "layout": base.get("layout"),
        "radius": base.get("radius"),
    }
    # Fill anything the base left out, so a partial or legacy row still yields a
    # complete design rather than a missing key rendering as empty CSS.
    for key, value in default_design().items():
        if key == "colours":
            for k, v in value.items():
                design["colours"].setdefault(k, v)
        elif design.get(key) is None:
            design[key] = value

    if not isinstance(raw, dict):
        return design

    colours = raw.get("colours")
    if isinstance(colours, dict):
        for key in TOKEN_KEYS:
            candidate = colours.get(key)
            # An absent key is left alone (partial update) and an invalid one
            # keeps the current value rather than reverting to the default.
            if is_valid_colour(candidate):
                design["colours"][key] = candidate.strip()

    design["hover_template"] = _pick(
        raw.get("hover_template"), HOVER_KEYS, design["hover_template"]
    )
    design["hover_intensity"] = _pick(
        raw.get("hover_intensity"), HOVER_INTENSITY_KEYS, design["hover_intensity"]
    )
    design["layout"] = _pick(raw.get("layout"), LAYOUT_KEYS, design["layout"])
    design["radius"] = _pick(raw.get("radius"), RADIUS_KEYS, design["radius"])
    return design


def diff_against_default(design: dict[str, Any]) -> int:
    """How many settings differ from the shipped design.

    Shown in the panel so an operator can see at a glance whether the site is
    stock, and used to decide whether a \"reset\" button should be offered.
    """
    default = default_design()
    changed = 0
    for key, value in design["colours"].items():
        if default["colours"].get(key) != value:
            changed += 1
    for key in ("hover_template", "hover_intensity", "layout", "radius"):
        if design.get(key) != default.get(key):
            changed += 1
    return changed
