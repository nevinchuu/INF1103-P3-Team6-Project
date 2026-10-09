"""Compares AI models on the same resume using the ai_manager pipeline.

Each model runs the full ai_manager flow (extract profile -> search jobs ->
extract job requirements -> rank -> format), and its top 2 jobs are shown
side by side with the time taken. Results are exported to
src/ai_manager_test/results/ as JSON (full detail) and Markdown (readable summary).
Edit MODELS_TO_TEST to change which models are compared.

    python src/ai_manager_test/compare_models.py
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

# ai_manager_test must be imported first: it adds src/ to the import path,
# which "import ai_manager" needs (ai_manager.py is one folder up, in src/)
import ai_manager_test
import ai_manager  # noqa: E402
import io_manager  # noqa: E402  (all output goes through the input/output layer)

# (provider, model) pairs; providers are the keys of ai_manager.PROVIDERS.
# Exact model IDs (not "-latest" aliases) so each run is repeatable.
MODELS_TO_TEST = [
    ("openai", "gpt-5.5"),
    ("openai", "gpt-5.4-mini"),
    ("openai", "gpt-5.4-nano"),
    ("anthropic", "claude-opus-5-5"),
    ("anthropic", "claude-sonnet-5-5"),
    ("anthropic", "claude-haiku-4-5"),
    ("gemini", "gemini-3.1-pro-preview"),
    ("gemini", "gemini-3.8-flash"),
    ("gemini", "gemini-3.1-flash-lite"),
]

TOP_N = 2
RESULTS_DIR = Path(__file__).resolve().parent / "results"  # src/ai_manager_test/results

# Models that search the same job title get the same listings, so differences
# between them come from the model rather than from the job portal changing mid-run.
_fetch_cache = {}
_original_fetch_jobs = ai_manager.fetch_jobs


def _cached_fetch_jobs(search_query: str, limit: int = 10) -> list[dict]:
    key = (search_query.strip().lower(), limit)
    if key not in _fetch_cache:
        jobs = _original_fetch_jobs(search_query, limit)
        if not jobs:
            return jobs  # don't cache failures
        _fetch_cache[key] = jobs
    return _fetch_cache[key]


ai_manager.fetch_jobs = _cached_fetch_jobs


def run_model(resume_text: str, provider: str, model: str) -> dict:
    """Runs the ai_manager pipeline with one model and returns its result entry."""
    ai_manager.set_provider(provider, model)
    start = time.perf_counter()
    profile = ai_manager.extract_candidate_profile(resume_text)
    jobs = ai_manager.search_and_extract_jobs(profile) if profile is not None else []
    entry = {"provider": provider, "model": model, "seconds": round(time.perf_counter() - start, 1)}
    if profile is None:
        entry["error"] = "Could not extract candidate profile; see log."
        return entry

    entry["jobs_evaluated"] = len(jobs)
    entry["profile"] = {
        k: profile[k]
        for k in ("highest_qualification", "years_of_experience", "internship_months",
                  "seniority_level", "search_keywords")
    }
    entry["output"] = ai_manager_test.format_output(profile, ai_manager_test.select_top_jobs(profile, jobs, top_n=TOP_N))
    return entry


def build_markdown(report: dict) -> str:
    """Builds a readable Markdown summary of the comparison."""
    lines = [
        "# Model Comparison",
        "",
        f"- **Run at:** {report['run_at']}",
        f"- **Resume:** {report['resume_file']}",
        "",
        "| Model | Time | Jobs | Qualification | Exp | Seniority |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["models"]:
        label = f"{r['provider']}/{r['model']}"
        if "error" in r:
            lines.append(f"| {label} | {r['seconds']}s | FAILED | | | |")
            continue
        p = r["profile"]
        lines.append(
            f"| {label} | {r['seconds']}s | {r['jobs_evaluated']} | {p['highest_qualification']} | "
            f"{p['years_of_experience']} | {p['seniority_level']} |"
        )

    lines += ["", f"## Top {TOP_N} jobs per model", ""]
    for r in report["models"]:
        lines.append(f"### {r['provider']}/{r['model']}")
        lines.append("")
        if "error" in r:
            lines += [f"Failed: {r['error']}", ""]
            continue
        lines.append(f"Search keywords: {', '.join(r['profile']['search_keywords'])}")
        lines.append("")
        for i, job in enumerate(r["output"]["job_listings"], 1):
            pay = job["pay_range"]
            lines.append(f"{i}. **[{job['job_title']}]({job['job_url']})**, {job['company']} "
                         f"(S${pay['min']:,}-{pay['max']:,} {pay['period']}, {job['employment_type']})")
            lines.append(f"   {job['suitability_reason']}")
        lines.append("")
    return "\n".join(lines)


def export_results(report: dict) -> tuple[Path, Path]:
    """Writes the report to results/ as JSON and Markdown; returns both paths."""
    RESULTS_DIR.mkdir(exist_ok=True)
    stem = f"model_comparison_{datetime.now():%Y%m%d_%H%M%S}"
    json_path = RESULTS_DIR / f"{stem}.json"
    md_path = RESULTS_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(build_markdown(report), encoding="utf-8")
    return json_path, md_path


def print_summary(report: dict) -> None:
    """Prints a short side-by-side summary of each model's run."""
    io_manager.display_message("\n--- SUMMARY ---")
    for r in report["models"]:
        label = f"{r['provider']}/{r['model']}"
        if "error" in r:
            io_manager.display_message(f"{label:<34} FAILED after {r['seconds']}s: {r['error']}")
            continue
        p = r["profile"]
        io_manager.display_message(f"{label:<34} {r['seconds']:>6}s  {r['jobs_evaluated']:>2} jobs  "
                                   f"{p['highest_qualification']}, {p['years_of_experience']}y exp")
        for job in r["output"]["job_listings"]:
            io_manager.display_message(f"{'':<36}- {job['job_title'][:60]} ({job['company'][:30]})")


if __name__ == "__main__":
    try:
        resume_path = ai_manager_test.get_resume_file_path(ai_manager_test.get_data_root())
        resume_text = ai_manager_test.read_resume_file(resume_path)

        results = []
        for provider, model in MODELS_TO_TEST:
            logging.info(f"===== {provider}/{model} =====")
            results.append(run_model(resume_text, provider, model))

        report = {
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "resume_file": Path(resume_path).name,
            "models": results,
        }

        print_summary(report)
        json_path, md_path = export_results(report)
        io_manager.display_message(f"\nResults exported to:\n  {json_path}\n  {md_path}")
    except KeyboardInterrupt:
        io_manager.display_message("\nOperation cancelled by user.")
