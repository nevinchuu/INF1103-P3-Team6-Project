"""Logic Manager: the business rules applied to the AI's job records.

filter_and_rank is the entry point: it filters, ranks (by each ranking key, in order) and gives each kept job an
outcome (match_outcome, from its MatchRules). It has two parts: a TailoringGuard (the fact checks on tailored
resumes) and a CareerSimulator (what-if and roadmaps). What the user types is checked before it gets here, by the
IO manager's InputValidator.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

from logic_layer.career_simulator import CareerSimulator
from logic_layer.education_rule import EducationRule
from logic_layer.experience_rule import ExperienceRule
from logic_layer.match_rule import MatchRule, OUTCOME_FAIR, OUTCOME_STRETCH, OUTCOME_STRONG
from logic_layer.ranking_rules import RankingRules
from logic_layer.roadmap_planner import RoadmapPlanner
from logic_layer.seniority_rule import SeniorityRule
from logic_layer.skill_name import SkillName
from logic_layer.tailoring_guard import TailoringGuard

# The choices and limits a search's filters can take ("Any" = no preference): the business rules filter and rank
# with them, so they live here; the IO manager's InputValidator checks the form against them
ANY = "Any"
MAX_SALARY = 50000
MAX_EXPERIENCE_YEARS = 60
JOB_TYPES = [ANY, "Full Time", "Part Time", "Contract", "Internship/Attachment"]
WORK_ARRANGEMENTS = [ANY, "Onsite", "Remote", "Hybrid"]  # the order the dropdowns list them
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
    "internship", "internships",
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
# Each job shows MIN_KEY_SKILLS to MAX_KEY_SKILLS key skills, its most important first (the AI is asked for 4-5,
# read like a resume's skills). A longer list is cut; when the AI names fewer, the employer's own skill tags on the
# posting fill the list up to the minimum (only tags that pass the same skill test)
MIN_KEY_SKILLS = 4
MAX_KEY_SKILLS = 5
# Praise in front of a tag ("Strong Attention to Detail") doesn't change what it is
PRAISE_WORDS = ("strong", "good", "excellent", "solid", "sound", "proven", "effective")

# A job this many levels or more above the candidate's level ranks below nearer jobs
FAR_ABOVE_LEVELS = 4
SKILL_GAP_LIMIT = 5  # most common missing skills returned by find_skill_gaps

# The share of a job's key skills a strong match needs (the outcomes themselves are in match_rule)
STRONG_MATCH_SHARE = 0.6


class LogicManager(RankingRules):
    """The business rules for AI-enriched job records: filtering, ranking, outcomes, skill gaps and tailoring checks.
    It implements RankingRules (match_outcome and sort_key), the two questions its CareerSimulator asks."""

    def __init__(self, match_rules: Sequence[MatchRule] | None = None,
                 planners: Sequence[RoadmapPlanner] | None = None,
                 ranking_keys: Sequence[Callable[[dict], float]] | None = None) -> None:
        # Aggregation: the match rules can be handed in (a test, or a team adding a rule); None means the standard
        # three. They hold no state of this manager, so the same rule objects can be shared
        self.__match_rules = tuple(match_rules) if match_rules is not None else self.__standard_match_rules()
        # The ranking keys: each takes a job's fit (see job_fit) and returns its sort value, smaller first. A later
        # key only breaks ties in the earlier ones. Passing a different list adds or reorders keys without
        # changing this class (OCP). None means the standard order from Phase 1.
        self.__ranking_keys = tuple(ranking_keys) if ranking_keys is not None else self.__standard_ranking_keys()
        # Composition: the guard and the simulator are parts of this manager, created with it and used only
        # through it. The planners are handed on to the simulator: they are its aggregation (None means its two
        # standard ones)
        self.__guard = TailoringGuard()
        self.__simulator = CareerSimulator(planners)

    @staticmethod
    def __standard_match_rules() -> tuple[MatchRule, ...]:
        """The requirements from Phase 1's multi-condition rule: experience, education and level."""
        return (ExperienceRule(), EducationRule(), SeniorityRule(FAR_ABOVE_LEVELS))

    @staticmethod
    def __standard_ranking_keys() -> tuple[Callable[[dict], float], ...]:
        """The ranking order: requirements met first (Phase 1), then a Strong match before a Fair one, then most key
        skills, share, level."""
        return (LogicManager.__rank_by_experience_gap, LogicManager.__rank_by_education,
                LogicManager.__rank_by_far_above_level, LogicManager.__rank_by_strong_match,
                LogicManager.__rank_by_skill_count, LogicManager.__rank_by_skill_share,
                LogicManager.__rank_by_level_gap)

    # ---------- how well a job fits, and the standard ranking keys ----------

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
    def __rank_by_experience_gap(fit: dict) -> float:
        """Jobs asking for fewer years of experience than the candidate lacks come first."""
        return fit["experience_gap"]

    @staticmethod
    def __rank_by_education(fit: dict) -> float:
        """Jobs whose minimum education the candidate meets come before those they don't."""
        return not fit["education_ok"]

    @staticmethod
    def __rank_by_far_above_level(fit: dict) -> float:
        """Jobs FAR_ABOVE_LEVELS or more above the candidate's level rank below nearer ones."""
        return fit["level_gap"] >= FAR_ABOVE_LEVELS

    @staticmethod
    def __rank_by_strong_match(fit: dict) -> float:
        """A Strong match (at least STRONG_MATCH_SHARE of the key skills) before a Fair one, so a job with a few
        more skills matched out of many never outranks one where the candidate has most of the skills asked."""
        return fit["share"] < STRONG_MATCH_SHARE

    @staticmethod
    def __rank_by_skill_count(fit: dict) -> float:
        """More matched key skills first."""
        return -fit["matched"]

    @staticmethod
    def __rank_by_skill_share(fit: dict) -> float:
        """A higher share of the job's key skills first (breaks ties in the number of matched skills)."""
        return -fit["share"]

    @staticmethod
    def __rank_by_level_gap(fit: dict) -> float:
        """A job nearer the candidate's level first (the last tie-breaker)."""
        return fit["level_gap"]

    # ---------- the what-if simulator (the CareerSimulator part) ----------

    @property
    def planners(self) -> list[tuple[str, str]]:
        """(key, label) of each roadmap planner, for a form or menu."""
        return self.__simulator.planners

    def learnable_skills(self, jobs: Sequence[dict]) -> list[str]:
        """The skills most of these jobs ask for that the candidate lacks, most-wanted first."""
        return self.__simulator.learnable_skills(jobs)

    def what_if(self, profile: dict, jobs: Sequence[dict], skills: Sequence[str], top_n: int = 5) -> dict:
        """How the jobs would rank if the candidate had these skills. ValueError, worded for the user, if the
        skills or jobs can't be simulated."""
        # This manager hands itself in as the RankingRules, so the simulator ranks by the search's own rules
        return self.__simulator.what_if(self, profile, jobs, skills, top_n)

    def roadmap(self, profile: dict, jobs: Sequence[dict], planner_key: str, steps: int = 3, top_n: int = 5,
                skills: Sequence[str] | None = None) -> dict:
        """Up to steps skills to learn in order, picked by the planner with this key. ValueError, worded for the
        user, if the planner, skills or jobs can't be used."""
        return self.__simulator.roadmap(self, profile, jobs, planner_key, steps, top_n, skills)

    # ---------- filters and search steps ----------

    @staticmethod
    def __is_job_link(url: str | None) -> bool:
        """True for an https MyCareersFuture job link. Checks the format only, without going online, so it is
        instant and cannot fail because of the network."""
        if not isinstance(url, str):
            return False
        try:
            parts = urlparse(url)
        except ValueError:
            return False
        host = parts.hostname or ""
        return parts.scheme == "https" and (host == "mycareersfuture.gov.sg" or host.endswith(".mycareersfuture.gov.sg"))

    def __filter_reason(self, job: dict, filters: dict) -> str:
        """Returns why a job is removed, e.g. "salary", or "" if it has a valid link and
        meets all of the user's filters (checked by IOManager's InputValidator)"""
        if not self.__is_job_link(job.get("job_url")):
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
        return self.__filter_reason(job, filters) == ""

    def count_removed(self, jobs: list[dict], filters: dict) -> dict[str, int]:
        """Counts how many jobs each filter removed, e.g. {"salary": 14, "job type": 6}.
        A job failing several filters is counted once, under the first one it fails."""
        counts = {}
        for job in jobs:
            reason = self.__filter_reason(job, filters)
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
        return tuple(key(fit) for key in self.__ranking_keys)

    def __rank_jobs(self, profile: dict, jobs: list[dict]) -> list[dict]:
        """All the jobs, best match first, in the order of the ranking rules."""
        return sorted(jobs, key=lambda job: self.sort_key(profile, job))

    def __select_top_jobs(self, profile: dict, jobs: list[dict], top_n: int) -> list[dict]:
        """The top_n jobs that best suit the candidate."""
        return self.__rank_jobs(profile, jobs)[:top_n]

    def filter_and_rank(self, profile: dict, jobs: list[dict], filters: dict, top_n: int = 5) -> list[dict]:
        """Entry point: drops jobs failing the filters or matching no skills, ranks the rest, adds each outcome."""
        kept = []
        for job in jobs:
            if self.passes_filters(job, filters) and job.get("matched_skills"):
                kept.append(job)
        ranked = []
        for job in self.__select_top_jobs(profile, kept, top_n):
            outcome, reason = self.match_outcome(profile, job)
            # A copy with the outcome added, so the caller's job is unchanged
            ranked.append({**job, "match_outcome": outcome, "outcome_reason": reason})
        return ranked

    def find_skill_gaps(self, jobs: list[dict]) -> list[tuple[str, int]]:
        """The skills missing from the most jobs, as [(skill, number of jobs)], most common first.
        Only skills missing from 2 or more jobs; "SQL" and "sql" count as one skill, and so do "UAT" and "User
        Acceptance Testing" (SkillName)"""
        skill_names = SkillName(skill for job in jobs for skill in job.get("missing_skills", []))
        counts = Counter()
        names = {}  # skill's comparison form -> how it was first written
        for job in jobs:
            job_skills = {}  # each skill counted once per job, kept in order (a set's order changes between runs)
            for skill in job.get("missing_skills", []):
                key = skill_names.key(skill)
                names.setdefault(key, skill.strip())
                job_skills[key] = True
            counts.update(job_skills.keys())  # equal counts stay in the order first seen, so every run agrees
        return [(names[key], count) for key, count in counts.most_common(SKILL_GAP_LIMIT) if count >= 2]

    # ---------- checking the AI's answers ----------

    @staticmethod
    def __is_learnable_skill(name: str) -> bool:
        """Whether a tag names something a candidate could train for, rather than a way of behaving at work or
        an activity with nothing behind it to learn. "Power BI", "HPLC" and "GMP" are skills;
        "Work in A Fast Paced Environment", "Team Player" and "Reporting" are not."""
        words = name.strip().lower().replace("-", " ").split()
        while len(words) > 1 and words[0] in PRAISE_WORDS:  # "Strong Attention to Detail" -> "attention to detail"
            words = words[1:]
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
    def __ground_skill_matches(requirements: dict, candidate_skills: list[str], listed_skills: list[str]) -> dict:
        """Keeps only skill matches that point at a real candidate skill and a real required skill, then derives
        matched_skills and missing_skills from them. "C++ <- Rust" is thrown away if the candidate has no Rust.
        Tags that are not skills at all are dropped first, so they neither count towards the job's skill share
        nor turn up as something to learn. Only the first MAX_KEY_SKILLS (the most important) are kept, and a job
        left with fewer than MIN_KEY_SKILLS is topped up from the employer's own skill tags on the posting
        (listed_skills, the same test applied). Names are compared with SkillName, so "UAT" and "User Acceptance
        Testing" are one skill: a job listing both keeps the first, and a resume skill by the same name as a job
        skill always covers it, whether or not the AI paired them."""
        names = SkillName([*candidate_skills, *requirements["required_skills"], *listed_skills,
                           *(match["job_skill"] for match in requirements["skill_matches"]),
                           *(match["candidate_skill"] for match in requirements["skill_matches"])])
        known_candidate_skills = {names.key(skill): skill for skill in candidate_skills}
        required_lookup: dict[str, str] = {}
        for skill in requirements["required_skills"]:
            if LogicManager.__is_learnable_skill(skill) and names.key(skill) not in required_lookup:
                required_lookup[names.key(skill)] = skill.strip()
        required_skills = list(required_lookup.values())[:MAX_KEY_SKILLS]
        required_lookup = {names.key(skill): skill for skill in required_skills}
        for tag in listed_skills:
            if len(required_skills) >= MIN_KEY_SKILLS:
                break
            if names.key(tag) not in required_lookup and LogicManager.__is_learnable_skill(tag):
                required_skills.append(tag.strip())
                required_lookup[names.key(tag)] = tag.strip()
        requirements = {**requirements, "required_skills": required_skills}

        candidate_skill_for_job_skill = {}
        for match in requirements["skill_matches"]:
            job_skill = required_lookup.get(names.key(match["job_skill"]))
            if job_skill and names.key(match["candidate_skill"]) in known_candidate_skills:
                # first match per skill, named as the resume names it
                candidate_skill_for_job_skill.setdefault(job_skill, known_candidate_skills[names.key(match["candidate_skill"])])
        for skill in required_skills:  # the same skill by name (also every tag added, which the AI never compared)
            if names.key(skill) in known_candidate_skills:
                candidate_skill_for_job_skill.setdefault(skill, known_candidate_skills[names.key(skill)])
        # in the order of the job's skills, so the most important match comes first
        candidate_skill_for_job_skill = {skill: candidate_skill_for_job_skill[skill] for skill in required_skills
                                         if skill in candidate_skill_for_job_skill}

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
            records.append({**portal_fields, **self.__ground_skill_matches(answer, profile["core_skills"],
                                                                           listing.get("listed_skills", []))})
        return records

    # ---------- tailored resumes (the TailoringGuard part) ----------
    # LogicManager is the one "domain rules" contract the web app uses. These checks live in its TailoringGuard part
    # (composition), and each method below passes to it, so callers never reach into the part and the guard can
    # change without touching them.

    def resume_parts(self, resume: dict) -> list[str]:
        """Each piece of text in a tailored resume."""
        return self.__guard.resume_parts(resume)

    def job_keywords(self, job: dict, resume_text: str) -> tuple[list[str], list[str]]:
        """Splits a job's skills into (ones the resume shows, ones it doesn't), for tailoring a Word resume."""
        return self.__guard.job_keywords(job, resume_text)

    def ground_tailored_resume(self, tailored: dict, resume_text: str, avoid_keywords: list[str],
                               use_keywords: list[str] | None = None) -> tuple[dict, list[str], list[dict]]:
        """The tailored resume without anything the original doesn't show, or shows only for other work:
        (resume, skills removed, notes)."""
        return self.__guard.ground_tailored_resume(tailored, resume_text, avoid_keywords, use_keywords)

    def ground_docx_edits(self, edits: list[dict], paragraphs: list[dict], resume_text: str, avoid_keywords: list[str],
                          use_keywords: list[str] | None = None
                          ) -> tuple[dict[int, str], dict[int, str], list[str], list[dict]]:
        """The Word edits that may be made: only those adding nothing new (nor moving a tool to other work, nor
        raising a skill level), never leaving a heading with no points, and keeping the number of pages.
        Returns (edits, reasons, skills removed, notes)."""
        kept, reasons, removed_skills, notes = self.__guard.ground_docx_edits(edits, paragraphs, resume_text,
                                                                               avoid_keywords, use_keywords)
        kept, group_notes = self.__guard.keep_bullet_groups(kept, paragraphs)
        kept, page_notes = self.__guard.fit_edits_to_page_count(kept, paragraphs)
        return kept, reasons, removed_skills, notes + group_notes + page_notes

    def resume_view(self, job: dict, resume_name: str) -> tuple[dict, bool]:
        """The job as one resume sees it, and whether that resume is the one that found it."""
        return self.__guard.resume_view(job, resume_name)

    def tailoring_skills(self, job: dict, same_resume: bool, avoid_keywords: list[str], tailored: dict) -> dict:
        """The skills a tailored page shows: {"matched": the card's matched skills that apply to this resume,
        "not_added": the ones the job wants that the resume doesn't show, "shown": which matched skills the
        tailored resume still shows, with a quote}. tailored is the tailor's result."""
        skills = self.__guard.tailoring_skills(job, same_resume, avoid_keywords, tailored["removed_skills"])
        shown = self.__guard.skills_shown(skills["matched"], tailored["skill_evidence"], tailored["parts"])
        return {**skills, "shown": shown}
