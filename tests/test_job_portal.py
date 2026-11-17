"""Tests for JobPortal: its multi-step search, and its handling of whatever MyCareersFuture sends back (through a
stub in place of the network)."""

import pytest
import requests
from fakes import FakePortal, StubHttp

from data_layer.job_portal import DETAIL_ATTEMPTS, JobPortal, PORTAL_TELECOMMUTING

GOOD_URL = "https://www.mycareersfuture.gov.sg/job/it/data-analyst-acme-5365191b6ddd018b70f7b8149c0a20ca"
LISTING = {"title": "Data Analyst", "metadata": {"jobDetailsUrl": GOOD_URL}}


def portal(post=None, get=None) -> tuple[JobPortal, StubHttp]:
    http = StubHttp(post=post, get=get)
    return JobPortal(http=http, sleep=lambda seconds: None), http


def test_a_broken_listing_is_skipped_and_a_good_one_kept():
    mcf, _ = portal(post=[{"results": [LISTING, {"title": "Broken", "employmentTypes": "not a list"}]}])
    assert [job["title"] for job in mcf.search("Data Analyst")] == ["Data Analyst"]


# The posting date (for sorting the results newest first) comes from the listing's metadata
def test_a_listing_keeps_its_posting_date():
    dated = {**LISTING, "metadata": {**LISTING["metadata"], "newPostingDate": "2026-11-12"}}
    mcf, _ = portal(post=[{"results": [dated, LISTING]}])
    assert [job["posted_date"] for job in mcf.search("Data Analyst")] == ["2026-11-12", ""]


def test_a_portal_that_keeps_failing_gives_none_after_three_tries():
    mcf, http = portal(post=[({}, 500)])
    assert mcf.search("Data Analyst") is None
    assert len(http.calls) == 3


def test_a_timeout_is_retried_and_can_then_succeed():
    mcf, http = portal(post=[requests.Timeout("slow"), {"results": [LISTING]}])
    assert len(mcf.search("Data Analyst")) == 1 and len(http.calls) == 2


@pytest.mark.parametrize("reply", [None, [1, 2], {"results": "no"}, {"other": 1}])
def test_a_reply_in_an_unexpected_format_is_a_failure_not_a_crash(reply):
    mcf, _ = portal(post=[reply])
    assert mcf.search("Data Analyst") is None


def test_no_listings_is_an_empty_list_not_a_failure():
    mcf, _ = portal(post=[{"results": []}])
    assert mcf.search("Data Analyst") == []


def test_the_search_words_and_filters_go_in_the_request_body():
    mcf, http = portal(post=[{"results": []}])
    mcf.search("Data Analyst", limit=7, filters={"salary": 3000})
    _, _, options = http.calls[0]
    assert options["json"] == {"search": "Data Analyst", "salary": 3000} and options["params"] == {"limit": 7}


@pytest.mark.parametrize("payload", [{"description": "x", "skills": ["SQL"]}, [1, 2], {"description": "x", "skills": None}])
def test_job_details_in_an_unexpected_format_are_treated_like_no_reply(payload):
    mcf, _ = portal(get=[payload])
    assert mcf.details(GOOD_URL) is None


def test_job_details_are_cleaned_and_a_closed_job_is_noticed():
    mcf, _ = portal(get=[{"description": "<p>Use SQL</p>", "skills": [{"skill": "SQL"}]}])
    assert mcf.details(GOOD_URL) == {"description": "Use SQL", "listed_skills": ["SQL"], "is_open": True,
                                     "min_years_experience": None}
    # The years of experience come from the posting (the search results leave them out)
    mcf, _ = portal(get=[{"description": "x", "skills": [], "minimumYearsExperience": 2}])
    assert mcf.details(GOOD_URL)["min_years_experience"] == 2
    mcf, _ = portal(get=[{"description": "x", "skills": [], "status": {"jobStatus": "Closed"}}])
    assert mcf.details(GOOD_URL)["is_open"] is False


# MyCareersFuture marks a job posted again as "Re-open": it is live, so it must not be shown as closed. A job is
# closed only by a closed status or an expiry date that has passed
@pytest.mark.parametrize("status, expiry, is_open", [
    ("Open", "2999-12-31", True),
    ("Re-open", "2999-12-31", True),
    ("Re-open", "", True),
    (None, None, True),
    ("Closed", "2999-12-31", False),
    ("Expired", "", False),
    ("Open", "2000-01-01", False),
])
def test_a_job_is_closed_only_by_a_closed_status_or_a_past_expiry_date(status, expiry, is_open):
    posting = {"description": "x", "skills": [], "status": {"id": 103, "jobStatus": status},
               "metadata": {"expiryDate": expiry}}
    mcf, _ = portal(get=[posting])
    assert mcf.details(GOOD_URL)["is_open"] is is_open


# The portal answers 429 when many postings are read at once. A posting not read leaves the AI only the employer's
# skill tags (one search read none of 40), so a busy reply is waited out, as long as the portal asks, and retried
def test_a_busy_portal_is_waited_out_and_the_posting_read():
    http = StubHttp(get=[({}, 429, {"Retry-After": "3"}), ({}, 503), {"description": "Python and SQL", "skills": []}])
    waits = []
    mcf = JobPortal(http=http, sleep=waits.append)
    assert mcf.details(GOOD_URL)["description"] == "Python and SQL"
    assert waits == [3.0, 2.0] and len(http.calls) == 3  # Retry-After, then 1, 2, 4... seconds


def test_a_portal_that_stays_busy_gives_up_and_a_missing_job_is_not_retried():
    http = StubHttp(get=[({}, 429)])  # every reply is 429
    mcf = JobPortal(http=http, sleep=lambda seconds: None)
    assert mcf.details(GOOD_URL) is None and len(http.calls) == DETAIL_ATTEMPTS
    http = StubHttp(get=[({}, 404)])  # the job was taken down: asking again won't help
    mcf = JobPortal(http=http, sleep=lambda seconds: None)
    assert mcf.details(GOOD_URL) is None and len(http.calls) == 1


def test_details_need_a_real_job_link_and_survive_network_errors():
    mcf, http = portal(get=[requests.ConnectionError("down")])
    assert mcf.details("not a job link") is None and http.calls == []
    assert mcf.details(GOOD_URL) is None


def test_the_portals_filters_for_each_search_step():
    mcf, _ = portal()
    preferences = {"job_type": "Full Time", "min_salary": 3000, "work_arrangement": "Remote"}
    assert mcf.filters_for(preferences, "all") == {
        "employmentTypes": ["Full Time", "Permanent"], "salary": 3000,
        "flexibleWorkArrangements": [PORTAL_TELECOMMUTING]}
    assert mcf.filters_for(preferences, "job type") == {"employmentTypes": ["Full Time", "Permanent"]}
    assert mcf.filters_for(preferences, "general") == {}
    assert mcf.filters_for(None, "all") == {}


# The search for jobs at the candidate's levels: every step keeps the level filter
def test_position_levels_are_kept_at_every_search_step():
    mcf, _ = portal()
    levels = ["Fresh/entry level", "Junior Executive"]
    preferences = {"job_type": "Full Time", "min_salary": 3000, "position_levels": levels}
    assert mcf.filters_for(preferences, "all") == {
        "positionLevels": levels, "employmentTypes": ["Full Time", "Permanent"], "salary": 3000}
    assert mcf.filters_for(preferences, "job type") == {"positionLevels": levels,
                                                        "employmentTypes": ["Full Time", "Permanent"]}
    assert mcf.filters_for(preferences, "general") == {"positionLevels": levels}


# fetch_listings is written once in JobPortal, so it works the same on every portal (LSP)
def test_fetch_listings_removes_duplicates_and_reports_progress_on_any_portal():
    job = {"title": "A", "job_url": "https://x/1"}
    other = {"title": "B", "job_url": "https://x/2"}
    steps_seen = []
    summary = {}
    listings = FakePortal({"a": [job, other], "b": [job]}).fetch_listings(
        ["a", "b"], ["all"], {}, on_progress=lambda stage, done, total: steps_seen.append((stage, done, total)),
        summary=summary)
    assert [item["title"] for item in listings] == ["A", "B"]
    assert steps_seen == [("search", 1, 2), ("search", 2, 2)]
    assert summary["listings_found"] == 2 and summary["search_step"] == "all"


def test_fetch_listings_tries_fewer_preferences_when_nothing_fits():
    job = {"title": "A", "job_url": "https://x/1"}
    summary = {}
    listings = FakePortal([job]).fetch_listings(
        ["a"], ["all", "job type", "general"], {}, fits=lambda listing, step: step == "general", summary=summary)
    assert len(listings) == 1 and summary["search_step"] == "general"


def test_fetch_listings_stops_at_a_portal_failure_instead_of_widening_the_search():
    fake = FakePortal(None)
    summary = {}
    assert fake.fetch_listings(["a"], ["all", "general"], {}, summary=summary) == []
    assert summary["search_step"] == "all" and summary["searches_failed"] == 1 and len(fake.searches) == 1


def test_listings_without_a_link_are_dropped():
    assert FakePortal([{"title": "A", "job_url": "N/A"}]).fetch_listings(["a"], ["all"], {}) == []


# When MyCareersFuture can't be reached, the last listings of the same search are used, so a search still works
def test_the_last_listings_of_a_search_are_used_when_the_portal_is_down(tmp_path):
    path = str(tmp_path / "listings.json")
    online = JobPortal(http=StubHttp(post=[{"results": [LISTING]}]), sleep=lambda seconds: None, saved_listings_path=path)
    assert [job["title"] for job in online.search("Data Analyst")] == ["Data Analyst"]
    down = JobPortal(http=StubHttp(post=[requests.ConnectionError("offline")]), sleep=lambda seconds: None,
                     saved_listings_path=path)
    assert [job["title"] for job in down.search("data analyst ")] == ["Data Analyst"]  # same search
    assert down.search("Accountant") is None  # never searched before: still a failure
    assert down.search("Data Analyst", filters={"salary": 9000}) is None  # different filters


def test_a_corrupt_listings_file_is_ignored(tmp_path):
    path = tmp_path / "listings.json"
    path.write_text("[not json")
    down = JobPortal(http=StubHttp(post=[({}, 500)]), sleep=lambda seconds: None, saved_listings_path=str(path))
    assert down.search("Data Analyst") is None
    online = JobPortal(http=StubHttp(post=[{"results": [LISTING]}]), sleep=lambda seconds: None,
                       saved_listings_path=str(path))
    assert online.search("Data Analyst") and "Data Analyst" in path.read_text()  # replaced with a good file
