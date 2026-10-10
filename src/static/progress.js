// Shared by the search page (app.js) and the tailor page (tailor.html): sends the page's form,
// then shows the progress card (templates/_progress.html) until the server's background task is done.
// The server runs the task in a thread; this script asks for its progress every second
// (GET /progress/<id>) and opens resultUrl(id) when it is finished.
// The task id is kept in the address (?search=<id>), so a refresh picks the task up again.
//
// Page needs: a <form id="task-form"> with a submit button #task-button, a #form-error alert,
// and the progress card. Call setupTaskForm({ resultUrl, onStarted }) once.

const POLL_MS = 1000;

// The server's JSON reply, or {} if it sent something else (e.g. an HTML error page)
async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function setupTaskForm({ resultUrl, onStarted = () => {} }) {
  const form = document.getElementById("task-form");
  const button = document.getElementById("task-button");
  const buttonText = button.textContent;
  const cancelButton = document.getElementById("cancel-button");
  const formError = document.getElementById("form-error");
  const panel = document.getElementById("progress-panel");
  const bar = document.getElementById("progress-bar");
  const percentText = document.getElementById("progress-percent");
  const message = document.getElementById("progress-message");
  const steps = [...panel.querySelectorAll(".steps li")];

  let activeTask = null;  // id of the task being polled; null stops polling

  function removeTaskFromAddress() {
    const params = new URLSearchParams(location.search);
    params.delete("search");
    const query = params.toString();
    history.replaceState(null, "", location.pathname + (query ? `?${query}` : ""));
  }

  function showForm(text, kind = "error") {
    activeTask = null;
    removeTaskFromAddress();
    formError.textContent = text;
    formError.className = `alert alert-${kind}`;
    formError.hidden = !text;
    form.hidden = false;
    panel.hidden = true;
    button.disabled = false;
    button.textContent = buttonText;
  }

  function showProgress(taskId) {
    activeTask = taskId;
    const params = new URLSearchParams(location.search);
    params.set("search", taskId);
    history.replaceState(null, "", `?${params}`);
    form.hidden = true;
    panel.hidden = false;
    cancelButton.disabled = false;
    setProgress(0, "Starting...");
    window.scrollTo({ top: 0, behavior: "smooth" });
    poll(taskId);
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

  async function poll(taskId) {
    if (taskId !== activeTask) return;  // cancelled, or a newer task started
    try {
      const response = await fetch(`/progress/${taskId}`);
      const data = await readJson(response);
      if (taskId !== activeTask) return;
      if (!response.ok || data.status === "error") {
        showForm(data.error || "Something went wrong. Please try again.");
        return;
      }
      if (data.status === "cancelled") {
        showForm("Cancelled.", "info");
        return;
      }
      setProgress(data.percent, data.message);
      if (data.status === "done") {
        window.location.href = resultUrl(taskId);
        return;
      }
    } catch {
      // A missed check (e.g. a brief network blip) is fine; try again next time
    }
    setTimeout(() => poll(taskId), POLL_MS);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.hidden = true;
    button.disabled = true;
    button.textContent = "Starting...";

    try {
      const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
      const data = await readJson(response);
      if (!response.ok || !data.search_id) {
        showForm(data.error || "Could not start. Please try again.");
        return;
      }
      onStarted();
      showProgress(data.search_id);
    } catch {
      showForm("Could not reach the server. Is web_app.py still running?");
    }
  });

  cancelButton.addEventListener("click", async () => {
    const taskId = activeTask;
    cancelButton.disabled = true;
    showForm("Cancelled.", "info");
    try {
      await fetch(`/search/${taskId}/cancel`, { method: "POST" });
    } catch {
      // The server may be gone; the form is back either way
    }
  });

  // Pick up a task that was running when the page was refreshed
  const runningTask = new URLSearchParams(location.search).get("search");
  if (runningTask) showProgress(runningTask);
}
