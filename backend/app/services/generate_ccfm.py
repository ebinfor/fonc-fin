"""
FONCIER+ -- Generateur PDF CCFM officiel
Conforme au modele reglementaire :
  - Bandes tricolores Niger (orange / blanc / vert)
  - QR Code scannable natif ReportLab (QrCodeWidget -- pas de dep externe)
  - 4 sections : Identite, References parcelle, Validation technique, Certification
  - Format A5 portrait (148x210 mm)
  - Formulaire demande A4 (remis au beneficiaire a l etape 1)

NUS : NUS-AAAA-NNNNN
REF : CCFM/AAAA/MM/NNNN
"""
import hashlib
import io
from datetime import datetime, timezone
from typing import Optional

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

# ── Palette Niger --------------------------------------------------
NIGER_ORANGE = colors.HexColor("#E05A00")
NIGER_GREEN  = colors.HexColor("#0D9F3F")
DARK_GREEN   = colors.HexColor("#0D3B2F")
INK          = colors.HexColor("#1A1A1A")
GRAY_LINE    = colors.HexColor("#CCCCCC")
CHECK_GREEN  = colors.HexColor("#1A7A1A")
W_A5, H_A5  = A5


def _s(name, **kw):
    d = dict(fontName="Times-Roman", fontSize=9, textColor=INK, leading=13,
             spaceBefore=2, spaceAfter=2)
    d.update(kw)
    return ParagraphStyle(name, **d)


def _qr_drawing(nus: str, base_url: str = "https://foncier.gov.ne/verify") -> Drawing:
    """QR Code natif ReportLab -- aucune dependance externe."""
    url = f"{base_url}/{nus}"
    qr = QrCodeWidget(url)
    qr.barWidth  = 30 * mm
    qr.barHeight = 30 * mm
    d = Drawing(30 * mm, 30 * mm)
    d.add(qr)
    return d


def _bandes(canvas, doc):
    """Bandes tricolores Niger en haut (6mm) et en bas (4mm)."""
    canvas.saveState()
    bw = W_A5
    # Haut : orange | blanc | vert
    for i, col in enumerate([NIGER_ORANGE, colors.white, NIGER_GREEN]):
        canvas.setFillColor(col)
        canvas.rect(bw * i / 3, H_A5 - 6 * mm, bw / 3, 6 * mm, fill=1, stroke=0)
    # Bas : meme ordre
    for i, col in enumerate([NIGER_ORANGE, colors.white, NIGER_GREEN]):
        canvas.setFillColor(col)
        canvas.rect(bw * i / 3, 0, bw / 3, 4 * mm, fill=1, stroke=0)
    canvas.restoreState()


# ── Generateur certificat A5 officiel --------------------------------

def generer_pdf_certificat(
    nus: str,
    reference_ccfm: str,
    date_signature,
    nom_prenom: str,
    nip_passeport: str,
    date_naissance: str,
    lieu_naissance: str,
    localite: str,
    lot: str,
    parcelle: str,
    superficie_m2: float,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    huissier_nom: str = "A renseigner",
    topographe_nom: str = "A renseigner",
    directeur_nom: str = "Le Directeur de l Urbanisme",
    hash_ccfm: Optional[str] = None,
    base_url: str = "https://foncier.gov.ne/verify",
    output_path: Optional[str] = None,
) -> bytes:
    """
    Genere le certificat PDF CCFM format A5 -- conforme au modele reglementaire.

    Sections :
      I.   Identite du beneficiaire
      II.  References de la parcelle
      III. Validation technique (Huissier + Topographe + NUS)
      IV.  Certification (QR Code + checks + signature directeur)
    """
    buf = io.BytesIO()
    target = buf if output_path is None else output_path

    doc = SimpleDocTemplate(
        target,
        pagesize=A5,
        topMargin=8 * mm, bottomMargin=6 * mm,
        leftMargin=8 * mm, rightMargin=8 * mm,
        title=f"CCFM -- {reference_ccfm}",
        author="Direction Generale de l Urbanisme -- Republique du Niger",
    )

    story = []
    CW = W_A5 - 16 * mm   # largeur utile

    # ── En-tete institutionnel
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph(
        "<b>REPUBLIQUE DU NIGER</b><br/>"
        "<font size=7>Ministere de l Urbanisme, du Logement et de l Habitat<br/>"
        "Direction Generale de l Urbanisme</font>",
        _s("hdr", fontSize=9, fontName="Times-Bold",
           alignment=TA_CENTER, textColor=DARK_GREEN, leading=12)
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=DARK_GREEN, spaceAfter=2))

    # ── Titre
    story.append(Paragraph(
        "<b>CERTIFICAT DE CONFORMITE FONCIERE MIXTE</b>",
        _s("t1", fontSize=10, fontName="Times-Bold",
           alignment=TA_CENTER, textColor=DARK_GREEN, spaceBefore=3)
    ))
    story.append(Paragraph(
        "<b>CCFM</b>",
        _s("t2", fontSize=17, fontName="Times-Bold",
           alignment=TA_CENTER, textColor=DARK_GREEN, spaceBefore=1)
    ))

    # ── Reference + date
    if hasattr(date_signature, 'strftime'):
        ds = date_signature.strftime('%d/%m/%Y')
    else:
        ds = str(date_signature)[:10]

    ref_row = Table([[
        Paragraph(f"<b>Reference : {reference_ccfm}</b>",
                  _s("r1", fontSize=9, fontName="Times-Bold")),
        Paragraph(f"<b>Date :  {ds}</b>",
                  _s("r2", fontSize=9, fontName="Times-Bold", alignment=TA_RIGHT)),
    ]], colWidths=[CW * 0.55, CW * 0.45])
    ref_row.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1,  DARK_GREEN),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]))
    story.append(Spacer(1, 2 * mm))
    story.append(ref_row)
    story.append(Spacer(1, 3 * mm))

    # ── Corps 2 colonnes
    half = CW / 2

    def sh(txt):
        return Paragraph(f"<b>{txt}</b>",
                         _s("sh", fontSize=9, fontName="Times-Bold",
                            textColor=DARK_GREEN, spaceBefore=5))

    def sf(lbl, val):
        return Paragraph(f"{lbl} : <b>{val or '...'}</b>",
                         _s("sf", fontSize=8.5, leading=12))

    def sc(txt):
        return Paragraph(
            f"<font color='#0D9F3F'>v</font>  {txt}",
            _s("sc", fontSize=8.5, textColor=CHECK_GREEN, leading=12)
        )

    # Colonne gauche
    left = [
        sh("I.  IDENTITE DU BENEFICIAIRE"),
        sf("Nom et prenom(s)", nom_prenom),
        sf("N NIP/Passeport", nip_passeport),
        sf("Naissance", f"{date_naissance} a {lieu_naissance}"),
        Spacer(1, 3 * mm),
        sh("II.  REFERENCES DE LA PARCELLE"),
        sf("Localite", localite),
        Paragraph(f"Lot : <b>{lot}</b>     Parcelle : <b>{parcelle}</b>",
                  _s("lp", fontSize=8.5, leading=12)),
        sf("Superficie", f"{superficie_m2:,.0f} m2"),
    ]
    if latitude and longitude:
        left += [
            Paragraph("<b>Coordonnees GPS :</b>",
                      _s("gph", fontSize=8.5, fontName="Times-Bold")),
            Paragraph(f"Lat. {latitude:.4f}   Long. {longitude:.4f}",
                      _s("gpv", fontSize=8.5, fontName="Times-Italic")),
        ]
    left += [
        Spacer(1, 3 * mm),
        sh("III.  VALIDATION TECHNIQUE"),
        sf("Huissier", huissier_nom),
        Paragraph("Signature et Cachet : " + "_" * 18,
                  _s("sig", fontSize=8)),
        Spacer(1, 2 * mm),
        sf("Topographe", topographe_nom),
        Paragraph("Signature et Cachet : " + "_" * 18,
                  _s("sig2", fontSize=8)),
        Spacer(1, 3 * mm),
        Paragraph(f"<b><font color='#0D3B2F'>NUS : {nus}</font></b>",
                  _s("nus", fontSize=9, fontName="Times-Bold")),
    ]

    # Colonne droite
    right = [
        sh("IV.  CERTIFICATION"),
        Paragraph(
            "Le present certificat atteste que la parcelle "
            "ci-dessus designee est conforme aux dispositions "
            "legales et reglementaires en vigueur en matiere "
            "d urbanisme et de cadastre, et qu elle ne fait "
            "l objet d aucun conflit foncier connu a la date de "
            "delivrance.",
            _s("cert", fontSize=8.5, alignment=TA_JUSTIFY, leading=12)
        ),
        Spacer(1, 3 * mm),
        sc("Conformite urbanisme et cadastre"),
        sc("Aucun conflit foncier connu"),
        sc("Limites clairement etablies"),
        sc("Occupation du terrain legitime"),
        Spacer(1, 4 * mm),
        _qr_drawing(nus, base_url),
        Paragraph("<i>Scannez pour verifier l authenticite</i>",
                  _s("qrc", fontSize=7, alignment=TA_CENTER,
                     textColor=colors.HexColor("#555555"))),
        Spacer(1, 3 * mm),
        Paragraph(f"<b>{directeur_nom}</b>",
                  _s("dir", fontSize=9, fontName="Times-Bold", alignment=TA_CENTER)),
        Paragraph("<i>Signature et Cachet officiel</i>",
                  _s("dirs", fontSize=8, alignment=TA_CENTER,
                     textColor=colors.HexColor("#666666"))),
    ]

    body = Table([[left, right]], colWidths=[half, half])
    body.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (0,  -1), 0),
        ("RIGHTPADDING", (0, 0), (0,  -1), 4),
        ("LEFTPADDING",  (1, 0), (1,  -1), 4),
        ("LINEAFTER",    (0, 0), (0,  -1), 0.5, GRAY_LINE),
    ]))
    story.append(body)

    # ── Note legale
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_LINE, spaceAfter=2))
    story.append(Paragraph(
        "<i>Note : le CCFM n est ni un acte de cession, ni un titre foncier, "
        "mais une condition prealable a tout etablissement de ces derniers "
        "et a toute transaction fonciere legale.</i>",
        _s("note", fontSize=7, alignment=TA_CENTER,
           textColor=colors.HexColor("#666666"))
    ))
    if hash_ccfm:
        story.append(Paragraph(
            f"<font size=6 color='#AAAAAA'>SHA-256 : {hash_ccfm}</font>",
            _s("sha", fontSize=6, alignment=TA_CENTER)
        ))

    doc.build(story, onFirstPage=_bandes, onLaterPages=_bandes)

    if output_path is None:
        return buf.getvalue()
    return b""


# ── Formulaire de demande A4 -----------------------------------------

def generer_pdf_formulaire_demande(
    nus: str,
    nom_prenom: str,
    nip_passeport: str,
    date_naissance: str,
    lieu_naissance: str,
    adresse: str,
    telephone: str,
    localite: str,
    lot: str,
    parcelle: str,
    superficie_m2: float,
    mode_paiement: str,
    reference_paiement: str,
    montant_paye: float = 50000.0,
    date_enregistrement=None,
    output_path: Optional[str] = None,
) -> bytes:
    """Genere le formulaire A4 remis au beneficiaire a l etape 1 (Guichetiere)."""
    buf = io.BytesIO()
    target = buf if output_path is None else output_path
    doc = SimpleDocTemplate(
        target, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        title=f"Formulaire demande CCFM -- {nus}",
    )
    story = []

    if date_enregistrement and hasattr(date_enregistrement, 'strftime'):
        ds = date_enregistrement.strftime('%d/%m/%Y')
    else:
        ds = datetime.now().strftime('%d/%m/%Y')

    story.append(Paragraph(
        "<b>REPUBLIQUE DU NIGER</b><br/>"
        "Ministere de l Urbanisme, du Logement et de l Habitat<br/>"
        "Direction Generale de l Urbanisme",
        _s("fhdr", fontSize=10, fontName="Times-Bold",
           alignment=TA_CENTER, textColor=DARK_GREEN, leading=15)
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=DARK_GREEN, spaceAfter=6))
    story.append(Paragraph(
        "FORMULAIRE DE DEMANDE DE CERTIFICAT DE CONFORMITE FONCIERE MIXTE (CCFM)",
        _s("ftit", fontSize=11, fontName="Times-Bold",
           alignment=TA_CENTER, textColor=DARK_GREEN)
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Table([[
        Paragraph(f"<b>NUS : {nus}</b>",
                  _s("fn", fontSize=10, fontName="Times-Bold")),
        Paragraph(f"<b>Date : {ds}</b>",
                  _s("fd", fontSize=10, alignment=TA_RIGHT)),
    ]], colWidths=[10 * cm, 7 * cm]))
    story.append(Spacer(1, 5 * mm))

    def sec(titre):
        story.append(Paragraph(f"<b>{titre}</b>",
            _s("fsec", fontSize=10, fontName="Times-Bold",
               textColor=DARK_GREEN, spaceBefore=8)))

    def champ(lbl, val):
        story.append(Table([[
            Paragraph(lbl, _s("fl", fontSize=9, fontName="Times-Bold")),
            Paragraph(str(val or ""), _s("fv", fontSize=9)),
        ]], colWidths=[5 * cm, 12 * cm]))

    sec("I.  IDENTITE DU DEMANDEUR")
    champ("Nom et prenom(s)",        nom_prenom)
    champ("N NIP/Passeport",         nip_passeport)
    champ("Date et lieu naissance",  f"{date_naissance} -- {lieu_naissance}")
    champ("Adresse",                 adresse)
    champ("Telephone",               telephone)

    sec("II.  REFERENCES DE LA PARCELLE")
    champ("Localite",                localite)
    champ("Lot / Parcelle",          f"{lot}  /  {parcelle}")
    champ("Superficie",              f"{superficie_m2:,.0f} m2")

    sec("III.  PAIEMENT (50 000 FCFA forfaitaire)")
    champ("Mode de paiement",        mode_paiement)
    champ("Reference paiement",      reference_paiement)
    champ("Montant acquitte",        f"{montant_paye:,.0f} FCFA")

    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_LINE))
    story.append(Paragraph(
        "Ce recepisse confirme l enregistrement de votre demande CCFM. "
        "Conservez-le precieusement. Votre dossier sera traite dans un delai de 21 jours. "
        "Presentez ce document a chaque etape du suivi de votre dossier.",
        _s("fnote", fontSize=8.5, alignment=TA_JUSTIFY,
           textColor=colors.HexColor("#444444"))
    ))

    doc.build(story)
    if output_path is None:
        return buf.getvalue()
    return b""


# ── Alias de compatibilité avec ccfm/ccfm_pdf_generator.py ──
# Les deux modules fournissent les mêmes fonctionnalités sous des noms légèrement
# différents. Les endpoints ccfm_v3 utilisent ccfm/ccfm_pdf_generator.py directement.
generer_certificat_ccfm = generer_pdf_certificat
generer_demande_ccfm    = generer_pdf_formulaire_demande
