"""TailoringGuard: checks the AI's tailored resumes against the original, so nothing the candidate didn't write
gets through. A part of LogicManager (composition): created by it and used only through it."""

import re

# Line estimates for fit_edits_to_page_count lean the safe way: an original paragraph is counted with
# lines a little wider than its chars_per_line (so fewer lines are freed), new wording a little
# narrower (so it takes more lines)
ORIGINAL_LINE_WIDTH = 1.15
NEW_LINE_WIDTH = 0.9

# Common tools and skills the guard also blocks when the resume doesn't show them, even if this job doesn't list them,
# so a rewrite can't slip in an unrelated skill the candidate never mentioned. Skills that are also everyday words
# ("Go", "R", "Swift", "Spring") are left out; they are still blocked when the job lists them
COMMON_SKILLS = [
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Rust", "Kotlin", "PHP", "Ruby",
    "SQL", "MySQL", "PostgreSQL", "MongoDB", "Oracle", "NoSQL", "Excel", "VBA", "Power BI", "Tableau", "Looker", "SAS",
    "SPSS", "MATLAB", "Spark", "Hadoop", "Kafka", "Airflow", "Snowflake", "Databricks", "dbt", "ETL",
    "AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform", "Jenkins", "Git", "Linux",
    "React", "Angular", "Vue", "Node.js", "Django", "Flask", "HTML", "CSS", "REST API", "GraphQL",
    "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "scikit-learn", "Pandas", "NumPy", "NLP",
    "Computer Vision", "Blockchain", "Salesforce", "SAP", "Jira", "Figma", "Agile", "Scrum", "DevOps", "CI/CD",
]


class TailoringGuard:
    """Checks the AI's tailored resumes against the original resume: nothing the candidate didn't write gets through."""

    def normalise_text(self, text: str) -> str:
        """Lowercase, one kind of dash and quote, single spaces, so a quote still matches across a line break"""
        text = re.sub(r"[‐-―−]", "-", text.lower())
        text = re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text))
        return re.sub(r"\s+", " ", text).strip()

    def resume_parts(self, resume: dict) -> list[str]:
        """Each piece of text in a tailored resume (ai_manager.tailor_resume): text sections, skills,
        and each entry's title, organisation, subtitle and bullets"""
        parts = []
        for section in resume["sections"]:
            parts.append(section["text"])
            for group in section["skill_groups"]:
                parts += group["skills"]
            for entry in section["entries"]:
                parts += [entry["title"], entry["organisation"], entry["subtitle"], *entry["bullets"]]
        return parts

    def skills_shown(self, matched_skills: list[dict], skill_evidence: list[dict], parts: list[str]) -> dict[str, str]:
        """The job card's matched skills that the tailored resume still shows, as {job skill: quote}."""
        card_skills = {match["job_skill"].lower(): match["job_skill"] for match in matched_skills}
        parts = [self.normalise_text(part) for part in parts]

        # Within one part, so a quote can't join the end of one bullet to the start of the next
        def in_resume(text: str) -> bool:
            """True if the text appears as whole words in one piece of the tailored resume."""
            text = self.normalise_text(text).rstrip(".,;:")  # a full stop the AI added doesn't count
            return bool(text) and any(re.search(rf"(?<!\w){re.escape(text)}(?!\w)", part) for part in parts)

        shown = {}
        for item in skill_evidence:
            job_skill = card_skills.get(item["job_skill"].lower())
            if job_skill and in_resume(item["evidence"]):
                shown.setdefault(job_skill, item["evidence"])
        for match in matched_skills:
            if match["job_skill"] not in shown:
                name = next((name for name in (match["candidate_skill"], match["job_skill"]) if in_resume(name)), None)
                if name:
                    shown[match["job_skill"]] = name
        return shown

    def job_keywords(self, job: dict, resume_text: str) -> tuple[list[str], list[str]]:
        """Splits a job's skills into (ones the resume shows, ones it doesn't), for tailoring a Word resume."""
        source = self.normalise_text(resume_text)
        matched = {match["job_skill"].lower() for match in job.get("matched_skills", [])}
        use, avoid = [], []
        for skill in dict.fromkeys([*job.get("required_skills", []), *job.get("listed_skills", [])]):
            if skill.lower() in matched or self.shows_skill(skill, source):
                use.append(skill)
            else:
                avoid.append(skill)
        return use, avoid

    def keep_bullet_groups(self, edits: dict[int, str], paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
        """Never leaves a heading with no points: if edits would remove every bullet under one, none are removed."""
        edits = dict(edits)
        groups, current, above = [], [], ""
        for paragraph in paragraphs:
            if paragraph["kind"] == "bullet":
                current.append(paragraph["id"])
                continue
            if current:
                groups.append((above, current))
                current = []
            above = paragraph["text"].split("\t")[0]
        if current:
            groups.append((above, current))

        notes = []
        for above, ids in groups:
            if all(edits.get(paragraph_id) == "" for paragraph_id in ids):
                for paragraph_id in ids:
                    del edits[paragraph_id]
                notes.append({"kind": "bullet_group_kept", "heading": above})
        return edits, notes

    def fit_edits_to_page_count(self, edits: dict[int, str], paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
        """Keeps a tailored Word resume to its pages: a rewrite needing more lines than the edits free is dropped."""
        edits = dict(edits)
        editable = {paragraph["id"]: paragraph for paragraph in paragraphs if paragraph["editable"]}

        def lines(text: str, paragraph: dict, width: float) -> int:
            """How many lines the text takes in the paragraph, rounding up, at the given width."""
            return -(-len(text) // max(1, int(paragraph["chars_per_line"] * width)))  # rounds up

        spare, longer = 0, []
        for paragraph_id, text in edits.items():
            paragraph = editable[paragraph_id]
            if len(text) <= len(paragraph["text"]):
                # Same font and width, so no longer means no more lines; count what it surely frees
                spare += max(0, lines(paragraph["text"], paragraph, ORIGINAL_LINE_WIDTH)
                             - lines(text, paragraph, NEW_LINE_WIDTH))
            else:
                longer.append(paragraph_id)

        notes = []
        for paragraph_id in longer:
            paragraph = editable[paragraph_id]
            extra = max(0, lines(edits[paragraph_id], paragraph, NEW_LINE_WIDTH)
                        - lines(paragraph["text"], paragraph, ORIGINAL_LINE_WIDTH))
            if extra <= spare:
                spare -= extra
            else:
                del edits[paragraph_id]
                notes.append({"kind": "edit_too_long", "start": paragraph["text"][:50]})
        return edits, notes

    def __numbers_in(self, text: str) -> set[str]:
        """Every whole number, without commas: "2,000 users, 15%" -> {"2000", "15"}."""
        return {number.replace(",", "") for number in re.findall(r"\d+(?:[.,]\d+)*", text)}

    def __appears_in(self, text: str, source: str) -> bool:
        """True if text is in source (already normalise_text'd) as whole words:
        "SQL" is found in "SQL, Excel", but "Java" is not found in "JavaScript"."""
        return re.search(rf"(?<!\w){re.escape(self.normalise_text(text))}(?!\w)", source) is not None

    def skill_pattern(self, skill: str) -> str:
        """A regex that finds a skill however it is spaced, hyphenated or pluralised: "Power BI" also finds "PowerBI"
        and "power-bi", "APIs" also finds "REST API", "Node.js" also finds "Node JS"."""
        tokens = re.findall(r"[a-z0-9+#]+", self.normalise_text(skill))
        parts = []
        for token in tokens:
            # A plural and its singular are the same skill ("APIs", "API"); short words like "AWS" or "JS" are kept
            stem = token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
            parts.append(re.escape(stem) + ("(?:e?s)?" if stem[-1].isalpha() else ""))
        return r"(?<![a-z0-9+#])" + r"[\s\-_./]*".join(parts) + r"(?![a-z0-9+#])"

    def shows_skill(self, skill: str, text: str) -> bool:
        """True if the text names the skill in any of its spellings (see skill_pattern)."""
        return bool(re.findall(r"[a-z0-9+#]", skill.lower())) and \
            re.search(self.skill_pattern(skill), self.normalise_text(text)) is not None

    def __skills_to_block(self, avoid_keywords: list[str], resume_text: str) -> list[str]:
        """The job skills the resume doesn't show, and the common skills it doesn't show either."""
        others = [skill for skill in COMMON_SKILLS if not self.shows_skill(skill, resume_text)]
        return list({skill.lower(): skill for skill in [*avoid_keywords, *others]}.values())

    def __skills_added(self, text: str, blocked: list[str]) -> list[str]:
        """Blocked skills (ones the resume doesn't show) that are written into reworded text."""
        return [skill for skill in blocked if self.shows_skill(skill, text)]

    def __ground_name_and_contact(self, tailored: dict, source: str) -> None:
        """Keeps the name, and each part of the contact line, only if the resume has them."""
        if tailored["name"] and not self.__appears_in(tailored["name"], source):
            tailored["name"] = ""
        # Keep the resume's own separator, e.g. "Singapore | +65 9000 0000" stays with " | "
        pieces = re.split(r"(\s*[·|•]\s*)", tailored["contact"])
        separator = pieces[1] if len(pieces) > 1 else " · "
        tailored["contact"] = separator.join(part for part in pieces[0::2] if part.strip() and self.__appears_in(part, source))

    def __ground_section(self, section: dict, source: str, source_numbers: set[str],
                        avoid_keywords: list[str]) -> tuple[list[str], list[dict]]:
        """Checks one section's text, skills and entries against the resume. Returns (skills removed, notes)."""
        removed_skills, notes = [], []
        if section["kind"] == "text" and not self.__numbers_in(section["text"]) <= source_numbers:
            notes.append({"kind": "section_number_not_in_resume", "heading": section["heading"]})
            section["text"] = ""
        added = self.__skills_added(section["text"], avoid_keywords)
        if added:
            notes.append({"kind": "section_adds_missing_skills", "heading": section["heading"], "skills": added})
            removed_skills += added
            section["text"] = ""

        for group in section["skill_groups"]:
            if group["label"] and not self.__appears_in(group["label"], source):
                group["label"] = ""
            removed_skills += [skill for skill in group["skills"] if not self.shows_skill(skill, source)]
            group["skills"] = [skill for skill in group["skills"] if self.shows_skill(skill, source)]
        section["skill_groups"] = [group for group in section["skill_groups"] if group["skills"]]

        kept_entries = []
        for entry in section["entries"]:
            entry_kept, entry_removed, entry_notes = self.__ground_entry(entry, source, source_numbers, avoid_keywords)
            removed_skills += entry_removed
            notes += entry_notes
            if entry_kept:
                kept_entries.append(entry)
        section["entries"] = kept_entries
        return removed_skills, notes

    def __ground_entry(self, entry: dict, source: str, source_numbers: set[str],
                      avoid_keywords: list[str]) -> tuple[bool, list[str], list[dict]]:
        """Checks one entry (a job, course or project). Returns (keep it?, skills removed, notes)."""
        where = entry["organisation"] or entry["title"]
        if not where or not self.__appears_in(where, source):
            return False, [], [{"kind": "entry_not_in_resume", "title": entry["title"] or entry["organisation"]}]
        if entry["subtitle"] and not self.__appears_in(entry["subtitle"], source):
            entry["subtitle"] = ""
        if entry["dates"] and not self.__appears_in(entry["dates"], source):
            entry["dates"] = ""

        removed_skills, notes = [], []
        bullets_with_numbers_in_resume = [bullet for bullet in entry["bullets"] if self.__numbers_in(bullet) <= source_numbers]
        dropped = len(entry["bullets"]) - len(bullets_with_numbers_in_resume)
        if dropped:
            notes.append({"kind": "bullets_number_not_in_resume", "title": entry["title"], "count": dropped})
        entry["bullets"] = []
        for bullet in bullets_with_numbers_in_resume:
            added = self.__skills_added(bullet, avoid_keywords)
            if added:
                notes.append({"kind": "bullet_adds_missing_skills", "title": entry["title"], "skills": added})
                removed_skills += added
            else:
                entry["bullets"].append(bullet)
        return True, removed_skills, notes

    def ground_tailored_resume(self, tailored: dict, resume_text: str,
                               avoid_keywords: list[str]) -> tuple[dict, list[str], list[dict]]:
        """Removes anything from a tailored PDF resume that the original doesn't contain, including job skills it
        doesn't show (avoid_keywords). Returns (resume, skills removed, notes)."""
        source = self.normalise_text(resume_text)
        source_numbers = self.__numbers_in(source)
        avoid_keywords = self.__skills_to_block(avoid_keywords, resume_text)
        self.__ground_name_and_contact(tailored, source)
        removed_skills, notes = [], []
        for section in tailored["sections"]:
            section_removed, section_notes = self.__ground_section(section, source, source_numbers, avoid_keywords)
            removed_skills += section_removed
            notes += section_notes
        tailored["sections"] = [section for section in tailored["sections"]
                                if section["text"] or section["skill_groups"] or section["entries"]]
        return tailored, list(dict.fromkeys(removed_skills)), notes

    def ground_docx_edits(self, edits: list[dict], paragraphs: list[dict], resume_text: str,
                          avoid_keywords: list[str]) -> tuple[dict[int, str], dict[int, str], list[str], list[dict]]:
        """Keeps only the Word edits that add nothing new. Returns (edits, reasons, skills removed, notes)."""
        source_numbers = self.__numbers_in(self.normalise_text(resume_text))
        editable = {paragraph["id"]: paragraph for paragraph in paragraphs if paragraph["editable"]}
        blocked = self.__skills_to_block(avoid_keywords, resume_text)

        kept, reasons, removed_skills, notes = {}, {}, [], []
        for edit in edits:
            paragraph = editable.get(edit["paragraph"])
            if paragraph is None or edit["paragraph"] in kept or edit["text"] == paragraph["text"]:
                continue  # not a paragraph that may change, already changed, or unchanged
            if edit["text"] == "":
                if paragraph["kind"] == "bullet":
                    kept[edit["paragraph"]] = ""
                    reasons[edit["paragraph"]] = edit["reason"]
                continue
            if not self.__numbers_in(edit["text"]) <= source_numbers:
                notes.append({"kind": "edit_number_not_in_resume", "start": paragraph["text"][:50]})
                continue
            added = self.__skills_added(edit["text"], blocked)
            if added:
                removed_skills += added
                notes.append({"kind": "edit_adds_missing_skills", "start": paragraph["text"][:50], "skills": added})
                continue
            kept[edit["paragraph"]] = edit["text"]
            reasons[edit["paragraph"]] = edit["reason"]
        return kept, reasons, list(dict.fromkeys(removed_skills)), notes

    def resume_view(self, job: dict, resume_name: str) -> tuple[dict, bool]:
        """The job as one resume sees it, and whether that resume is the one that found it. For another resume the
        card's matched skills are cleared, so the AI is not told them and none of them count as shown."""
        if job.get("resume") == resume_name:
            return job, True
        return {**job, "matched_skills": []}, False

    def tailoring_skills(self, job: dict, same_resume: bool, avoid_keywords: list[str], removed_skills: list[str]) -> dict:
        """The skills a tailored page shows: the card's matched skills, and the skills the job wants that the resume
        doesn't show (never added, so the user can decide). Returns {"matched": [...], "not_added": [...]}."""
        matched = job.get("matched_skills", []) if same_resume else []
        # The card's missing skills are for the resume that found the job; for another one, its own gaps
        missing = (job.get("missing_skills", []) if same_resume
                   else [skill for skill in job.get("required_skills", []) if skill in avoid_keywords])
        not_added = {skill.lower(): skill for skill in [*missing, *removed_skills]}
        return {"matched": matched, "not_added": list(not_added.values())}
