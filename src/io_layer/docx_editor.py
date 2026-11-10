"""DocxEditor: reads a Word resume, edits it in place for a tailored version, and writes resumes as Word files."""

import io
import re
from copy import deepcopy

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

MIN_WORDS_TO_EDIT = 12  # a plain Word paragraph this long is body text (e.g. a profile), not a name or contact line
TYPED_BULLET = re.compile(r"^\s*[•\-–*·]\s+")  # a bullet typed as a character, e.g. "• Built dashboards"
DURATION = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*(?:months?|mths?|mos?|years?|yrs?)\s*\)", re.IGNORECASE)  # " (6 months)"
YEAR_AT_END = re.compile(r"^(.*?\S)\s*(?:,|\(|–|-)\s*((?:19|20)\d{2})\)?\s*$")  # "Google Certificate, 2023"
DATED_LIST_HEADING = re.compile(r"certif|licen|award|honou?r|achievement|course", re.IGNORECASE)
AVERAGE_CHAR_WIDTH = 0.45  # average character width as a share of the font size (Calibri, Arial and similar)
BULLET_INDENT_PT = 36  # usual indent of a bulleted list when the file doesn't say (0.5 inch)


class DocxEditor:
    """Reads and writes the Word files behind tailored resumes."""

    def write_resume_docx(self, resume: dict) -> bytes:
        """Return a tailored resume as a Word file (bytes), in a plain one-column layout that hiring systems read well."""
        document = Document()
        for section in document.sections:
            section.top_margin = section.bottom_margin = Cm(1.8)
            section.left_margin = section.right_margin = Cm(2)
        style = document.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10.5)
        style.paragraph_format.space_after = Pt(2)

        def heading(text: str) -> None:
            """Adds a bold, coloured section heading to the Word resume."""
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(10)
            paragraph.paragraph_format.space_after = Pt(4)
            run = paragraph.add_run(text)
            run.bold = True
            run.font.size = Pt(11)
            run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x8A)

        if resume["name"]:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(resume["name"])
            run.bold = True
            run.font.size = Pt(18)
        if resume["contact"]:
            paragraph = document.add_paragraph(resume["contact"])
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        for section in resume["sections"]:
            heading(section["heading"])
            if section["text"]:
                document.add_paragraph(section["text"])
            for group in section["skill_groups"]:
                # "Technical: Python, SQL", with the label in bold
                paragraph = document.add_paragraph()
                if group["label"]:
                    paragraph.add_run(f"{group['label']}: ").bold = True
                paragraph.add_run(", ".join(group["skills"]))
            for entry in section["entries"]:
                # "Title, Organisation" on the left, dates on the right (a right-aligned tab stop)
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(4)
                paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(17), WD_TAB_ALIGNMENT.RIGHT)
                paragraph.add_run(entry["title"]).bold = True
                if entry["organisation"] and entry["organisation"] != entry["title"]:
                    paragraph.add_run(f", {entry['organisation']}")
                if entry["dates"]:
                    paragraph.add_run(f"\t{entry['dates']}").italic = True
                if entry["subtitle"]:
                    document.add_paragraph().add_run(entry["subtitle"]).italic = True
                for bullet in entry["bullets"]:
                    document.add_paragraph(bullet, style="List Bullet")

        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    def tidy_resume_dates(self, resume: dict) -> dict:
        """Drop a length of time after the dates in a tailored resume (ai_manager.tailor_resume),
        e.g. "Sep 2024 - Feb 2025 (6 months)" -> "Sep 2024 - Feb 2025". Returns the same resume."""
        for section in resume["sections"]:
            for entry in section["entries"]:
                entry["dates"] = DURATION.sub("", entry["dates"])
        return resume

    def read_docx_paragraphs(self, path: str) -> list[dict]:
        """Return a Word resume's paragraphs (id, text, kind, editable, chars_per_line, ...) for tailoring it in place."""
        document = Document(path)
        section = document.sections[0]
        page_text_width = (section.page_width - section.left_margin - section.right_margin) / 12700  # EMU -> points
        paragraphs = []
        for position, paragraph in enumerate(document.paragraphs):
            text = paragraph.text
            if not text.strip():
                continue
            runs = [run for run in paragraph.runs if run.text]
            formats = {(bool(run.bold), bool(run.italic), bool(run.underline)) for run in runs}
            all_bold = bool(runs) and all(run.bold for run in runs)
            is_bullet = "list" in paragraph.style.name.lower() or TYPED_BULLET.match(text) is not None
            # A link or field is not one of paragraph.runs, so its text would be lost when the runs are replaced
            only_runs = "".join(run.text for run in runs) == text

            if not paragraphs:
                kind = "name"
            elif "\t" in text:
                kind = "entry"
            elif is_bullet:
                kind = "bullet"
            elif all_bold or paragraph.style.name.lower().startswith("heading"):
                kind = "heading"
            else:
                kind = "text"
            editable = (only_runs and len(formats) == 1 and kind in ("bullet", "text")
                        and (kind == "bullet" or len(text.split()) >= MIN_WORDS_TO_EDIT))

            # Line width: the page's text width minus the paragraph's indents (a list's indent is usually
            # not in the paragraph itself, so a bullet without one gets BULLET_INDENT_PT)
            indent = paragraph.paragraph_format.left_indent or paragraph.style.paragraph_format.left_indent
            indent_pt = indent.pt if indent is not None else (BULLET_INDENT_PT if is_bullet else 0)
            right_indent = paragraph.paragraph_format.right_indent
            width_pt = page_text_width - indent_pt - (right_indent.pt if right_indent is not None else 0)
            chars_per_line = max(20, int(width_pt / (self.font_size_pt(paragraph) * AVERAGE_CHAR_WIDTH)))

            paragraphs.append({
                "id": position,
                "text": TYPED_BULLET.sub("", text).strip(),
                "kind": kind,
                "centered": paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER,
                "editable": editable,
                "chars_per_line": chars_per_line,
            })
        return paragraphs

    def font_size_pt(self, paragraph: Paragraph, default: float = 11) -> float:
        """A Word paragraph's font size in points: its first run's, else its style's (or a style it is based on)."""
        for run in paragraph.runs:
            if run.font.size is not None:
                return run.font.size.pt
        style = paragraph.style
        while style is not None:
            if style.font.size is not None:
                return style.font.size.pt
            style = style.base_style
        return default

    def find_layout_fixes(self, paragraphs: list[dict], edits: dict[int, str]) -> tuple[dict[int, str], list[str]]:
        """Small layout clean-ups for a Word resume, e.g. dropping "(6 months)" after dates. Returns (fixes, notes)."""
        fixes, durations, dated = {}, 0, 0
        has_entries = any(paragraph["kind"] == "entry" for paragraph in paragraphs)
        heading = ""
        for paragraph in paragraphs:
            if paragraph["id"] in edits:
                continue
            if paragraph["kind"] == "heading":
                heading = paragraph["text"]
            elif paragraph["kind"] == "entry" and DURATION.search(paragraph["text"]):
                fixes[paragraph["id"]] = DURATION.sub("", paragraph["text"])
                durations += 1
            elif paragraph["kind"] == "bullet" and has_entries and DATED_LIST_HEADING.search(heading):
                match = YEAR_AT_END.match(paragraph["text"])
                if match:
                    fixes[paragraph["id"]] = f"{match.group(1)}\t{match.group(2)}"
                    dated += 1
        notes = []
        if durations:
            notes.append("Tidied the layout: removed the length of time after the dates, e.g. \"(6 months)\".")
        if dated:
            notes.append(f"Tidied the layout: put the year of {dated} certification(s) or award(s) on the right, "
                         f"like your other entries.")
        return fixes, notes

    def write_edited_docx(self, path: str, edits: dict[int, str], fixes: dict[int, str] | None = None) -> bytes:
        """Return a copy of a Word resume (bytes) with the edits and layout fixes applied; nothing else changes."""
        document = Document(path)
        paragraphs = document.paragraphs
        # An existing title-and-dates line, whose look a dated certification copies
        template = next((paragraph for paragraph in paragraphs if "\t" in paragraph.text), None)

        for position, new_text in (fixes or {}).items():
            paragraph = paragraphs[position]
            if "\t" in paragraph.text:
                # A title-and-dates line: drop the length of time, keeping each run's formatting
                for run in paragraph.runs:
                    run.text = DURATION.sub("", run.text)
            elif template is not None:
                self.make_entry_line(paragraph, template, *new_text.split("\t", 1))

        for position, new_text in edits.items():
            paragraph = paragraphs[position]
            if new_text == "":
                paragraph._element.getparent().remove(paragraph._element)
                continue
            # Keep a typed bullet character, and the first run's formatting for the whole paragraph
            typed_bullet = TYPED_BULLET.match(paragraph.text)
            runs = [run for run in paragraph.runs if run.text]
            runs[0].text = (typed_bullet.group(0) if typed_bullet else "") + new_text
            for run in runs[1:]:
                run.text = ""

        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    def word_preview(self, paragraphs: list[dict], edits: dict[int, str], fixes: dict[int, str]) -> list[dict]:
        """Each paragraph of a tailored Word resume for the web preview: new wording, and if changed or removed."""
        return [{**paragraph, "text": edits.get(paragraph["id"]) or fixes.get(paragraph["id"], paragraph["text"]),
                 "kind": "entry" if paragraph["id"] in fixes else paragraph["kind"],
                 "changed": paragraph["id"] in edits, "removed": edits.get(paragraph["id"]) == ""}
                for paragraph in paragraphs]

    def make_entry_line(self, paragraph: Paragraph, template: Paragraph, name: str, year: str) -> None:
        """Turn a bullet like "Google Certificate, 2023" into a title-and-year line that looks like template."""
        space_before = paragraph.paragraph_format.space_before
        space_after = paragraph.paragraph_format.space_after
        element = paragraph._p
        if element.pPr is not None:
            element.remove(element.pPr)  # drops the bullet style and numbering
        if template._p.pPr is not None:
            element.insert(0, deepcopy(template._p.pPr))
        paragraph.paragraph_format.space_before = space_before
        paragraph.paragraph_format.space_after = space_after

        for run in paragraph.runs:
            element.remove(run._r)
        template_runs = [run for run in template.runs if run.text]
        for source, text in ((template_runs[0], name), (template_runs[-1], f"\t{year}")):
            run = paragraph.add_run(text)
            if source._r.rPr is not None:
                run._r.insert(0, deepcopy(source._r.rPr))