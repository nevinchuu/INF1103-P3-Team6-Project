// Search page: checks the form before it is sent and remembers the last choices.
// Sending the form and the progress card are handled by progress.js (loaded first).

const form = document.getElementById("task-form");
const resumeChoice = document.getElementById("resume_choice");
const resumeFile = document.getElementById("resume_file");
const resumeNote = document.getElementById("resume-note");

const MAX_UPLOAD = Number(form.dataset.maxUpload);
const ALLOWED_EXTENSIONS = [".pdf", ".docx"];
const SAVED_FIELDS = ["resume_choice", "min_salary", "job_type", "work_arrangement",
                      "max_years_experience", "fallback_qualification", "fallback_skills"];
const STORAGE_KEY = "jobMatcher.lastSearch";

// ---------- Checks before sending ----------

// Which resume will be used, and whether the picked file is too big or the wrong type
resumeFile.addEventListener("change", () => {
  const file = resumeFile.files[0];
  resumeFile.setCustomValidity("");
  resumeChoice.disabled = Boolean(file);
  resumeNote.hidden = !file;
  if (!file) return;

  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(extension)) {
    resumeFile.setCustomValidity("Please choose a PDF or Word (.docx) file.");
  } else if (file.size > MAX_UPLOAD) {
    resumeFile.setCustomValidity(`That file is too large. Please choose one under ${MAX_UPLOAD / (1024 * 1024)} MB.`);
  }
  resumeFile.reportValidity();

  resumeNote.textContent = `Using "${file.name}". If you've uploaded this exact file before, we'll reuse it.`;
});

// ---------- Remembering the last search's choices (this browser only) ----------

function rememberFields() {
  const values = {};
  for (const name of SAVED_FIELDS) values[name] = form.elements[name].value;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
  } catch {
    // Storage may be blocked (e.g. private browsing); the form still works
  }
}

function restoreFields() {
  let values;
  try {
    values = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return;
  }
  for (const name of SAVED_FIELDS) {
    const field = form.elements[name];
    const value = values[name];
    if (typeof value !== "string") continue;
    // A select only takes a value it still offers (e.g. a resume that was deleted is skipped)
    if (field.tagName === "SELECT" && ![...field.options].some((option) => option.value === value)) continue;
    field.value = value;
  }
}

restoreFields();
setupTaskForm({ resultUrl: (id) => `/results/${id}`, onStarted: rememberFields });
