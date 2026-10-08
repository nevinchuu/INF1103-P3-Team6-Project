"""Compares AI models on the same resume using the ai_manager pipeline.

Each model runs the full ai_manager flow (extract profile -> search jobs ->
extract job requirements -> rank -> format), and its top 2 jobs are shown
side by side with time, token usage and cost. Results are exported to
results/ as JSON (full detail) and Markdown (readable summary).
Edit MODELS_TO_TEST and PRICES to change which models are compared.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import ai_manager
import ai_manager_test

# (provider, model) pairs; providers are the keys of ai_manager.PROVIDERS.
# Exact model IDs (not "-latest" aliases) so each run maps to a known price.
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

# USD per 1M tokens (input, output), standard tier, short context. Output prices
# apply to thinking/reasoning tokens too. Checked October 2026 against:
#   OpenAI:    https://developers.openai.com/api/docs/pricing
#   Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
#   Gemini:    https://ai.google.dev/gemini-api/docs/pricing
#              (3.8 Flash price is the introductory rate through 31 Dec 2026)
PRICES = {
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}

TOP_N = 2
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

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


def calculate_cost(model: str, usage: dict) -> float | None:
    """Returns the USD cost of the given token usage, or None if the model has no price."""
    if model not in PRICES:
        return None
    input_price, output_price = PRICES[model]
    return (usage["input_tokens"] * input_price + usage["output_tokens"] * output_price) / 1_000_000


def run_model(resume_text: str, provider: str, model: str) -> dict:
    """Runs the ai_manager pipeline with one model and returns its result entry."""
    ai_manager.set_provider(provider, model)
    ai_manager.reset_usage()
    start = time.perf_counter()
    profile, jobs = ai_manager.run_ai_pipeline(resume_text)
    seconds = round(time.perf_counter() - start, 1)

    usage = ai_manager.get_usage()
    cost = calculate_cost(model, usage)
    entry = {
        "provider": provider,
        "model": model,
        "seconds": seconds,
        "usage": usage,
        "cost_usd": round(cost, 4) if cost is not None else None,
    }
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


def _format_cost(cost: float | None) -> str:
    return f"${cost:.4f}" if cost is not None else "n/a"


def build_markdown(report: dict) -> str:
    """Builds a readable Markdown summary of the comparison."""
    lines = [
        "# Model Comparison",
        "",
        f"- **Run at:** {report['run_at']}",
        f"- **Resume:** {report['resume_file']}",
        f"- **Total cost:** {_format_cost(report['total_cost_usd'])}",
        "",
        "| Model | Time | Jobs | Requests | Input tokens | Output tokens | Cost | Qualification | Exp | Seniority |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["models"]:
        u = r["usage"]
        label = f"{r['provider']}/{r['model']}"
        if "error" in r:
            lines.append(f"| {label} | {r['seconds']}s | FAILED | {u['requests']} | {u['input_tokens']:,} | "
                         f"{u['output_tokens']:,} | {_format_cost(r['cost_usd'])} | | | |")
            continue
        p = r["profile"]
        lines.append(
            f"| {label} | {r['seconds']}s | {r['jobs_evaluated']} | {u['requests']} | {u['input_tokens']:,} | "
            f"{u['output_tokens']:,} | {_format_cost(r['cost_usd'])} | {p['highest_qualification']} | "
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
    print("\n--- SUMMARY ---")
    for r in report["models"]:
        label = f"{r['provider']}/{r['model']}"
        if "error" in r:
            print(f"{label:<34} FAILED after {r['seconds']}s ({_format_cost(r['cost_usd'])}): {r['error']}")
            continue
        p = r["profile"]
        print(f"{label:<34} {r['seconds']:>6}s  {r['jobs_evaluated']:>2} jobs  {_format_cost(r['cost_usd']):>9}  "
              f"{p['highest_qualification']}, {p['years_of_experience']}y exp")
        for job in r["output"]["job_listings"]:
            print(f"{'':<36}- {job['job_title'][:60]} ({job['company'][:30]})")
    print(f"\nTotal cost: {_format_cost(report['total_cost_usd'])}")


if __name__ == "__main__":
    try:
        resume_path = ai_manager_test.get_resume_file_path(ai_manager_test.get_data_root())
        resume_text = ai_manager_test.read_resume_file(resume_path)

        results = []
        for provider, model in MODELS_TO_TEST:
            logging.info(f"===== {provider}/{model} =====")
            results.append(run_model(resume_text, provider, model))

        costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
        report = {
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "resume_file": Path(resume_path).name,
            "total_cost_usd": round(sum(costs), 4),
            "models": results,
        }

        print_summary(report)
        json_path, md_path = export_results(report)
        print(f"\nResults exported to:\n  {json_path}\n  {md_path}")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
