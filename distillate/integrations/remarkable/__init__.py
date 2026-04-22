"""reMarkable integration for Distillate.

Opt-in backend that uploads papers to a reMarkable tablet via rmapi, then
extracts highlights and typed notes from downloaded .rmscene bundles via
the `rmscene` library.

Enabled when ``READING_SOURCE=remarkable`` (see distillate.config). Requires
the optional ``rmscene`` dependency: ``pip install "distillate[remarkable]"``.

Public surface (submodules, imported lazily):
    - ``client``    — rmapi CLI wrapper (upload, list, stat, download)
    - ``auth``      — device registration flow
    - ``renderer``  — .rmscene parsing + highlight/OCR extraction (added in split)
"""
