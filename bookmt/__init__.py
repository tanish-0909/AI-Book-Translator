"""English -> Marathi book translator with lossless, page-faithful PDF output.

Pipeline stages, each independently re-runnable against workdir/state.db:

    0  preflight  verify model + vision access, fetch font, assert Devanagari shaping
    1  extract    PDF -> text blocks (bbox + style) and original image bytes
    2  glossary   whole-book term pass -> enforced Marathi renderings
    3  translate  per-page draft with chapter context
    4  review     per-page critique and revision against the source
    5  render     clone the PDF, strip only text, lay in Marathi
    6  qa         integrity + vision checks with an auto-revise loop
"""

__version__ = "1.0.0"
