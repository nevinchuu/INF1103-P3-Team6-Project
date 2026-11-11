"""Logic Manager: the business rules applied to the AI's job records.

filter_and_rank is the entry point: it filters, ranks (by each rule in self.rules, in order) and gives each kept
job an outcome (match_outcome, from its MatchRules). The fact checks on tailored resumes are done by its
TailoringGuard part.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

from logic.education_rule import EducationRule
from logic.experience_rule import ExperienceRule
from logic.match_rule import MatchRule
from logic.seniority_rule import SeniorityRule
from logic.tailoring_guard import TailoringGuard

# Ordered lowest to highest, so a list index works as an education level number.
# Same values as the AI schemas' EDUCATION_LEVELS in ai_manager.py (copied so this layer does not import another layer)
EDUCATION_LEVELS = ["None", "Secondary", "ITE/Nitec", "A-Level", "Diploma", "Bachelor's", "Master's", "Doctorate"]

# Job levels, lowest to highest: the labels MyCareersFuture uses (same as the AI schemas' SENIORITY_LEVELS in ai_manager.py).
# The order is only rough (e.g. "Professional" vs "Senior Executive"), so it is used as a tie-breaker
SENIORITY_LEVELS = [
    "Fresh/entry level", "Non-executive", "Junior Executive", "Executive", "Senior Executive",
    "Professional", "Manager", "Middle Management", "Senior Management",
]

# MyCareersFuture tags postings with workplace behaviours as well as real skills ("Work in A Fast Paced
# Environment" sits beside "SQL"), and the AI copies what it is given. They are not things a candidate can
# train for, so counting them would both dilute a job's skill share and offer them in the what-if list.
# The prompt asks for concrete skills; this is the check that does not depend on the AI obeying it.
NOT_SKILL_PHRASES = {
    "communication", "written and verbal communication", "verbal communication", "interpersonal skills",
    "teamwork", "team player", "team work", "collaboration", "problem solving", "critical thinking",
    "attention to detail", "detail oriented", "time management", "multitasking", "adaptability",
    "flexibility", "leadership", "initiative", "positive attitude", "work ethic", "hardworking",
    "reliable", "self motivated", "independent", "proactive", "organisational skills",
    "organizational skills", "interpersonal", "fast paced environment", "customer service",
}
# A tag opening this way is describing a disposition, not naming a skill ("Able to Work Independently")
NOT_SKILL_OPENERS = ("work in", "work under", "work independently", "able to", "ability to", "willing to",
                     "willingness to", "comfortable with", "eager to", "passion for", "passionate about")

# Tags naming an activity rather than anything a course could teach. Unlike the behaviours above these read
# like real skills, but there is nothing to go and learn: "Reporting" names no tool, standard or technique.
# Kept deliberately industry-neutral - no tool or field names - because the candidate's field comes from their
# resume, not from this layer. Naming this field's tools here would only fit one kind of candidate, and a
# chemistry or biology resume must get its own field's tools out of the same rule. That part is the AI's job
# (the required_skills rule in ai_manager's prompt); this set is only the floor under it.
GENERIC_ACTIVITY_PHRASES = {
    "reporting", "report writing", "presentation", "presentations", "key performance indicators", "kpis",
    "ad hoc analysis", "requirements gathering", "project timeline", "project timelines",
    "stakeholder management", "stakeholder engagement", "process improvement", "written communication",
    "general administration", "administrative support", "day to day operations",
}
MAX_SKILL_WORDS = 6  # a named tool or technique is short; a long phrase is a sentence about the person

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


class LogicManager:
    """The business rules for AI-enriched job records: filtering, ranking, outcomes, skill gaps and tailoring checks."""

    def __init__(self, rules: Sequence[Callable[[dict], float]] | None = None,
                 match_rules: Sequence[MatchRule] | None = None) -> None:
        # The ranking rules: each takes a job's fit (see job_fit) and returns its sort value, smaller first. A later
        # rule only breaks ties in the earlier ones. Passing a different list adds or reorders rules without
        # changing this class (OCP). None means the standard order from Phase 1.
        self.__rules = tuple(rules) if rules is not None else self.default_rules()
        # Aggregation: the match rules can be handed in (a test, or a team adding a rule); None means the standard
        # three. They hold no state of this manager, so the same rule objects can be shared
        self.__match_rules = tuple(match_rules) if match_rules is not None else self.default_match_rules()
        # Composition: the guard is a part of this manager, created with it and used only through it
        self.__guard = TailoringGuard()

    @classmethod
    def default_rules(cls) -> tuple[Callable[[dict], float], ...]:
        """The ranking order from Phase 1: requirements met first, then most key skills, share, level."""
        return (cls.rank_by_experience_gap, cls.rank_by_education, cls.rank_by_far_above_level,
                cls.rank_by_skill_count, cls.rank_by_skill_share, cls.rank_by_level_gap)

    @property
    def rules(self) -> tuple[Callable[[dict], float], ...]:
        """The ranking rules in use, in order."""
        return self.__rules

    @staticmethod
    def default_match_rules() -> tuple[MatchRule, ...]:
        """The requirements from Phase 1's multi-condition rule: experience, education and level."""
        return (ExperienceRule(), EducationRule(), SeniorityRule(FAR_ABOVE_LEVELS))

    @property
    def match_rules(self) -> tuple[MatchRule, ...]:
        """The match rules in use, in the order their shortfalls are listed."""
        return self.__match_rules

    # ---------- how well a job fits, and the standard ranking rules ----------

    @classmethod
    def job_fit(cls, profile: dict, job: dict) -> dict:
        """How one AI-enriched job compares with the candidate, measured once for every rule:
        {"experience_gap": years short, "education_ok": meets the minimum education, "level_gap": levels the
        job's lowest listed level is above theirs, "matched" / "required": key skills had / listed,
        "share": matched / required (0 if the job lists none)}."""
        experience_gap = max(0, job["min_years_experience"] - profile["years_of_experience"])
        # "Not specified", or a level this layer doesn't know, asks for nothing the candidate could miss
        education_ok = (
            job["min_education"] not in EDUCATION_LEVELS
            or EDUCATION_LEVELS.index(job["min_education"]) <= EDUCATION_LEVELS.index(profile["highest_qualification"])
        )
        matched, required = len(job["matched_skills"]), len(job["required_skills"])
        return {"experience_gap": experience_gap, "education_ok": education_ok,
                "level_gap": cls.__seniority_gap(job, profile.get("seniority_level")),
                "matched": matched, "required": required, "share": matched / required if required else 0}

    @staticmethod
    def __seniority_gap(job: dict, candidate_seniority: str | None) -> int:
        """How many levels a job's lowest listed level is above the candidate's; 0 if at or below it, or unknown."""
        if candidate_seniority not in SENIORITY_LEVELS:
            return 0
        job_levels = [SENIORITY_LEVELS.index(level) for level in job.get("position_levels", [])
                      if level in SENIORITY_LEVELS]
        if not job_levels:
            return 0
        return max(0, min(job_levels) - SENIORITY_LEVELS.index(candidate_seniority))

    @staticmethod
    def rank_by_experience_gap(fit: dict) -> float:
        """Jobs asking for fewer years of experience than the candidate lacks come first."""
        return fit["experience_gap"]

    @staticmethod
    def rank_by_education(fit: dict) -> float:
        """Jobs whose minimum education the candidate meets come before those they don't."""
        return not fit["education_ok"]

    @staticmethod
    def rank_by_far_above_level(fit: dict) -> float:
        """Jobs FAR_ABOVE_LEVELS or more above the candidate's level rank below nearer ones."""
        return fit["level_gap"] >= FAR_ABOVE_LEVELS

    @staticmethod
    def rank_by_skill_count(fit: dict) -> float:
        """More matched key skills first."""
        return -fit["matched"]

    @staticmethod
    def rank_by_skill_share(fit: dict) -> float:
        """A higher share of the job's key skills first (breaks ties in the number of matched skills)."""
        return -fit["share"]

    @staticmethod
    def rank_by_level_gap(fit: dict) -> float:
        """A job nearer the candidate's level first (the last tie-breaker)."""
        return fit["level_gap"]

    # ---------- filters and search steps ----------


    @staticmethod
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

    def filter_reason(self, job: dict, filters: dict) -> str:
        """Returns why a job is removed, e.g. "salary", or "" if it has a valid link and
        meets all of the user's filters (from IOManager.prompt_filters)"""
        if not self.is_valid_job_url(job.get("job_url")):
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

    def passes_filters(self, job: dict, filters: dict) -> bool:
        """Returns True if a job has a valid link and meets all of the user's filters"""
        return self.filter_reason(job, filters) == ""

    def count_removed(self, jobs: list[dict], filters: dict) -> dict[str, int]:
        """Counts how many jobs each filter removed, e.g. {"salary": 14, "job type": 6}.
        A job failing several filters is counted once, under the first one it fails."""
        counts = {}
        for job in jobs:
            reason = self.filter_reason(job, filters)
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    @staticmethod
    def search_steps(preferences: dict | None) -> list[str]:
        """The job searches to try, most specific first: "all" preferences, "job type" only (unless Any), then none."""
        preferences = preferences or {}
        steps = ["all"]
        if preferences.get("job_type", ANY) != ANY:
            steps.append("job type")
        steps.append("general")
        return steps

    @staticmethod
    def filters_for_step(filters: dict, step: str) -> dict:
        """The filters that still apply after a search step: "all" keeps every preference, "job type" only the
        job type."""
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

    # ---------- ranking and outcome ----------

    def requirement_gaps(self, profile: dict, job: dict) -> tuple[int, bool, int]:
        """How far the candidate is from a job's requirements: years of experience short, whether they meet the
        education level, and levels below the job's."""
        fit = self.job_fit(profile, job)
        return fit["experience_gap"], fit["education_ok"], fit["level_gap"]

    def match_outcome(self, profile: dict, job: dict) -> tuple[str, str]:
        """The multi-condition rule. Stretch if any match rule finds a shortfall (by default: short on experience,
        below the education or far above the level); else Strong match (at least STRONG_MATCH_SHARE of the key
        skills) or Fair match. Returns (outcome, reason)."""
        fit = self.job_fit(profile, job)
        # Polymorphism: every rule is asked the same way, with no branching on which rule it is
        gaps = [gap for gap in (rule.shortfall(fit, job) for rule in self.__match_rules) if gap]
        if gaps:
            return OUTCOME_STRETCH, f"This job {' and '.join(gaps)}."

        if not fit["required"]:
            return OUTCOME_FAIR, "You meet its requirements."  # the job lists no key skills to compare
        if fit["share"] >= STRONG_MATCH_SHARE:
            return OUTCOME_STRONG, (f"You meet its requirements and have {fit['matched']} of its "
                                    f"{fit['required']} key skills.")
        return OUTCOME_FAIR, f"You meet its requirements but have {fit['matched']} of its {fit['required']} key skills."

    def sort_key(self, profile: dict, job: dict) -> tuple:
        """One job's sort key: every rule's value in order (smaller sorts first, so the best match is on top).
        No branching on which rule it is: each rule answers for itself."""
        fit = self.job_fit(profile, job)
        return tuple(rule(fit) for rule in self.__rules)

    def rank_jobs(self, profile: dict, jobs: list[dict]) -> list[dict]:
        """All the jobs, best match first, in the order of the ranking rules."""
        return sorted(jobs, key=lambda job: self.sort_key(profile, job))

    def select_top_jobs(self, profile: dict, jobs: list[dict], top_n: int) -> list[dict]:
        """The top_n jobs that best suit the candidate."""
        return self.rank_jobs(profile, jobs)[:top_n]

    def filter_and_rank(self, profile: dict, jobs: list[dict], filters: dict, top_n: int = 5) -> list[dict]:
        """Entry point: drops jobs failing the filters or matching no skills, ranks the rest, adds each outcome."""
        kept = []
        for job in jobs:
            if self.passes_filters(job, filters) and job.get("matched_skills"):
                kept.append(job)
        ranked = []
        for job in self.select_top_jobs(profile, kept, top_n):
            outcome, reason = self.match_outcome(profile, job)
            # A copy with the outcome added, so the caller's job is unchanged
            ranked.append({**job, "match_outcome": outcome, "outcome_reason": reason})
        return ranked

    def find_skill_gaps(self, jobs: list[dict]) -> list[tuple[str, int]]:
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

    # ---------- checking the AI's answers ----------

    @staticmethod
    def is_learnable_skill(name: str) -> bool:
        """Whether a tag names something a candidate could train for, rather than a way of behaving at work or
        an activity with nothing behind it to learn. "Power BI", "HPLC" and "GMP" are skills;
        "Work in A Fast Paced Environment", "Team Player" and "Reporting" are not."""
        words = name.strip().lower().replace("-", " ").split()
        if not words:
            return False
        plain = " ".join(words)
        if plain.endswith(" skills"):  # "Communication Skills" is the same tag as "Communication"
            plain = plain[: -len(" skills")]
        return (plain not in NOT_SKILL_PHRASES
                and plain not in GENERIC_ACTIVITY_PHRASES
                and not plain.startswith(NOT_SKILL_OPENERS)
                and len(words) <= MAX_SKILL_WORDS)

    @staticmethod
    def __ground_skill_matches(requirements: dict, candidate_skills: list[str]) -> dict:
        """Keeps only skill matches that point at a real candidate skill and a real required skill, then derives
        matched_skills and missing_skills from them. "C++ <- Rust" is thrown away if the candidate has no Rust.
        Tags that are not skills at all are dropped first, so they neither count towards the job's skill share
        nor turn up as something to learn."""
        known_candidate_skills = {skill.lower() for skill in candidate_skills}
        required_skills = [skill for skill in requirements["required_skills"]
                           if LogicManager.is_learnable_skill(skill)]
        requirements = {**requirements, "required_skills": required_skills}
        required_lookup = {skill.lower(): skill for skill in required_skills}

        candidate_skill_for_job_skill = {}
        for match in requirements["skill_matches"]:
            job_skill = required_lookup.get(match["job_skill"].lower())
            if job_skill and match["candidate_skill"].lower() in known_candidate_skills:
                candidate_skill_for_job_skill.setdefault(job_skill, match["candidate_skill"])  # first match per skill

        # A copy without skill_matches: the AI's raw answer is not changed
        checked = {key: value for key, value in requirements.items() if key != "skill_matches"}
        checked["matched_skills"] = [{"job_skill": job_skill, "candidate_skill": candidate_skill}
                                     for job_skill, candidate_skill in candidate_skill_for_job_skill.items()]
        checked["missing_skills"] = [skill for skill in required_skills if skill not in candidate_skill_for_job_skill]
        return checked

    def add_requirements(self, listings: list[dict], answers: list[dict | None], profile: dict) -> list[dict]:
        """Each listing merged with its AI answer, plus its matched and missing skills. Listings with no answer are
        left out. answers lines up with listings (AIManager.extract_requirements_for_listings)."""
        records = []
        for listing, answer in zip(listings, answers):
            if answer is None:
                continue
            # The description was only needed by the AI, so the record keeps the portal's other fields
            portal_fields = {key: value for key, value in listing.items() if key != "description"}
            records.append({**portal_fields, **self.__ground_skill_matches(answer, profile["core_skills"])})
        return records

    # ---------- tailored resumes (the TailoringGuard part) ----------
    # LogicManager is the one "domain rules" contract both apps use. These checks live in its TailoringGuard part
    # (composition), and each method below passes straight to it, so callers never reach into the part and the
    # guard can change without touching them.

    def normalise_text(self, text: str) -> str:
        return self.__guard.normalise_text(text)

    def resume_parts(self, resume: dict) -> list[str]:
        return self.__guard.resume_parts(resume)

    def skills_shown(self, matched_skills: list[dict], skill_evidence: list[dict], parts: list[str]) -> dict[str, str]:
        return self.__guard.skills_shown(matched_skills, skill_evidence, parts)

    def job_keywords(self, job: dict, resume_text: str) -> tuple[list[str], list[str]]:
        return self.__guard.job_keywords(job, resume_text)

    def keep_bullet_groups(self, edits: dict[int, str], paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
        return self.__guard.keep_bullet_groups(edits, paragraphs)

    def fit_edits_to_page_count(self, edits: dict[int, str],
                                paragraphs: list[dict]) -> tuple[dict[int, str], list[dict]]:
        return self.__guard.fit_edits_to_page_count(edits, paragraphs)

    def ground_tailored_resume(self, tailored: dict, resume_text: str,
                               avoid_keywords: list[str]) -> tuple[dict, list[str], list[dict]]:
        return self.__guard.ground_tailored_resume(tailored, resume_text, avoid_keywords)

    def ground_docx_edits(self, edits: list[dict], paragraphs: list[dict], resume_text: str,
                          avoid_keywords: list[str]) -> tuple[dict[int, str], dict[int, str], list[str], list[dict]]:
        return self.__guard.ground_docx_edits(edits, paragraphs, resume_text, avoid_keywords)

    def resume_view(self, job: dict, resume_name: str) -> tuple[dict, bool]:
        return self.__guard.resume_view(job, resume_name)

    def tailoring_skills(self, job: dict, same_resume: bool, avoid_keywords: list[str],
                         removed_skills: list[str]) -> dict:
        return self.__guard.tailoring_skills(job, same_resume, avoid_keywords, removed_skills)
