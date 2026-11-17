"""TailoringGuard: checks the AI's tailored resumes against the original, so nothing the candidate didn't write
gets through. A part of LogicManager (composition): created by it and used only through it."""

import difflib
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

# Qualifications a rewrite may only name if the resume does: aiming a profile at a job that asks for a degree, the
# AI can turn "Diploma graduate" into "Degree holder". Plurals and "Bachelor's" are found too (see __skill_pattern).
# Short forms that are also other words ("BA" is also Business Analyst, "MA") are left out
QUALIFICATIONS = [
    "Degree", "Bachelor", "BSc", "BEng", "Master", "MSc", "MBA", "PhD", "Doctorate", "Postgraduate",
    "Diploma", "Nitec", "A-Level", "O-Level", "N-Level",
]

# Words that make a skill or a piece of work sound stronger than the resume says ("Wrote SQL queries" -> "Performed
# complex SQL scripting", "Comfortable with" -> "Proficient in"). A rewording may use one only if its original does
SKILL_LEVEL_WORDS = [
    "mastery", "master", "expert", "expertise", "advanced", "proficient", "proficiency", "extensive", "extensively",
    "complex", "deep", "deeply", "strong", "strongly", "highly", "seasoned", "exceptional", "outstanding",
    "excellent", "fluent", "fluency", "specialist", "spearheaded", "architected", "pioneered",
]
# A brand word dropped when comparing tools, so "Microsoft Excel" in a rewording still matches "Excel" in its original
BRAND_WORDS = ("microsoft ", "google ", "amazon ", "adobe ")
# A qualification's full name, e.g. "Diploma in Financial Technology" or "Bachelor of Science": the qualification word,
# "in" or "of", then the field in capitals (joined by and, &, of or in). A rewording may name one only as its
# original does, so "Diploma in Data Analytics and Financial Technology" can't stand in for the real one
QUALIFICATION_NAME = re.compile(
    r"\b(?i:" + "|".join(re.escape(name) for name in QUALIFICATIONS) + r")(?:'s)?\s+(?:in|of)\s+"
    r"[A-Z][\w&-]*(?:\s+(?:and|&|of|in|[A-Z][\w&-]*))*")


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
            if skill.lower() in matched or self.__shows_skill(skill, source):
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

    def __skill_pattern(self, skill: str) -> str:
        """A regex that finds a skill however it is spaced, hyphenated or pluralised: "Power BI" also finds "PowerBI"
        and "power-bi", "APIs" also finds "REST API", "Node.js" also finds "Node JS"."""
        tokens = re.findall(r"[a-z0-9+#]+", self.normalise_text(skill))
        parts = []
        for token in tokens:
            # A plural and its singular are the same skill ("APIs", "API"); short words like "AWS" or "JS" are kept
            stem = token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
            parts.append(re.escape(stem) + ("(?:e?s)?" if stem[-1].isalpha() else ""))
        # Between words: spaces and the punctuation a resume puts inside one item, so "English: Native" and
        # "Spanish: Upper Intermediate (B2)" are still found in the resume they were copied from
        return r"(?<![a-z0-9+#])" + r"[\s\-_./:,()|&]*".join(parts) + r"(?![a-z0-9+#])"

    def __shows_skill(self, skill: str, text: str) -> bool:
        """True if the text names the skill in any of its spellings (see __skill_pattern)."""
        return bool(re.findall(r"[a-z0-9+#]", skill.lower())) and \
            re.search(self.__skill_pattern(skill), self.normalise_text(text)) is not None

    def __skills_to_block(self, avoid_keywords: list[str], resume_text: str) -> list[str]:
        """The job skills the resume doesn't show, and the common skills it doesn't show either."""
        others = [skill for skill in COMMON_SKILLS if not self.__shows_skill(skill, resume_text)]
        return list({skill.lower(): skill for skill in [*avoid_keywords, *others]}.values())

    def __skills_added(self, text: str, blocked: list[str]) -> list[str]:
        """Blocked skills (ones the resume doesn't show) that are written into reworded text."""
        return [skill for skill in blocked if self.__shows_skill(skill, text)]

    def __recombined(self, original: str, new: str, tools: list[str],
                     is_point: bool) -> tuple[list[str], list[str], list[str]]:
        """What a rewording claims that its original doesn't, although each word is somewhere in the resume: a tool
        named in a point whose original doesn't name it ("Built Tableau and Power BI dashboards" for "Built Power BI
        dashboards"), a skill-level word the original doesn't use ("complex SQL scripting" for "SQL queries"), and a
        qualification renamed ("Diploma in Data Analytics and Financial Technology" for "Diploma in Financial
        Technology"). A summary may gather the resume's skills, so tools are checked in points only.
        Returns (tools added, skill-level words added, qualification names the original doesn't have)."""
        def names(tool: str, text: str) -> bool:
            bare = tool.lower()
            for brand in BRAND_WORDS:
                bare = bare.removeprefix(brand)
            return self.__shows_skill(tool, text) or self.__shows_skill(bare, text)

        added_tools = [tool for tool in tools if is_point and names(tool, new) and not names(tool, original)]
        new_words = set(re.findall(r"[a-z]+", self.normalise_text(new)))
        original_words = set(re.findall(r"[a-z]+", self.normalise_text(original)))
        stronger = [word for word in SKILL_LEVEL_WORDS if word in new_words and word not in original_words]
        source = self.normalise_text(original)
        # A name never ends on a joining word: "Diploma in Financial Technology and a 6-month..." names the diploma only
        names = (re.sub(r"(?i)(?:[\s,]+(?:and|&|of|in))+[\s,]*$", "", match.group(0))
                 for match in QUALIFICATION_NAME.finditer(new))
        renamed = list(dict.fromkeys(name for name in names if not self.__appears_in(name, source)))
        return added_tools, stronger, renamed

    def __ground_sentences(self, text: str, resume_text: str, source_numbers: set[str], blocked: list[str],
                           missing: list[str], tools: list[str], is_point: bool) -> tuple[str, dict]:
        """Checks reworded text one sentence at a time. A sentence that adds a number, a blocked skill or a
        qualification the resume doesn't have, or that recombines the resume (a tool its original sentence doesn't
        name, or a stronger skill-level word; see __recombined), goes back to the resume's closest original sentence;
        with none close enough, it is left out. So one bad sentence never costs a whole summary or point.
        Returns (checked text, {"kept_original", "removed", "skills", "qualifications", "number", "tools", "words"})."""
        # The resume's own sentences: one per sentence or bullet point. A PDF wraps long lines, so a line that goes on
        # in lower case joins the line before it; a heading or a name ("PROFESSIONAL SUMMARY") stays on its own
        lines = []
        for line in (line.strip() for line in re.split(r"\n|[•●▪◦]", resume_text)):
            if lines and line[:1].islower() and not lines[-1].endswith((".", "!", "?")):
                lines[-1] += " " + line
            elif line:
                lines.append(line)
        originals = [" ".join(sentence.split()) for line in lines for sentence in re.split(r"(?<=[.!?])\s+", line)]
        originals = [sentence for sentence in originals if len(sentence) > 15]
        report = {"kept_original": 0, "removed": 0, "skills": [], "qualifications": [], "number": False,
                  "tools": [], "words": []}
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            unused = [original for original in originals if original not in kept]
            closest = max(unused, default="", key=lambda original: difflib.SequenceMatcher(
                None, original.lower(), sentence.lower()).ratio())
            close = bool(closest) and difflib.SequenceMatcher(None, closest.lower(), sentence.lower()).ratio() >= 0.5
            skills, claimed = self.__skills_added(sentence, blocked), self.__skills_added(sentence, missing)
            wrong_number = not self.__numbers_in(sentence) <= source_numbers
            # A sentence with no close original is new wording: compared with the whole resume, it can't recombine
            tools_added, stronger, renamed = self.__recombined(closest if close else resume_text, sentence, tools,
                                                               is_point)
            claimed += renamed
            if not (skills or claimed or wrong_number or tools_added or stronger):
                kept.append(sentence)
                continue
            report["skills"] += skills
            report["qualifications"] += claimed
            report["number"] = report["number"] or wrong_number
            report["tools"] += tools_added
            report["words"] += stronger
            if close:
                kept.append(closest)
                report["kept_original"] += 1
            else:
                report["removed"] += 1
        for key in ("skills", "qualifications", "tools", "words"):
            report[key] = list(dict.fromkeys(report[key]))
        return " ".join(sentence for sentence in kept if sentence), report

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
            removed_skills += [skill for skill in group["skills"] if not self.__shows_skill(skill, source)]
            group["skills"] = [skill for skill in group["skills"] if self.__shows_skill(skill, source)]
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

    def ground_tailored_resume(self, tailored: dict, resume_text: str, avoid_keywords: list[str],
                               use_keywords: list[str] | None = None) -> tuple[dict, list[str], list[dict]]:
        """Removes anything from a tailored PDF resume that the original doesn't contain, including job skills it
        doesn't show (avoid_keywords), and undoes rewordings that move the job skills it does show (use_keywords)
        or common tools to work they weren't part of. Returns (resume, skills removed, notes)."""
        source = self.normalise_text(resume_text)
        source_numbers = self.__numbers_in(source)
        avoid_keywords = self.__skills_to_block(avoid_keywords, resume_text)
        tools = list({tool.lower(): tool for tool in [*(use_keywords or []), *COMMON_SKILLS]}.values())
        self.__ground_name_and_contact(tailored, source)
        removed_skills, notes = [], []
        # Reworded text is checked a sentence at a time first: one that adds a number, a job skill or a
        # qualification (e.g. "Degree" for a diploma holder) the resume doesn't have goes back to the resume's own
        # wording, so a summary or point is never lost for one bad sentence. Entry titles ("Diploma in ...") are
        # checked against the resume in __ground_entry, and the section checks below stay as a second net
        missing = [name for name in QUALIFICATIONS if not self.__shows_skill(name, resume_text)]

        def checked(text: str, where: str, is_point: bool) -> str:
            text, report = self.__ground_sentences(text, resume_text, source_numbers, avoid_keywords, missing,
                                                   tools, is_point)
            if report["kept_original"] or report["removed"]:
                notes.append({"kind": "sentences_checked", "where": where, "is_point": is_point, **report})
                removed_skills.extend(report["skills"])
            return text

        for section in tailored["sections"]:
            if section["text"]:
                section["text"] = checked(section["text"], section["heading"], False)
            for entry in section["entries"]:
                entry["bullets"] = [bullet for bullet in (checked(bullet, entry["title"], True)
                                                          for bullet in entry["bullets"]) if bullet]
        for section in tailored["sections"]:
            section_removed, section_notes = self.__ground_section(section, source, source_numbers, avoid_keywords)
            removed_skills += section_removed
            notes += section_notes
        tailored["sections"] = [section for section in tailored["sections"]
                                if section["text"] or section["skill_groups"] or section["entries"]]
        return tailored, list(dict.fromkeys(removed_skills)), notes

    def ground_docx_edits(self, edits: list[dict], paragraphs: list[dict], resume_text: str, avoid_keywords: list[str],
                          use_keywords: list[str] | None = None
                          ) -> tuple[dict[int, str], dict[int, str], list[str], list[dict]]:
        """Keeps only the Word edits that add nothing new, and nothing the resume says only about other work: a
        bullet may name only the tools (the job skills the resume shows, use_keywords, and common tools) its own
        original names. Returns (edits, reasons, skills removed, notes)."""
        source_numbers = self.__numbers_in(self.normalise_text(resume_text))
        editable = {paragraph["id"]: paragraph for paragraph in paragraphs if paragraph["editable"]}
        blocked = self.__skills_to_block(avoid_keywords, resume_text)
        missing = [name for name in QUALIFICATIONS if not self.__shows_skill(name, resume_text)]
        tools = list({tool.lower(): tool for tool in [*(use_keywords or []), *COMMON_SKILLS]}.values())

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
            # A qualification the resume doesn't name, e.g. "Degree holder" for a diploma: the paragraph stays as it was
            claimed = self.__skills_added(edit["text"], missing)
            if claimed:
                notes.append({"kind": "edit_adds_qualification", "start": paragraph["text"][:50], "qualifications": claimed})
                continue
            added = self.__skills_added(edit["text"], blocked)
            if added:
                removed_skills += added
                notes.append({"kind": "edit_adds_missing_skills", "start": paragraph["text"][:50], "skills": added})
                continue
            # Only real facts, but recombined: a qualification renamed, a tool this bullet doesn't name, or a stronger
            # skill-level word
            tools_added, stronger, renamed = self.__recombined(paragraph["text"], edit["text"], tools,
                                                               paragraph["kind"] == "bullet")
            if renamed:
                notes.append({"kind": "edit_adds_qualification", "start": paragraph["text"][:50], "qualifications": renamed})
                continue
            if tools_added or stronger:
                notes.append({"kind": "edit_recombines", "start": paragraph["text"][:50], "tools": tools_added,
                              "words": stronger})
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
        # A removed skill is listed only if the job asks for it: the guard also removes the AI's rewordings of the
        # resume's own items, and those are not something "the job wants"
        wanted = {skill.lower() for skill in [*job.get("required_skills", []), *avoid_keywords]}
        not_added = {skill.lower(): skill for skill in [*missing, *removed_skills] if skill.lower() in wanted}
        return {"matched": matched, "not_added": list(not_added.values())}
