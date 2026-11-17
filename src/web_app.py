"""Web app (Flask): the pages and the search API, over the IOManager.

Run from the project root with python src/web_app.py, then open http://127.0.0.1:5000. Every route calls one or two
IOManager methods and renders the answer; the AI, logic and data managers are named only in from_environment, where
the app is wired together. The background searches and tailoring runs are polled through GET /progress/<id>.
To add a page, add a route and a template in src/templates/ that extends base.html.
"""

import io
import logging
import os
from urllib.parse import urlsplit

from flask import abort, Flask, jsonify, redirect, render_template, request, send_file, url_for
from flask.typing import ResponseReturnValue

from ai_layer.ai_manager import AIManager
from data_layer.data_manager import DataManager
from io_layer.io_manager import IOManager
from logic_layer.logic_manager import LogicManager


# ==========================================


# Network address the server listens on. 127.0.0.1 = this computer only. The Docker image sets
# WEB_HOST=0.0.0.0, because inside a container 127.0.0.1 can't be reached from outside it
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")


WEB_PORT = 5000


# The only names the app answers to. Any other Host header gets 400, so a website that points its own name at
# 127.0.0.1 ("DNS rebinding") can't read the app's pages as if it were the app itself
TRUSTED_HOSTS = ["127.0.0.1", "localhost"]


class WebApp:
    """The Flask app: a thin route layer that asks the IOManager for what each page shows."""

    def __init__(self, io: IOManager) -> None:
        # Association: the IO manager is created outside and handed in, and outlives any one request;
        # tests give it managers with fakes behind it
        self.__io = io
        # Composition: the Flask object is created by this app and belongs to it
        self.app = Flask(__name__)  # templates/ and static/ are found next to this file
        self.app.config["MAX_CONTENT_LENGTH"] = io.form_options()["max_upload"]
        self.app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS
        # Every value a page prints goes through this, so a whole number never shows as "1.0" or "0.0"
        self.app.jinja_env.finalize = self.__whole_number
        self.app.before_request(self.__same_origin_posts_only)
        self.__register_routes()
        self.__register_error_pages()

    @staticmethod
    def __whole_number(value: object) -> object:
        """A float with nothing after the point as an int (1.0 -> 1, 0.0 -> 0); everything else unchanged."""
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    @classmethod
    def from_environment(cls) -> "WebApp":
        """The app with its real managers: the AI provider and storage come from the environment variables."""
        ai, logic, data = AIManager.from_environment(), LogicManager(), DataManager.from_environment()
        return cls(IOManager(ai, logic, data))

    @classmethod
    def create_app(cls) -> Flask:
        """The Flask app with real managers (for `flask run`, a WSGI server, or a quick check in CI)."""
        return cls.from_environment().app

    # ==========================================
    # SET-UP
    # ==========================================

    def __register_routes(self) -> None:
        """Endpoint names are what the templates pass to url_for."""
        routes = [
            ("/", "index", self.index, ["GET"]),
            ("/results/<search_id>", "results", self.results, ["GET"]),
            ("/whatif/<search_id>", "whatif", self.whatif, ["GET", "POST"]),
            ("/saved", "saved", self.saved, ["GET"]),
            ("/saved/remove", "remove_saved", self.remove_saved, ["POST"]),
            ("/saved/update", "update_saved", self.update_saved, ["POST"]),
            ("/saved/clear", "clear_saved", self.clear_saved, ["POST"]),
            ("/tailor", "tailor", self.tailor, ["GET"]),
            ("/tailored/<search_id>", "tailored", self.tailored, ["GET"]),
            ("/tailored/<search_id>/download", "download_tailored", self.download_tailored, ["GET"]),
            ("/search", "start_search", self.start_search, ["POST"]),
            ("/results/<search_id>/update", "update_search", self.update_search, ["POST"]),
            ("/tailor", "start_tailor", self.start_tailor, ["POST"]),
            ("/progress/<search_id>", "progress", self.progress, ["GET"]),
            ("/search/<search_id>/cancel", "cancel_search", self.cancel_search, ["POST"]),
        ]
        for path, endpoint, view, methods in routes:
            self.app.add_url_rule(path, endpoint, view, methods=methods)

    def __register_error_pages(self) -> None:
        self.app.register_error_handler(404, self.not_found)
        self.app.register_error_handler(405, self.wrong_method)
        self.app.register_error_handler(500, self.server_error)
        self.app.register_error_handler(413, self.too_large)

    @staticmethod
    def __same_origin_posts_only() -> None:
        """Every form and fetch that changes something (POST) must come from the app's own pages. Another website
        open in the same browser could otherwise send them too (cross-site request forgery), e.g. clear the saved
        jobs or start searches on the API key. Browsers say where a request comes from in Sec-Fetch-Site and Origin;
        a request without them (a test client, curl) is not from another website, so it is let through."""
        if request.method != "POST":
            return
        site = request.headers.get("Sec-Fetch-Site")  # "none" = typed or bookmarked by the user
        origin = request.headers.get("Origin")
        if site not in (None, "same-origin", "none") or (origin and urlsplit(origin).netloc != request.host):
            logging.warning(f"Refused a cross-site {request.method} to {request.path} (from {origin or site})")
            abort(403)

    # ==========================================
    # HELPERS FOR THE ROUTES
    # ==========================================

    @staticmethod
    def __form_and_upload() -> tuple[dict, bytes]:
        """What the form sent: its answers, with the upload's file name added as resume_filename, and the file's
        bytes ("" and b"" if no file was picked)."""
        values = request.form.to_dict()
        upload = request.files.get("resume_file")
        if upload and upload.filename:
            values["resume_filename"] = upload.filename
            return values, upload.read()
        return values, b""

    def __task(self, task_id: str, kind: str) -> dict:
        """The progress of one task of this kind ("search" or "tailor"); 404 if there is none, so a tailoring run's id
        can't open a results page (or the other way round)."""
        progress = self.__io.search_progress(task_id)
        if progress["status"] == "missing" or progress["kind"] != kind:
            abort(404)
        return progress

    def __results_page(self, search_id: str, problem: str = "") -> str:
        """The results page for one finished search. problem: why the filters sent to "Update matches" weren't
        used, shown above the results."""
        return render_template("results.html", filter_problem=problem, **self.__io.search_results(search_id),
                               **self.__io.form_options())

    def __back_to_saved(self, outcome: str, open_url: str = "") -> ResponseReturnValue:
        """The Saved page again, keeping the filter the form was sent from, with an outcome code (the page words
        it) and the card to reopen (open=job link)."""
        keyword = request.form.get("q", "").strip()
        return redirect(url_for("saved", **({"q": keyword} if keyword else {}),
                                **{key: value for key, value in {"outcome": outcome, "open": open_url}.items() if value}))

    # ==========================================
    # PAGES
    # ==========================================

    def index(self) -> ResponseReturnValue:
        """Search form: pick or upload a resume and set job filters."""
        return render_template("index.html", resumes=self.__io.resume_choices(), **self.__io.form_options())

    def results(self, search_id: str) -> ResponseReturnValue:
        """The profile and top jobs from one finished search."""
        status = self.__task(search_id, "search")["status"]
        if status == "running":
            return redirect(url_for("index", search=search_id))  # the search page picks up its progress
        if status != "done":
            return redirect(url_for("index"))
        return self.__results_page(search_id)

    def whatif(self, search_id: str) -> ResponseReturnValue:
        """What-if page for one finished search: test skills the jobs ask for, or plan a roadmap (no AI involved)."""
        status = self.__task(search_id, "search")["status"]
        if status == "running":
            return redirect(url_for("index", search=search_id))  # the what-if needs the finished search
        if status != "done":
            abort(404)
        if request.method == "POST":
            values = {"mode": request.form.get("mode", "test"), "skills": request.form.getlist("skill"),
                      "extra_skills": request.form.get("extra_skills", ""),
                      **{key: request.form[key] for key in ("steps", "top_n") if key in request.form}}
            page = (self.__io.what_if if values["mode"] == "test" else self.__io.roadmap)(search_id, values)
        else:
            page = self.__io.what_if(search_id, {})
        return render_template("whatif.html", **page)

    def saved(self) -> ResponseReturnValue:
        """Every saved job, or those matching ?q= (saved.js also filters as you type)."""
        # The message comes as a short code (?outcome=), so a link someone crafted can't put its own words on the page
        page = self.__io.saved_jobs(request.args.get("q", ""), request.args.get("outcome", ""))
        return render_template("saved.html", open_url=request.args.get("open", ""), **page)

    def update_saved(self) -> ResponseReturnValue:
        """Sets the status and note of one saved job, found by its link."""
        outcome = self.__io.update_saved(request.form)
        reopen = request.form.get("job_url", "") if outcome in ("updated", "status", "note") else ""
        return self.__back_to_saved(outcome, reopen)

    def remove_saved(self) -> ResponseReturnValue:
        """Removes one saved job, found by its link (IDs change after every removal)."""
        return self.__back_to_saved(self.__io.remove_saved(request.form.get("job_url", "")))

    def clear_saved(self) -> ResponseReturnValue:
        """Removes every saved job."""
        return redirect(url_for("saved", outcome=self.__io.clear_saved()))

    def tailor(self) -> ResponseReturnValue:
        """Page to tailor a resume for one job (?job_url=...). The job comes from Saved jobs or a recent search."""
        form = self.__io.tailoring_form(request.args.get("job_url", ""))
        if form is None:
            abort(404)
        return render_template("tailor.html", from_search=request.args.get("from_search", ""), **form)

    def tailored(self, search_id: str) -> ResponseReturnValue:
        """A finished tailored resume: what changed, what was left out, and a preview."""
        progress = self.__task(search_id, "tailor")
        if progress["status"] in ("running", "error"):
            # The tailor page follows the run's progress, or shows why it failed
            return redirect(url_for("tailor", job_url=progress["job_url"], search=search_id))
        if progress["status"] != "done":
            return redirect(url_for("tailor", job_url=progress["job_url"]))
        page, _ = self.__io.tailored_resume(search_id)
        return render_template("tailored.html", **page)

    def download_tailored(self, search_id: str) -> ResponseReturnValue:
        """The tailored resume as a Word file, e.g. nevin_chua_Data_Analyst.docx."""
        page, docx_bytes = self.__io.tailored_resume(search_id)
        if not page:
            abort(404)
        return send_file(io.BytesIO(docx_bytes), as_attachment=True, download_name=page["download_name"],
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def not_found(self, error: Exception) -> ResponseReturnValue:
        """Friendly page for unknown links, e.g. results from before the server restarted."""
        return render_template("not_found.html"), 404

    def wrong_method(self, error: Exception) -> ResponseReturnValue:
        """The friendly not-found page for an address only a form can use, e.g. typing /search into the browser."""
        return render_template("not_found.html"), 405

    def server_error(self, error: Exception) -> ResponseReturnValue:
        """Friendly page for any error a page didn't handle, instead of Flask's plain one (the details go to the log)."""
        logging.error(f"Unexpected error on {request.path} "
                      f"({type(getattr(error, 'original_exception', error)).__name__})")
        return render_template("server_error.html"), 500

    def too_large(self, error: Exception) -> ResponseReturnValue:
        """Uploads over MAX_CONTENT_LENGTH. Sent as JSON because app.js sends the form."""
        megabytes = self.app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify(error=f"That file is too large. Please upload a resume under {megabytes} MB."), 413

    # ==========================================
    # SEARCH API (used by static/progress.js)
    # ==========================================

    def start_search(self) -> ResponseReturnValue:
        """Checks the form, then starts the search in a background thread. Returns {"search_id"}."""
        values, resume = self.__form_and_upload()
        values.pop("from_search", None)  # only "Update matches" reuses an earlier search
        try:
            return jsonify(search_id=self.__io.start_search(values, resume))
        except ValueError as error:  # the message is written for the user
            return jsonify(error=str(error)), 400

    def update_search(self, search_id: str) -> ResponseReturnValue:
        """"Update matches" on the results page: a new search with the new filters, reusing this search's
        profile so the resume isn't read again. Opens the search page, which shows the new search's progress."""
        if self.__task(search_id, "search")["status"] != "done":
            abort(404)
        try:
            new_id = self.__io.start_search({**request.form.to_dict(), "from_search": search_id})
        except ValueError as error:
            return self.__results_page(search_id, str(error))
        return redirect(url_for("index", search=new_id))

    def start_tailor(self) -> ResponseReturnValue:
        """Starts tailoring a resume for one job in a background thread. Returns {"search_id"};
        progress and cancelling use the same routes as a search."""
        values, resume = self.__form_and_upload()
        try:
            return jsonify(search_id=self.__io.start_tailoring(values.get("from_search", ""),
                                                               values.get("job_url", ""), values, resume))
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    def progress(self, search_id: str) -> ResponseReturnValue:
        """Progress of one search or tailoring run, for the progress bar."""
        progress = self.__io.search_progress(search_id)
        if progress["status"] == "missing":
            return jsonify(error=progress["error"]), 404
        return jsonify(status=progress["status"], percent=progress["percent"], message=progress["message"],
                       error=progress["error"])

    def cancel_search(self, search_id: str) -> ResponseReturnValue:
        """Stops a running search. The background thread stops at its next progress update."""
        self.__io.cancel_search(search_id)
        return jsonify(ok=True)

    def run(self) -> None:
        """Sets up logging, reports the saved jobs and starts the server."""
        self.__io.start_up(f"127.0.0.1:{WEB_PORT}")
        self.app.run(host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    WebApp.from_environment().run()
