"""Sphinx configuration for the suncast docs site (Furo theme, MyST markdown, Mermaid)."""

project = "suncast"
author = "ckeller42"
extensions = [
    "myst_parser",  # the markdown pages
    "sphinxcontrib.mermaid",  # C4-styled flowcharts and sequence diagrams (client-side mermaid.js)
]

# Render ```mermaid fences through sphinxcontrib.mermaid; anchors for in-page links.
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 3

html_theme = "furo"
html_title = "suncast"
exclude_patterns = ["_build", "superpowers"]
