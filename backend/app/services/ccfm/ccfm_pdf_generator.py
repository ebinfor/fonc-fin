"""
FONCIER+ v3 — Générateurs PDF CCFM v2
Certificat de Conformité Foncière Mixte

Police : DejaVuSans (TTF) — support Unicode complet (m², é, à, ☐, etc.)
QR Code natif : reportlab.graphics.barcode.qr.QrCodeWidget
Format A5 portrait (148 × 210 mm) — conforme au document officiel

Fonctions exportées :
  generer_certificat_ccfm(data, output_path, armoiries_path=None) → str
  generer_demande_ccfm(data, output_path, armoiries_path=None) → str
"""

from __future__ import annotations

import math
import os
from datetime import datetime

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ── Enregistrement polices Unicode ──────────────────────────────────────────
def _reg(name, paths, fallback):
    for p in paths:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont(name, p))
                return name
            except Exception:
                pass
    return fallback

FONT_REG    = _reg("DejaVu",       ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
                                   "Helvetica")
FONT_BOLD   = _reg("DejaVuBold",   ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
                                   "Helvetica-Bold")
FONT_ITALIC = _reg("DejaVuItalic", ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
                                    "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"],
                                   "Helvetica-Oblique")

# ── Couleurs officielles Niger ──────────────────────────────────────────────
ORANGE = colors.HexColor("#FF7A00")
VERT   = colors.HexColor("#009A00")
NOIR   = colors.black
GRIS   = colors.HexColor("#555555")
GRIS_L = colors.HexColor("#CCCCCC")
BLEU   = colors.HexColor("#1a3d6e")
BLEU_L = colors.HexColor("#e8eef7")

W_A5, H_A5 = A5
ML = 12 * mm
MR = 12 * mm
MT = 6  * mm
MB = 6  * mm

# ── Helpers ──────────────────────────────────────────────────────────────────

def _draw_star(c, cx, cy, r_out, r_in, n=5, color=colors.white):
    p = c.beginPath()
    for k in range(n * 2):
        angle = math.pi / 2 + k * math.pi / n
        r = r_out if k % 2 == 0 else r_in
        x_ = cx + r * math.cos(angle)
        y_ = cy + r * math.sin(angle)
        if k == 0: p.moveTo(x_, y_)
        else:      p.lineTo(x_, y_)
    p.close()
    c.setFillColor(color)
    c.drawPath(p, fill=1, stroke=0)

def _draw_qr_code(c, x, y, size, url):
    qr = QrCodeWidget(url)
    qr.barWidth = qr.barHeight = size
    qr.qrVersion = 0
    d = Drawing(size, size)
    d.add(qr)
    renderPDF.draw(d, c, x, y)

def _draw_armoiries(c, cx, cy, arm_r, armoiries_path=None):
    """
    Affiche les armoiries du Niger.
    Cherche automatiquement l'image dans le même dossier que ce script,
    ou utilise armoiries_path si fourni.
    Fallback : emblème vectoriel stylisé.
    """
    from reportlab.lib.utils import ImageReader

    # Chemins candidats (image réelle)
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        armoiries_path,
        os.path.join(_script_dir, "armoiries_niger.png"),
        os.path.join(_script_dir, "armoiries_niger.jpg"),
        "/home/claude/armoiries_niger.png",
        "/home/claude/foncier/foncier_v3_final/backend/ccfm/armoiries_niger.png",
    ]

    for path in candidates:
        if path and os.path.exists(path):
            try:
                img = ImageReader(path)
                # L'image est plus large que haute (719×473) → ajuster la hauteur
                iw, ih = img.getSize()
                ratio = iw / ih  # ~1.52
                draw_w = arm_r * 2 * ratio
                draw_h = arm_r * 2
                c.drawImage(img,
                            cx - draw_w / 2,
                            cy - draw_h / 2,
                            draw_w, draw_h,
                            mask="auto", preserveAspectRatio=True)
                return
            except Exception:
                pass

    # Fallback vectoriel
    c.setFillColor(BLEU);  c.circle(cx, cy, arm_r, fill=1, stroke=0)
    c.setStrokeColor(ORANGE); c.setLineWidth(2); c.circle(cx, cy, arm_r*0.78, fill=0, stroke=1)
    c.setFillColor(VERT);  c.circle(cx, cy, arm_r*0.44, fill=1, stroke=0)
    _draw_star(c, cx, cy, arm_r*0.40, arm_r*0.18, n=5, color=colors.white)

def _dotline(c, x1, y_, x2):
    c.saveState()
    c.setStrokeColor(GRIS_L); c.setLineWidth(0.3); c.setDash(1, 2)
    c.line(x1, y_, x2, y_)
    c.restoreState()

def _sec_title(c, text, x, y_, sz=7.5, color=BLEU):
    c.setFont(FONT_BOLD, sz); c.setFillColor(color); c.drawString(x, y_, text)
    tw = c.stringWidth(text, FONT_BOLD, sz)
    c.setStrokeColor(color); c.setLineWidth(0.6)
    c.line(x, y_-1*mm, x+tw, y_-1*mm)
    return y_ - 5.5*mm

def _field(c, label, value, x, y_, w, sz=6.5):
    c.setFont(FONT_REG, sz); c.setFillColor(NOIR)
    lw = c.stringWidth(label, FONT_REG, sz)
    c.drawString(x, y_, label)
    vw = 0
    if value:
        c.drawString(x+lw, y_, str(value))
        vw = c.stringWidth(str(value), FONT_REG, sz)
    _dotline(c, x+lw+vw+1, y_-0.6*mm, x+w)
    return y_ - 4.8*mm

def _bandes(c, W, y_top, y_bot, bh):
    bw = (W-ML-MR)/3
    for fy in (y_top, y_bot):
        for i, fc in enumerate([ORANGE, colors.white, VERT]):
            c.setFillColor(fc)
            if fc == colors.white:
                c.setStrokeColor(GRIS_L); c.setLineWidth(0.2)
                c.rect(ML+i*bw, fy, bw, bh, fill=1, stroke=1)
            else:
                c.rect(ML+i*bw, fy, bw, bh, fill=1, stroke=0)

def _fmt_date(d):
    if isinstance(d, datetime): return d.strftime("%d/%m/%Y")
    return str(d) if d else ""

# ════════════════════════════════════════════════════════════════════════════
# 1. CERTIFICAT CCFM — A5 PORTRAIT
# ════════════════════════════════════════════════════════════════════════════

def generer_certificat_ccfm(data, output_path, armoiries_path=None):
    W, H = A5
    BH = 3.0*mm

    c = canvas.Canvas(output_path, pagesize=A5)
    c.setTitle("Certificat de Conformité Foncière Mixte")
    c.setAuthor("Direction de l'Urbanisme — République du Niger")
    c.setSubject("CCFM — " + str(data.get("reference_ccfm", "")))

    # Bandes tricolores haut + bas
    band_top = H - MT - BH
    _bandes(c, W, band_top, MB, BH)

    # En-tête (fond bleu clair + armoiries + textes)
    HDR_H = 26*mm
    hdr_y = band_top - HDR_H
    c.setFillColor(BLEU_L)
    c.rect(ML, hdr_y, W-ML-MR, HDR_H, fill=1, stroke=0)

    # Armoiries : image ratio ~1.52 → arm_r = demi-hauteur
    arm_r = 7*mm
    arm_cy = hdr_y + HDR_H - arm_r - 1*mm
    _draw_armoiries(c, W/2, arm_cy, arm_r, armoiries_path)

    c.setFont(FONT_BOLD, 9); c.setFillColor(BLEU)
    c.drawCentredString(W/2, hdr_y+9*mm, "REPUBLIQUE DU NIGER")
    c.setFont(FONT_REG, 5.8); c.setFillColor(GRIS)
    c.drawCentredString(W/2, hdr_y+5.5*mm, "Ministère de l'Urbanisme, du Logement et de l'Habitat")
    c.drawCentredString(W/2, hdr_y+2.5*mm, "Direction Générale de l'Urbanisme")

    c.setStrokeColor(BLEU); c.setLineWidth(0.8)
    c.line(ML, hdr_y, W-MR, hdr_y)

    # Titre
    titre_y = hdr_y - 6*mm
    c.setFont(FONT_BOLD, 10); c.setFillColor(NOIR)
    c.drawCentredString(W/2, titre_y, "CERTIFICAT DE CONFORMITE FONCIERE MIXTE")
    c.setFont(FONT_BOLD, 14); c.setFillColor(BLEU)
    c.drawCentredString(W/2, titre_y - 6*mm, "CCFM")

    # Encadré Référence / Date — centré
    ref_y = titre_y - 16*mm
    ref_w = 70*mm; ref_x = (W-ref_w)/2; ref_h = 9*mm
    c.setFillColor(BLEU_L); c.setStrokeColor(BLEU); c.setLineWidth(0.6)
    c.rect(ref_x, ref_y, ref_w, ref_h, fill=1, stroke=1)
    c.setFont(FONT_BOLD, 6.5); c.setFillColor(BLEU)
    c.drawString(ref_x+3*mm, ref_y+5.8*mm, "Référence : " + str(data.get("reference_ccfm","…………………")))
    c.drawString(ref_x+3*mm, ref_y+1.8*mm, "Date :         " + _fmt_date(data.get("date_signature","")))

    # Corps 2 colonnes
    body_top = ref_y - 3*mm
    body_bot = MB + BH + 9*mm
    c.setStrokeColor(GRIS_L); c.setLineWidth(0.4)
    c.line(W/2, body_top, W/2, body_bot)

    col_l = ML; col_r = W/2+2*mm; col_w = W/2-ML-2*mm

    # ── Colonne gauche ──────────────────────────────────────────────────────
    y = body_top
    y = _sec_title(c, "I.   IDENTITÉ DU BÉNÉFICIAIRE", col_l, y)
    y = _field(c, "Nom & prénom(s) : ",   data.get("nom_prenom",""),    col_l, y, col_w)
    y = _field(c, "N° NIP/Passeport : ",  data.get("nip_passeport",""), col_l, y, col_w)
    nais = _fmt_date(data.get("date_naissance",""))
    lieu = data.get("lieu_naissance","")
    y = _field(c, "Naissance : ", f"{nais} à {lieu}" if (nais or lieu) else "", col_l, y, col_w)
    y -= 3*mm

    y = _sec_title(c, "II.  RÉFÉRENCES DE LA PARCELLE", col_l, y)
    y = _field(c, "Localité : ", data.get("localite",""), col_l, y, col_w)

    # Lot + Parcelle
    c.setFont(FONT_REG, 6.5); c.setFillColor(NOIR)
    ls = "Lot : " + str(data.get("lot",""))
    ps = "  Parcelle : " + str(data.get("parcelle",""))
    c.drawString(col_l, y, ls)
    _dotline(c, col_l+c.stringWidth(ls,FONT_REG,6.5)+1, y-0.6*mm, col_l+33*mm)
    c.drawString(col_l+34*mm, y, ps)
    _dotline(c, col_l+34*mm+c.stringWidth(ps,FONT_REG,6.5)+1, y-0.6*mm, col_l+col_w)
    y -= 4.8*mm

    # Superficie avec m²
    c.setFont(FONT_REG, 6.5); c.setFillColor(NOIR)
    c.drawString(col_l, y, "Superficie : " + str(data.get("superficie_m2","…")) + " m\u00b2")
    y -= 4.8*mm

    # GPS
    c.setFont(FONT_BOLD, 6.5); c.setFillColor(NOIR)
    c.drawString(col_l, y, "Coordonnées GPS :")
    y -= 4.5*mm
    c.setFont(FONT_REG, 6.5)
    c.drawString(col_l+3*mm, y,
        "Lat. " + str(data.get("latitude","…………")) +
        "   Long. " + str(data.get("longitude","…………")))
    y -= 5.5*mm

    # §III Validation
    y = _sec_title(c, "III. VALIDATION TECHNIQUE", col_l, y)
    c.setFont(FONT_REG, 6.5); c.setFillColor(NOIR)
    nom_h = str(data.get("nom_huissier","") or "")
    c.drawString(col_l, y, "Huissier : " + (nom_h if nom_h else "…………………………………"))
    y -= 4.5*mm
    c.drawString(col_l, y, "Signature & Cachet :")
    _dotline(c, col_l+c.stringWidth("Signature & Cachet :",FONT_REG,6.5)+1, y-0.5*mm, col_l+col_w)
    y -= 7*mm
    nom_t = str(data.get("nom_topographe","") or "")
    c.drawString(col_l, y, "Topographe : " + (nom_t if nom_t else "…………………………………"))
    y -= 4.5*mm
    c.drawString(col_l, y, "Signature & Cachet :")
    _dotline(c, col_l+c.stringWidth("Signature & Cachet :",FONT_REG,6.5)+1, y-0.5*mm, col_l+col_w)
    y -= 7*mm
    c.setFont(FONT_BOLD, 6.2); c.setFillColor(BLEU)
    c.drawString(col_l, y, "NUS : " + str(data.get("nus","…………………………")))

    # ── Colonne droite ──────────────────────────────────────────────────────
    ry = body_top
    ry = _sec_title(c, "IV. CERTIFICATION", col_r, ry)

    cert = (
        "Le présent certificat atteste que la parcelle ci-dessus désignée "
        "est conforme aux dispositions légales et réglementaires en vigueur "
        "en matière d'urbanisme et de cadastre, et qu'elle ne fait l'objet "
        "d'aucun conflit foncier connu à la date de délivrance."
    )
    c.setFont(FONT_REG, 6.5)
    words = cert.split()
    lines_txt, cur = [], []
    for w_ in words:
        if c.stringWidth(" ".join(cur+[w_]), FONT_REG, 6.5) <= col_w-1*mm:
            cur.append(w_)
        else:
            lines_txt.append(cur); cur = [w_]
    if cur: lines_txt.append(cur)

    for i, ln in enumerate(lines_txt):
        is_last = (i == len(lines_txt)-1)
        if is_last or len(ln) <= 2:
            c.drawString(col_r, ry, " ".join(ln))
        else:
            sp = len(ln)-1
            if sp > 0:
                bw_ = sum(c.stringWidth(w_,FONT_REG,6.5) for w_ in ln)
                spw = c.stringWidth(" ",FONT_REG,6.5)
                ex  = (col_w-1*mm-bw_-sp*spw)/sp
                cx_ = col_r
                for j, word in enumerate(ln):
                    c.drawString(cx_, ry, word)
                    if j < sp: cx_ += c.stringWidth(word,FONT_REG,6.5)+spw+ex
            else:
                c.drawString(col_r, ry, " ".join(ln))
        ry -= 3.8*mm
    ry -= 3*mm

    # 4 attestations vertes
    attestations = [
        "\u2713  Conformité urbanisme et cadastre",
        "\u2713  Aucun conflit foncier connu",
        "\u2713  Limites clairement établies",
        "\u2713  Occupation du terrain légitime",
    ]
    c.setFont(FONT_REG, 6); c.setFillColor(VERT)
    for att in attestations:
        c.drawString(col_r, ry, att); ry -= 4*mm
    ry -= 2*mm

    # QR Code
    qr_size = 22*mm
    qr_x = col_r+(col_w-qr_size)/2
    qr_y = ry - qr_size
    qr_url = data.get("qr_url") or (
        "https://foncier.ne/verify/" +
        str(data.get("nus", data.get("reference_ccfm","CCFM"))).replace("/","-")
    )
    _draw_qr_code(c, qr_x, qr_y, qr_size, qr_url)
    c.setFont(FONT_ITALIC, 4.5); c.setFillColor(GRIS)
    c.drawCentredString(col_r+col_w/2, qr_y-2.5*mm, "Scannez pour vérifier l'authenticité")
    ry = qr_y - 6*mm

    # Signature Directeur
    c.setFont(FONT_BOLD, 6.8); c.setFillColor(NOIR)
    c.drawCentredString(col_r+col_w/2, ry, "Le Directeur de l'Urbanisme")
    dw = c.stringWidth("Le Directeur de l'Urbanisme", FONT_BOLD, 6.8)
    c.setStrokeColor(NOIR); c.setLineWidth(0.4)
    c.line(col_r+col_w/2-dw/2, ry-0.8*mm, col_r+col_w/2+dw/2, ry-0.8*mm)
    c.setFont(FONT_ITALIC, 6); c.setFillColor(GRIS)
    c.drawCentredString(col_r+col_w/2, ry-5*mm, "Signature & Cachet officiel")

    # Note légale
    note_y = MB+BH+7*mm
    c.setStrokeColor(GRIS_L); c.setLineWidth(0.35)
    c.line(ML, note_y+4*mm, W-MR, note_y+4*mm)
    note = (
        "Note : le CCFM n'est ni un acte de cession, ni un titre foncier, "
        "mais une condition préalable à tout établissement de ces derniers "
        "et à toute transaction foncière légale."
    )
    c.setFont(FONT_ITALIC, 5.0); c.setFillColor(GRIS)
    cur_n, ln_n = [], []
    for w_ in note.split():
        if c.stringWidth(" ".join(cur_n+[w_]), FONT_ITALIC, 5.0) <= W-ML-MR:
            cur_n.append(w_)
        else:
            ln_n.append(" ".join(cur_n)); cur_n=[w_]
    if cur_n: ln_n.append(" ".join(cur_n))
    for i, ln in enumerate(ln_n):
        c.drawCentredString(W/2, note_y+1.5*mm-i*3.2*mm, ln)

    c.save()
    return output_path


# ════════════════════════════════════════════════════════════════════════════
# 2. FORMULAIRE DE DEMANDE — A4 PORTRAIT
# ════════════════════════════════════════════════════════════════════════════

def generer_demande_ccfm(data, output_path, armoiries_path=None):
    W, H = A4
    x_left = 22*mm; col_w = W - 2*x_left

    c = canvas.Canvas(output_path, pagesize=A4)
    c.setTitle("Formulaire de Demande CCFM")
    c.setAuthor("FONCIER+ v3 — République du Niger")

    y = H - 12*mm

    # En-tête
    arm_r_a4 = 13*mm
    _draw_armoiries(c, W/2, y-arm_r_a4, arm_r_a4, armoiries_path)
    y -= 30*mm
    c.setFont(FONT_BOLD, 13); c.setFillColor(BLEU)
    c.drawCentredString(W/2, y, "RÉPUBLIQUE DU NIGER")
    y -= 5.5*mm
    c.setFont(FONT_REG, 8.5); c.setFillColor(GRIS)
    c.drawCentredString(W/2, y, "Ministère de l'Urbanisme du Logement et de l'Habitat")
    y -= 4.5*mm
    c.drawCentredString(W/2, y, "Secrétariat Général — Direction de l'Urbanisme")
    y -= 6*mm

    bw_a4 = col_w/3
    for i, fc in enumerate([ORANGE, colors.white, VERT]):
        c.setFillColor(fc)
        if fc == colors.white:
            c.setStrokeColor(GRIS_L); c.setLineWidth(0.2)
            c.rect(x_left+i*bw_a4, y, bw_a4, 2.5*mm, fill=1, stroke=1)
        else:
            c.rect(x_left+i*bw_a4, y, bw_a4, 2.5*mm, fill=1, stroke=0)
    y -= 9*mm

    c.setFont(FONT_BOLD, 13); c.setFillColor(NOIR)
    c.drawCentredString(W/2, y, "FORMULAIRE DE DEMANDE DU CERTIFICAT")
    y -= 6.5*mm
    c.drawCentredString(W/2, y, "DE CONFORMITÉ FONCIÈRE MIXTE (CCFM)")
    y -= 11*mm

    c.setFont(FONT_BOLD, 9); c.setFillColor(BLEU)
    c.drawString(x_left, y, "Informations Générales")
    y -= 5.5*mm
    c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
    c.drawString(x_left, y, "Numéro unique de suivi (NUS) : " + str(data.get("nus","……………………………")))
    y -= 5*mm
    c.drawString(x_left, y, "QR Code de traçabilité : [généré automatiquement lors de l'enregistrement]")
    y -= 10*mm

    def sec(title, y_):
        c.setFont(FONT_BOLD, 9.5); c.setFillColor(BLEU)
        c.drawString(x_left, y_, title)
        tw = c.stringWidth(title, FONT_BOLD, 9.5)
        c.setStrokeColor(BLEU); c.setLineWidth(0.7)
        c.line(x_left, y_-1.5*mm, x_left+tw, y_-1.5*mm)
        return y_ - 8*mm

    def fl(label, val, y_):
        c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
        vs = str(val) if val else "……………………………………………………………………"
        c.drawString(x_left, y_, f"{label} : {vs}")
        return y_ - 5.5*mm

    # §1
    y = sec("1. IDENTITÉ DU DEMANDEUR", y)
    y = fl("Nom et prénom(s)",          data.get("nom_prenom",""),     y)
    nais = _fmt_date(data.get("date_naissance",""))
    lieu = data.get("lieu_naissance","")
    y = fl("Date et lieu de naissance", f"{nais} à {lieu}",           y)
    y = fl("Nationalité",               data.get("nationalite","NE"), y)
    y = fl("NIP / Passeport / CNI",     data.get("nip_passeport",""), y)
    y = fl("Adresse",                   data.get("adresse",""),       y)
    y = fl("Téléphone / Email",
           f"{data.get('telephone','')}  /  {data.get('email','')}", y)
    y -= 5*mm

    # §2
    y = sec("2. INFORMATIONS SUR LA PARCELLE", y)
    c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
    c.drawString(x_left, y,
        f"Localité : {data.get('localite','')}     "
        f"Lot : {data.get('lot','')}     "
        f"Parcelle : {data.get('parcelle','')}     "
        f"Superficie : {data.get('superficie_m2','')} m\u00b2")
    y -= 5.5*mm
    c.drawString(x_left, y,
        f"Coordonnées GPS : Latitude {data.get('latitude','')}  |  Longitude {data.get('longitude','')}")
    y -= 10*mm

    # §3
    y = sec("3. PIÈCES À FOURNIR (joindre en annexe)", y)
    pieces = [
        ("Copie de la pièce d'identité du demandeur",    "Copie de l'acte de cession et/ou du titre foncier"),
        ("Plan de lotissement / extrait cadastral",       "Constat de l'huissier (si disponible)"),
        ("Rapport du topographe (si disponible)",         "Justificatif de paiement des frais"),
    ]
    c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
    for left, right in pieces:
        c.drawString(x_left,           y, "\u2610 " + left)
        c.drawString(x_left+89*mm,     y, "\u2610 " + right)
        y -= 5.5*mm
    y -= 5*mm

    # §4
    y = sec("4. FRAIS ET MODALITÉS DE PAIEMENT", y)
    c.setFont(FONT_BOLD, 8.5); c.setFillColor(NOIR)
    c.drawString(x_left, y, "Montant forfaitaire : 50 000 F CFA")
    c.setFont(FONT_REG, 8.5)
    c.drawString(x_left+58*mm, y, "(conformément au barème officiel)")
    y -= 5.5*mm
    c.drawString(x_left, y, "Modes acceptés : Mobile Money (Airtel Money, Moov Money), Banque, Trésor Public.")
    y -= 5.5*mm
    c.drawString(x_left, y,
        f"Référence de paiement : {data.get('reference_paiement','…………………')}     "
        f"Mode : {data.get('mode_paiement','…………………')}")
    y -= 10*mm

    # §5
    y = sec("5. ENGAGEMENT DU DEMANDEUR", y)
    c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
    c.drawString(x_left, y, f"Je soussigné(e) {data.get('nom_prenom','…………………………………………………')},")
    y -= 5*mm
    c.drawString(x_left, y, "demande l'établissement d'un CCFM pour la parcelle désignée ci-dessus et certifie")
    y -= 5*mm
    c.drawString(x_left, y, "sur l'honneur l'exactitude des informations fournies.")
    y -= 10*mm
    c.drawString(x_left, y, f"Fait à {data.get('localite','…………')}, le ………………")
    c.drawString(x_left+105*mm, y, "Signature du demandeur")
    y -= 9*mm
    c.drawString(x_left+105*mm, y, "Visa du Directeur de l'Urbanisme")
    y -= 12*mm

    # §6
    c.setStrokeColor(GRIS_L); c.setDash(3,3); c.setLineWidth(0.5)
    c.line(x_left, y+4*mm, W-x_left, y+4*mm)
    c.setDash(); c.setStrokeColor(NOIR)
    y = sec("6. RÉSERVÉ À L'ADMINISTRATION", y)
    c.setFont(FONT_REG, 8.5); c.setFillColor(NOIR)
    for label in ["Numéro de dossier", "Agent réceptionnaire", "Date"]:
        c.drawString(x_left, y, f"{label} : ……………………………"); y -= 5.5*mm
    c.drawString(x_left, y,
        f"Enregistrement NUS :  \u2610 Oui  –  Réf. : {data.get('nus','……………………………')}")

    c.save()
    return output_path


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ex = {
        "reference_ccfm": "CCFM/2026/02/0089", "date_signature": datetime(2026,2,18),
        "nom_prenom": "MOUSSA Amadou Ibrahim", "nip_passeport": "NE-1990-00123",
        "date_naissance": "12/01/1990", "lieu_naissance": "Niamey",
        "nationalite": "NE", "adresse": "Quartier Plateau, Niamey",
        "telephone": "+227 90 12 34 56", "email": "moussa@email.ne",
        "localite": "Niamey II", "lot": "LOT-1234", "parcelle": "PARC-5678",
        "superficie_m2": "498", "latitude": "13.5137", "longitude": "2.1098",
        "nom_huissier": "Me Rachid Moussa", "nom_topographe": "Issoufou Mahamane",
        "nus": "NUS-2026-00089", "qr_url": "https://foncier.ne/verify/NUS-2026-00089",
        "reference_paiement": "AM-2026-9876543", "mode_paiement": "AIRTEL_MONEY",
    }
    generer_certificat_ccfm(ex, "/home/claude/test_certificat_A5.pdf")
    print("✓ Certificat A5")
    generer_demande_ccfm(ex, "/home/claude/test_demande_A4.pdf")
    print("✓ Demande A4")
