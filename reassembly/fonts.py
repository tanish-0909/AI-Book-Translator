from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt

def set_devanagari_font(run, font_name="Nirmala UI", size_pt=12):
    """
    Directly modifies the underlying XML of a python-docx Run object 
    to force Microsoft Word to use the specified font for Complex Scripts (w:cs).
    This prevents Devanagari conjuncts and matras from breaking.
    """
    # Set standard ASCII font using the high-level API
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    
    # Access the rPr (Run Properties) XML element
    rPr = run._r.get_or_add_rPr()
    
    # Create or get the w:rFonts element
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.append(rFonts)
        
    # Explicitly set the Complex Script (cs) slot
    rFonts.set(qn('w:cs'), font_name)
    rFonts.set(qn('w:ascii'), font_name)
    rFonts.set(qn('w:hAnsi'), font_name)
    
    # Set the size for Complex Script
    szCs = rPr.find(qn('w:szCs'))
    if szCs is None:
        szCs = OxmlElement('w:szCs')
        rPr.append(szCs)
    szCs.set(qn('w:val'), str(size_pt * 2))  # szCs uses half-points
