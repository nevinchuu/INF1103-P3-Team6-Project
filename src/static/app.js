// Search page: sends the form, then shows a progress card until the search is done.
// The server runs the search in the background; this script asks for its
// progress every second (GET /progress/<id>) and opens the results when finished.

const form = document.getElementById("search-form");
const button = document.getElementById("search-button");
const formError = document.getElementById("form-error");
const panel = document.getElementById("progress-panel");
const bar = document.getElementById("progress-bar");
const percentText = document.getElementById("progress-percent");
const message = document.getElementById("progress-message");
const steps = [...document.querySelectorAll(".steps li")];

const POLL_MS = 1000;

function showError(text) {
  formError.textContent = text;
  formError.hidden = false;
  form.hidden = false;
  panel.hidden = true;
  button.disabled = false;
  button.textContent = "Find matching jobs";
}

function setProgress(percent, text) {
  bar.style.width = percent + "%";
  bar.parentElement.setAttribute("aria-valuenow", percent);
  percentText.textContent = percent + "%";
  message.textContent = text;

  // A step is done once the next one has started, and active while it is the latest started
  steps.forEach((step, i) => {
    const started = percent >= Number(step.dataset.from);
    const nextStarted = i + 1 < steps.length ? percent >= Number(steps[i + 1].dataset.from) : percent >= 100;
    step.classList.toggle("done", nextStarted);
    step.classList.toggle("active", started && !nextStarted);
  });
}

async function poll(searchId) {
  try {
    const response = await fetch(`/progress/${searchId}`);
    const data = await response.json();
    if (!response.ok || data.status === "error") {
      showError(data.error || "The search failed. Please try again.");
      return;
    }
    setProgress(data.percent, data.message);
    if (data.status === "done") {
      window.location.href = `/results/${searchId}`;
      return;
    }
  } catch {
    // A missed check (e.g. a brief network blip) is fine; try again next time
  }
  setTimeout(() => poll(searchId), POLL_MS);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  button.disabled = true;
  button.textContent = "Starting...";

  try {
    const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) {
      showError(data.error || "Could not start the search.");
      return;
    }
    form.hidden = true;
    panel.hidden = false;
    setProgress(0, "Starting...");
    window.scrollTo({ top: 0, behavior: "smooth" });
    poll(data.search_id);
  } catch {
    showError("Could not reach the server. Is web_app.py still running?");
  }
});
