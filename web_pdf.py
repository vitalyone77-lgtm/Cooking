"""
PDF рецептов для веб-версии (кнопка «Скачать PDF»): reportlab + шрифт с кириллицей.

На сервере берётся DejaVu Sans (есть в Ubuntu: /usr/share/fonts/truetype/dejavu). Пути Windows
в списке — только для локальной разработки. Другой шрифт можно задать в .env: PDF_FONT и PDF_FONT_BOLD.
Символы, которых нет в шрифте (эмодзи 🍽 ⏱ 🔥 и т.п.), отбрасываются — иначе в PDF были бы «квадратики».
"""
import io
import os
import re
import threading
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

FONT = "KitchenSans"
FONT_BOLD = "KitchenSans-Bold"
GREEN = colors.HexColor("#2F6B4F")

_FONT_CANDIDATES = [
    (os.getenv("PDF_FONT", ""), os.getenv("PDF_FONT_BOLD", "")),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
]

_lock = threading.Lock()
_ready = False


class PdfUnavailable(RuntimeError):
    """На сервере нет шрифта с кириллицей — PDF собрать нельзя."""


def _ensure_fonts() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        for regular, bold in _FONT_CANDIDATES:
            if regular and bold and os.path.isfile(regular) and os.path.isfile(bold):
                pdfmetrics.registerFont(TTFont(FONT, regular))
                pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
                pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
                _ready = True
                return
        raise PdfUnavailable("не найден шрифт с кириллицей (задай PDF_FONT и PDF_FONT_BOLD в .env)")


# Знаки, которых может не быть в шрифте, но терять нельзя: «5‑6 мин» без неразрывного дефиса превратилось бы в «56 мин».
_LOOKALIKES = {
    "‐": "-", "‑": "-", "‒": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}


def _clean(line: str) -> str:
    """Оставляет символы, которые есть в шрифте (эмодзи отбрасываются), подменяет «двойников» и схлопывает пробелы."""
    glyphs = pdfmetrics.getFont(FONT).face.charToGlyph
    out = []
    for ch in line:
        if ch in (" ", "\t") or ord(ch) in glyphs:
            out.append(ch)
        elif ch in _LOOKALIKES:
            out.append(_LOOKALIKES[ch])
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


_HEADING = re.compile(r"^\*{1,2}([^*]+?)\*{1,2}:?\s*$")
_BULLET = re.compile(r"^[-•–]\s+(.*)$")
_NUMBERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """Экранирует XML и превращает **жирный** в <b>; лишние * и ` убирает."""
    html = _BOLD.sub(lambda m: "<b>" + m.group(1) + "</b>", escape(text))
    return html.replace("*", "").replace("`", "")


def _styles() -> dict:
    base = dict(fontName=FONT, fontSize=10.5, leading=15, textColor=colors.HexColor("#222222"))
    return {
        "title": ParagraphStyle("t", fontName=FONT_BOLD, fontSize=18, leading=22, textColor=GREEN, spaceAfter=6),
        "h": ParagraphStyle("h", fontName=FONT_BOLD, fontSize=12.5, leading=16, textColor=GREEN, spaceBefore=9, spaceAfter=3),
        "p": ParagraphStyle("p", spaceAfter=2, **base),
        "bullet": ParagraphStyle("b", leftIndent=14, bulletIndent=3, bulletFontName=FONT, spaceAfter=1.5, **base),
        "num": ParagraphStyle("n", leftIndent=18, bulletIndent=0, bulletFontName=FONT_BOLD, spaceAfter=3, **base),
    }


def _recipe_flowables(title: str, text: str, st: dict) -> list:
    clean_title = _clean(title) or "Рецепт"
    out = [Paragraph(_inline(clean_title), st["title"])]
    first = True
    prev_blank = False
    for raw in text.splitlines():
        line = _clean(raw)
        if not line:
            if not prev_blank:
                out.append(Spacer(1, 2 * mm))
            prev_blank = True
            continue
        prev_blank = False
        if first:
            first = False
            a, b = _norm(line), _norm(clean_title)
            if a and b and (a in b or b in a):
                continue  # первая строка рецепта дублирует заголовок
        if (m := _HEADING.match(line)):
            out.append(Paragraph(_inline(m.group(1)), st["h"]))
        elif (m := _BULLET.match(line)):
            out.append(Paragraph(_inline(m.group(1)), st["bullet"], bulletText="•"))
        elif (m := _NUMBERED.match(line)):
            out.append(Paragraph(_inline(m.group(2)), st["num"], bulletText=m.group(1) + "."))
        else:
            out.append(Paragraph(_inline(line), st["p"]))
    return out


def _footer(site: str):
    left = "Кухонный помощник" + (" · " + site if site else "")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(doc.leftMargin, 10 * mm, _clean(left))
        canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, "стр. %d" % doc.page)
        canvas.restoreState()

    return draw


def build_pdf(items: list[dict], site: str = "", doc_title: str = "") -> bytes:
    """items: [{"title": str, "text": str}, ...]; каждый рецепт начинается с новой страницы."""
    _ensure_fonts()
    st = _styles()
    story: list = []
    for i, it in enumerate(items):
        if i:
            story.append(PageBreak())
        story += _recipe_flowables(it.get("title", ""), it.get("text", ""), st)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=_clean(doc_title or (items[0].get("title", "") if items else "")) or "Рецепт",
        author="Кухонный помощник",
    )
    footer = _footer(site)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
