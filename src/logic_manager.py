"""Logic layer: the business rules applied to the AI's job records.

filter_and_rank is the entry point: it filters, ranks and gives each kept job an outcome (match_outcome).
"""

import re
from collections import Counter
from urllib.parse import urlparse

# Ordered lowest to highest, so a list index works as an education level number.
# Same values as ai_manager.EDUCATION_LEVELS (copied so this layer does not import another layer)
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]
# Job levels, lowest to highest: the labels MyCareersFuture uses (same as ai_manager.SENIORITY_LEVELS).
# The order is only rough (e.g. "Professional" vs "Senior Executive"), so it is used as a tie-breaker
SENIORITY_LEVELS = [
    "Fresh/entry level", "Non-executive", "Junior Executive", "Executive", "Senior Executive",
    "Professional", "Manager", "Middle Management", "Senior Management",
]
ANY = "Any"  # the filter value meaning "no preference" (from io_manager)
# The highest salary and years of experience a filter can be set to, i.e. "no limit"
# (same values as io_manager's, copied so this layer does not import another layer)
MAX_SALARY = 50000
MAX_EXPERIENCE_YEARS = 60
# A job this many levels or more above the candidate's level ranks below nearer jobs
FAR_ABOVE_LEVELS = 4
SKILL_GAP_LIMIT = 5  # most common missing skills returned by find_skill_gaps
# The outcome match_outcome gives each kept job, and the share of its key skills a strong match needs
OUTCOME_STRONG = "Strong match"
OUTCOME_FAIR = "Fair match"
OUTCOME_STRETCH = "Stretch"
STRONG_MATCH_SHARE = 0.6
# Line estimates for fit_edits_to_page_count lean the safe way: an original paragraph is counted with
# lines a little wider than its chars_per_line (so fewer lines are freed), new wording a little
# narrower (so it takes more lines)
ORIGINAL_LINE_WIDTH = 1.15
NEW_LINE_WIDTH = 0.9


# ==========================================
# 1. FILTERS AND SEARCH STEPS
# Which jobs are removed (and why), and which preferences each job search keeps.
# ==========================================

def is_valid_job_url(url: str | None) -> bool:
    """Checks the link format only, without going online, so it is instant and
    cannot fail because of the network"""
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == "mycareersfuture.gov.sg" or host.endswith(".mycareersfuture.gov.sg")
    )


def filter_reason(job: dict, filters: dict) -> str:
    """Returns why a job is removed, e.g. "salary", or "" if it has a valid link and
    meets all of the user's filters (from io_manager.prompt_filters)"""
    if not is_valid_job_url(job.get("job_url")):
        return "bad link"

    # Salary: drop jobs whose pay range is completely outside the user's range.
    # Jobs that do not state a salary (0) are kept.
    job_min = job.get("min_salary") or 0
    job_max = job.get("max_salary") or 0
    if job_max > 0 and job_max < filters["min_salary"]:
        return "salary"
    if job_min > 0 and job_min > filters["max_salary"]:
        return "salary"

    # Experience: drop jobs that need more years than the user allows
    if (job.get("min_years_experience") or 0) > filters["max_years_experience"]:
        return "experience"

    # Job type: must be one of the job's employment types, unless the user picked "Any".
    # "Permanent" jobs on MyCareersFuture are full-time, so they also count as "Full Time".
    job_types = job.get("employment_types", [])
    if "Permanent" in job_types:
        job_types = job_types + ["Full Time"]
    if filters["job_type"] != ANY and filters["job_type"] not in job_types:
        return "job type"

    # Work arrangement: must match, unless the user picked "Any" or the job does not say
    arrangement = job.get("work_arrangement", "Not specified")
    if filters["work_arrangement"] != ANY and arrangement not in (filters["work_arrangement"], "Not specified"):
        return "work arrangement"

    return ""


def passes_filters(job: dict, filters: dict) -> bool:
    """Returns True if a job has a valid link and meets all of the user's filters"""
    return filter_reason(job, filters) == ""


def count_removed(jobs: list[dict], filters: dict) -> dict[str, int]:
    """Counts how many jobs each filter removed, e.g. {"salary": 14, "job type": 6}.
    A job failing several filters is counted once, under the first one it fails."""
    counts = {}
    for job in jobs:
        reason = filter_reason(job, filters)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def search_steps(preferences: dict | None) -> list[str]:
    """The job searches to try, most specific first: "all" preferences, "job type" only (unless Any), then none."""
    preferences = preferences or {}
    steps = ["all"]
    if preferences.get("job_type", ANY) != ANY:
        steps.append("job type")
    steps.append("general")
    return steps


def filters_for_step(filters: dict, step: str) -> dict:
    """The filters that still apply after a search step: "all" keeps every preference, "job type" only the job type."""
    if step == "all":
        return filters
    # A preference the search had to drop is not applied again, or it would empty the results
    return {
        "min_salary": 0,
        "max_salary": MAX_SALARY,
        "max_years_experience": MAX_EXPERIENCE_YEARS,
        "job_type": filters["job_type"] if step == "job type" else ANY,
        "work_arrangement": ANY,
    }


# ==========================================
# 2. RANKING AND OUTCOME
# Ordering the jobs that are left, giving each one an outcome, and the skills worth building.
# ==========================================

def seniority_gap(job: dict, candidate_seniority: str | None) -> int:
    """How many levels a job's lowest listed level is above the candidate's; 0 if at or below it, or unknown."""
    if candidate_seniority not in SENIORITY_LEVELS:
        return 0
    job_levels = [SENIORITY_LEVELS.index(level) for level in job.get("position_levels", [])
                  if level in SENIORITY_LEVELS]
    if not job_levels:
        return 0
    return max(0, min(job_levels) - SENIORITY_LEVELS.index(candidate_seniority))


def requirement_gaps(profile: dict, job: dict) -> tuple[int, bool, int]:
    """How far the candidate is from a job's requirements, from the AI's job and profile fields:
    years of experience short, whether they meet the education level, and levels below the job's"""
    experience_gap = max(0, job["min_years_experience"] - profile["years_of_experience"])
    # "Not specified", or a level this layer doesn't know, asks for nothing the candidate could miss
    education_ok = (
        job["min_education"] not in EDUCATION_LEVELS
        or EDUCATION_LEVELS.index(job["min_education"]) <= EDUCATION_LEVELS.index(profile["highest_qualification"])
    )
    level_gap = seniority_gap(job, profile.get("seniority_level"))
    return experience_gap, education_ok, level_gap


def match_outcome(profile: dict, job: dict) -> tuple[str, str]:
    """Stretch if short on experience, below the education or far above the level; else Strong match
    (at least STRONG_MATCH_SHARE of the key skills) or Fair match. Returns (outcome, reason)."""
    experience_gap, education_ok, level_gap = requirement_gaps(profile, job)
    gaps = []
    if experience_gap:
        gaps.append(f"needs {experience_gap} more year{'s' if experience_gap != 1 else ''} of experience")
    if not education_ok:
        gaps.append(f"asks for {job['min_education']} or higher")
    if level_gap >= FAR_ABOVE_LEVELS:
        gaps.append("is well above your current level")
    if gaps:
        return OUTCOME_STRETCH, f"This job {' and '.join(gaps)}."

    matched = len(job["matched_skills"])
    required = len(job["required_skills"])
    if not required:
        return OUTCOME_FAIR, "You meet its requirements."  # the job lists no key skills to compare
    if matched / required >= STRONG_MATCH_SHARE:
        return OUTCOME_STRONG, f"You meet its requirements and have {matched} of its {required} key skills."
    return OUTCOME_FAIR, f"You meet its requirements but have {matched} of its {required} key skills."


def select_top_jobs(profile: dict, jobs: list[dict], top_n: int) -> list[dict]:
    """The top_n jobs that best suit the candidate: requirements met first, then most key skills, share, level."""
    # Smallest tuple first: experience gap, education not met, far above level, -matched skills, -share, level gap
    def sort_key(job: dict) -> tuple:
        experience_gap, education_ok, level_gap = requirement_gaps(profile, job)
        matched = len(job["matched_skills"])
        match_ratio = matched / len(job["required_skills"]) if job["required_skills"] else 0
        return (experience_gap, not education_ok, level_gap >= FAR_ABOVE_LEVELS, -matched, -match_ratio, level_gap)

    return sorted(jobs, key=sort_key)[:top_n]


def filter_and_rank(profile: dict, jobs: list[dict], filters: dict, top_n: int = 5) -> list[dict]:
    """Entry point: drops jobs failing the filters or matching no skills, ranks the rest, adds each outcome."""
    kept = []
    for job in jobs:
        if passes_filters(job, filters) and job.get("matched_skills"):
            kept.append(job)
    ranked = []
    for job in select_top_jobs(profile, kept, top_n):
        outcome, reason = match_outcome(profile, job)
        # A copy with the outcome added, so the caller's job is unchanged
        ranked.append({**job, "match_outcome": outcome, "outcome_reason": reason})
    return ranked


def find_skill_gaps(jobs: list[dict]) -> list[tuple[str, int]]:
    """The skills missing from the most jobs, as [(skill, number of jobs)], most common first.
    Only skills missing from 2 or more jobs; "SQL" and "sql" count as one skill"""
    counts = Counter()
    names = {}  # lowercase skill -> how it was first written
    for job in jobs:
        job_skills = {}  # each skill counted once per job, kept in order (a set's order changes between runs)
        for skill in job.get("missing_skills", []):
            key = skill.strip().lower()
            names.setdefault(key, skill.strip())
            job_skills[key] = True
        counts.update(job_skills.keys())  # equal counts stay in the order first seen, so every run agrees
    return [(names[key], count) for key, count in counts.most_common(SKILL_GAP_LIMIT) if count >= 2]


# ==========================================
# 3. TAILORING RULES
# What a tailored resume may say and change, and which of the card's skills it still shows.
# ==========================================

def normalise_text(text: str) -> str:
    """Lowercase, one kind of dash and quote, single spaces, so a quote still matches across a line break"""
    text = re.sub(r"[‐-―−]", "-", text.lower())
    text = re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text))
    return re.sub(r"\s+", " ", text).strip()


def resume_parts(resume: dict) -> list[str]:
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


def skills_shown(matched_skills: list[dict], skill_evidence: list[dict], parts: list[str]) -> dict[str, str]:
    """The job card's matched skills that the tailored resume still shows, as {job skill: quote}."""
    card_skills = {match["job_skill"].lower(): match["job_skill"] for match in matched_skills}
    parts = [normalise_text(part) for part in parts]

    # Within one part, so a quote can't join the end of one bullet to the start of the next
    def in_resume(text: str) -> bool:
        text = normalise_text(text).rstrip(".,;:")  # a full stop the AI added doesn't count
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


def job_keywords(job: dict, resume_text: str) -> tuple[list[str], list[str]]:
    """Splits a job's skills into (ones the resume shows, ones it doesn't), for tailoring a Word resume."""
    source = normalise_text(resume_text)
    matched = {match["job_skill"].lower() for match in job.get("matched_skills", [])}
    use, avoid = [], []
    for skill in dict.fromkeys([*job.get("required_skills", []), *job.get("listed_skills", [])]):
        if skill.lower() in matched or re.search(rf"(?<!\w){re.escape(normalise_text(skill))}(?!\w)", source):
            use.append(skill)
        else:
            avoid.append(skill)
    return use, avoid


def keep_bullet_groups(edits: dict[int, str], paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
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


def fit_edits_to_page_count(edits: dict[int, str], paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
    """Keeps a tailored Word resume to its pages: a rewrite needing more lines than the edits free is dropped."""
    edits = dict(edits)
    editable = {paragraph["id"]: paragraph for paragraph in paragraphs if paragraph["editable"]}

    def lines(text: str, paragraph: dict, width: float) -> int:
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


# ==========================================
# 4. CHECKING THE AI'S ANSWERS
# Before anything uses the AI's replies, they are checked against the portal's listings, the candidate's
# skills and the original resume: nothing the candidate didn't write gets through.
# ==========================================

def _numbers_in(text: str) -> set[str]:
    """Every whole number, without commas: "2,000 users, 15%" -> {"2000", "15"}."""
    return {number.replace(",", "") for number in re.findall(r"\d+(?:[.,]\d+)*", text)}


def _appears_in(text: str, source: str) -> bool:
    """True if text is in source (already normalise_text'd) as whole words:
    "SQL" is found in "SQL, Excel", but "Java" is not found in "JavaScript"."""
    return re.search(rf"(?<!\w){re.escape(normalise_text(text))}(?!\w)", source) is not None


def _skills_added(text: str, avoid_keywords: list[str]) -> list[str]:
    """Job skills the resume doesn't show (avoid_keywords) that are written into reworded text."""
    return [skill for skill in avoid_keywords if _appears_in(skill, normalise_text(text))]


def _ground_skill_matches(requirements: dict, candidate_skills: list[str]) -> dict:
    """Keeps only skill matches that point at a real candidate skill and a real required skill, then derives
    matched_skills and missing_skills from them. "C++ <- Rust" is thrown away if the candidate has no Rust."""
    known_candidate_skills = {skill.lower() for skill in candidate_skills}
    required_skills = requirements["required_skills"]
    required_lookup = {skill.lower(): skill for skill in required_skills}

    candidate_skill_for_job_skill = {}
    for match in requirements["skill_matches"]:
        job_skill = required_lookup.get(match["job_skill"].lower())
        if job_skill and match["candidate_skill"].lower() in known_candidate_skills:
            candidate_skill_for_job_skill.setdefault(job_skill, match["candidate_skill"])  # first match per job skill

    # A copy without skill_matches: the AI's raw answer is not changed
    checked = {key: value for key, value in requirements.items() if key != "skill_matches"}
    checked["matched_skills"] = [{"job_skill": job_skill, "candidate_skill": candidate_skill}
                                 for job_skill, candidate_skill in candidate_skill_for_job_skill.items()]
    checked["missing_skills"] = [skill for skill in required_skills if skill not in candidate_skill_for_job_skill]
    return checked


def add_requirements(listings: list[dict], answers: list[dict | None], profile: dict) -> list[dict]:
    """Each listing merged with its AI answer, plus its matched and missing skills. Listings with no answer are
    left out. answers lines up with listings (ai_manager.extract_requirements_for_listings)."""
    records = []
    for listing, answer in zip(listings, answers):
        if answer is None:
            continue
        # The description was only needed by the AI, so the record keeps the portal's other fields
        portal_fields = {key: value for key, value in listing.items() if key != "description"}
        records.append({**portal_fields, **_ground_skill_matches(answer, profile["core_skills"])})
    return records


def _ground_name_and_contact(tailored: dict, source: str) -> None:
    """Keeps the name, and each part of the contact line, only if the resume has them."""
    if tailored["name"] and not _appears_in(tailored["name"], source):
        tailored["name"] = ""
    # Keep the resume's own separator, e.g. "Singapore | +65 9000 0000" stays with " | "
    pieces = re.split(r"(\s*[·|•]\s*)", tailored["contact"])
    separator = pieces[1] if len(pieces) > 1 else " · "
    tailored["contact"] = separator.join(part for part in pieces[0::2] if part.strip() and _appears_in(part, source))


def _ground_section(section: dict, source: str, source_numbers: set[str],
                    avoid_keywords: list[str]) -> tuple[list[str], list[dict]]:
    """Checks one section's text, skills and entries against the resume. Returns (skills removed, notes)."""
    removed_skills, notes = [], []
    if section["kind"] == "text" and not _numbers_in(section["text"]) <= source_numbers:
        notes.append({"kind": "section_number_not_in_resume", "heading": section["heading"]})
        section["text"] = ""
    added = _skills_added(section["text"], avoid_keywords)
    if added:
        notes.append({"kind": "section_adds_missing_skills", "heading": section["heading"], "skills": added})
        removed_skills += added
        section["text"] = ""

    for group in section["skill_groups"]:
        if group["label"] and not _appears_in(group["label"], source):
            group["label"] = ""
        removed_skills += [skill for skill in group["skills"] if not _appears_in(skill, source)]
        group["skills"] = [skill for skill in group["skills"] if _appears_in(skill, source)]
    section["skill_groups"] = [group for group in section["skill_groups"] if group["skills"]]

    kept_entries = []
    for entry in section["entries"]:
        entry_kept, entry_removed, entry_notes = _ground_entry(entry, source, source_numbers, avoid_keywords)
        removed_skills += entry_removed
        notes += entry_notes
        if entry_kept:
            kept_entries.append(entry)
    section["entries"] = kept_entries
    return removed_skills, notes


def _ground_entry(entry: dict, source: str, source_numbers: set[str],
                  avoid_keywords: list[str]) -> tuple[bool, list[str], list[dict]]:
    """Checks one entry (a job, course or project). Returns (keep it?, skills removed, notes)."""
    where = entry["organisation"] or entry["title"]
    if not where or not _appears_in(where, source):
        return False, [], [{"kind": "entry_not_in_resume", "title": entry["title"] or entry["organisation"]}]
    if entry["subtitle"] and not _appears_in(entry["subtitle"], source):
        entry["subtitle"] = ""
    if entry["dates"] and not _appears_in(entry["dates"], source):
        entry["dates"] = ""

    removed_skills, notes = [], []
    bullets_with_numbers_in_resume = [bullet for bullet in entry["bullets"] if _numbers_in(bullet) <= source_numbers]
    dropped = len(entry["bullets"]) - len(bullets_with_numbers_in_resume)
    if dropped:
        notes.append({"kind": "bullets_number_not_in_resume", "title": entry["title"], "count": dropped})
    entry["bullets"] = []
    for bullet in bullets_with_numbers_in_resume:
        added = _skills_added(bullet, avoid_keywords)
        if added:
            notes.append({"kind": "bullet_adds_missing_skills", "title": entry["title"], "skills": added})
            removed_skills += added
        else:
            entry["bullets"].append(bullet)
    return True, removed_skills, notes


def ground_tailored_resume(tailored: dict, resume_text: str,
                           avoid_keywords: list[str]) -> tuple[dict, list[str], list[dict]]:
    """Removes anything from a tailored PDF resume that the original doesn't contain, including job skills it
    doesn't show (avoid_keywords). Returns (resume, skills removed, notes)."""
    source = normalise_text(resume_text)
    source_numbers = _numbers_in(source)
    _ground_name_and_contact(tailored, source)
    removed_skills, notes = [], []
    for section in tailored["sections"]:
        section_removed, section_notes = _ground_section(section, source, source_numbers, avoid_keywords)
        removed_skills += section_removed
        notes += section_notes
    tailored["sections"] = [section for section in tailored["sections"]
                            if section["text"] or section["skill_groups"] or section["entries"]]
    return tailored, list(dict.fromkeys(removed_skills)), notes


def ground_docx_edits(edits: list[dict], paragraphs: list[dict], resume_text: str,
                      avoid_keywords: list[str]) -> tuple[dict[int, str], dict[int, str], list[str], list[dict]]:
    """Keeps only the Word edits that add nothing new. Returns (edits, reasons, skills removed, notes)."""
    source_numbers = _numbers_in(normalise_text(resume_text))
    editable = {paragraph["id"]: paragraph for paragraph in paragraphs if paragraph["editable"]}

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
        if not _numbers_in(edit["text"]) <= source_numbers:
            notes.append({"kind": "edit_number_not_in_resume", "start": paragraph["text"][:50]})
            continue
        added = _skills_added(edit["text"], avoid_keywords)
        if added:
            removed_skills += added
            notes.append({"kind": "edit_adds_missing_skills", "start": paragraph["text"][:50], "skills": added})
            continue
        kept[edit["paragraph"]] = edit["text"]
        reasons[edit["paragraph"]] = edit["reason"]
    return kept, reasons, list(dict.fromkeys(removed_skills)), notes

