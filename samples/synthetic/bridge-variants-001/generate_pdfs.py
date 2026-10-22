"""Author four independent single-factor variants, without reading expected facts/model output.

Styling and unchanged content are copied from the original authoring script.
Original PDF and generator are never rewritten.
"""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CASES = ("missing-area", "additional-negative", "duplicate-row", "reordered-columns")
FONT = Path("/Library/Fonts/Arial Unicode.ttf")
pdfmetrics.registerFont(TTFont("CJK", str(FONT)))
INK = colors.HexColor("#18334B")
TEXT = colors.HexColor("#243643")
MUTED = colors.HexColor("#536574")
LINE = colors.HexColor("#C9D4DD")
WIDTH = A4[0] - 104
styles = {
    "title": ParagraphStyle("title", fontName="CJK", fontSize=23, leading=32, textColor=INK, spaceAfter=12),
    "sub": ParagraphStyle("sub", fontName="CJK", fontSize=11, leading=18, textColor=MUTED, spaceAfter=12),
    "h": ParagraphStyle("h", fontName="CJK", fontSize=15, leading=22, textColor=INK, spaceBefore=15, spaceAfter=9),
    "body": ParagraphStyle("body", fontName="CJK", fontSize=11, leading=19, textColor=TEXT, wordWrap="CJK", spaceAfter=8),
    "small": ParagraphStyle("small", fontName="CJK", fontSize=9.5, leading=16, textColor=MUTED, wordWrap="CJK", spaceAfter=7),
    "caption": ParagraphStyle("caption", fontName="CJK", fontSize=10, leading=16, textColor=INK, spaceBefore=6, spaceAfter=6),
    "cell": ParagraphStyle("cell", fontName="CJK", fontSize=10, leading=16, textColor=TEXT, wordWrap="CJK", alignment=TA_LEFT),
}

def p(text, kind="body"):
    return Paragraph(text, styles[kind])

def table(rows, widths, header=True, compact_last_row=False):
    t = Table([[p(str(c), "cell") for c in row] for row in rows], colWidths=widths, hAlign="LEFT")
    commands = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.5, LINE)]
    if header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F5"))]
    else:
        commands += [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F6F8"))]
    if compact_last_row:
        commands += [("TOPPADDING", (0, -1), (-1, -1), 3), ("BOTTOMPADDING", (0, -1), (-1, -1), 3)]
    t.setStyle(TableStyle(commands))
    return t

def frame(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFont("CJK", 9)
    canvas.setFillColor(MUTED)
    canvas.drawString(52, h - 34, "SYNTHETIC / 演示样本")
    canvas.drawRightString(w - 52, h - 34, "B-DEMO-001")
    canvas.setStrokeColor(LINE)
    canvas.line(52, h - 43, w - 52, h - 43)
    canvas.line(52, 48, w - 52, 48)
    canvas.setFont("CJK", 8.5)
    canvas.drawString(52, 32, "虚构桥梁与记录，仅用于学习演示")
    canvas.drawRightString(w - 52, 32, f"第 {doc.page} 页 / 共 2 页")
    canvas.restoreState()

def build_story(case):
    if case not in CASES:
        raise ValueError(case)
    deck_rows = [["病害编号", "观测编号", "位置", "数量", "面积"],
                 ["D-001", "O-001", "第三跨桥面", "1 处", "0.04 m²"]]
    if case == "duplicate-row":
        deck_rows.append(deck_rows[1].copy())
    girder_rows = [["病害编号", "观测编号", "位置", "数量", "面积"],
                   ["D-002", "O-002", "第三跨主梁左侧", "1 处", "未提供（单位 m²）" if case == "missing-area" else "2.6 m²"],
                   ["D-003", "O-003", "第三跨主梁外侧边缘", "1 处", "0.9 m²"]]
    girder_widths = [87, 87, 145, 64, WIDTH-383]
    if case == "reordered-columns":
        order = [1, 4, 2, 0, 3]
        girder_rows = [[row[i] for i in order] for row in girder_rows]
        girder_widths = [girder_widths[i] for i in order]
    return [
        p("桥梁病害与观测记录", "title"),
        p("溪桥演示桥 · 首份简化样本", "sub"),
        table([["演示声明", "本文件为 synthetic（合成）样本。桥梁身份和编号均为虚构，内容不代表任何实际桥梁的检测结论。"]], [76, WIDTH-76], False),
        p("1  基本信息", "h"),
        p("表 1  桥梁与日期", "caption"),
        table([["桥梁名称", "溪桥演示桥"], ["桥梁编号", "B-DEMO-001"], ["报告编制日期", "2026-10-10（演示样本编制日期）"], ["观测日期", "未知"]], [115, WIDTH-115], False),
        Spacer(1, 8),
        p("所有观测日期均未知。报告编制日期不作为观测日期；实际地址、检测机构和观测人员未提供。", "small"),
        p("表 2  构件清单（均属于 B-DEMO-001）", "caption"),
        table([["构件编号", "构件名称", "本样本描述范围"], ["C-DECK-01", "桥面铺装", "第三跨桥面、桥头区域"], ["C-GIRDER-01", "主梁", "第三跨主梁左侧、外侧边缘"]], [126, 90, WIDTH-216]),
        p("2  桥面铺装观测", "h"),
        p("第三跨桥面有 1 处坑槽，面积为 0.04 m²。"),
        p("桥头铺装平顺，未见明显病害。"),
        p("表 3  坑槽记录（构件 C-DECK-01）", "caption"),
        table(deck_rows, [87, 87, 145, 64, WIDTH-383], compact_last_row=case == "duplicate-row"),
        PageBreak(),
        p("主梁观测与记录说明", "title"),
        p("溪桥演示桥 · B-DEMO-001", "sub"),
        p("3  主梁观测", "h"),
        p("本节观测对象为主梁，构件编号 C-GIRDER-01。"),
        p("第三跨主梁左侧有 1 处露筋，面积未提供（单位 m²）。" if case == "missing-area" else "第三跨主梁左侧有 1 处露筋，面积为 2.6 m²。"),
        p("第三跨主梁外侧边缘有 1 处露筋，面积为 0.9 m²。"),
        *([p("第三跨主梁中部未见露筋。")] if case == "additional-negative" else []),
        p("表 4  露筋记录（构件 C-GIRDER-01）", "caption"),
        table(girder_rows, girder_widths),
        Spacer(1, 10),
        p("表中两条观测的日期均未知。两个露筋位置不同，分别保留各自病害编号与观测编号。"),
        p("4  记录说明", "h"),
        p("正文与表格描述相同编号的记录，不表示新增病害或新增观测。桥头的否定陈述仅针对桥头区域，不表示全桥无病害。"),
        p("本样本仅描述病害、位置和面积，未提供评级、原因分析或处治结论。所有面积均以 m² 表示。"),
    ]

def main():
    (HERE / "input").mkdir(parents=True, exist_ok=True)
    for case in CASES:
        output = HERE / "input" / f"{case}.pdf"
        doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=52, leftMargin=52,
                                topMargin=67, bottomMargin=66, title="溪桥演示桥 - synthetic 病害与观测记录",
                                author="Ontology-proj 演示样本", subject="Synthetic fixture; not real inspection findings")
        doc.build(build_story(case), onFirstPage=frame, onLaterPages=frame)
        print(output)

if __name__ == "__main__":
    main()
