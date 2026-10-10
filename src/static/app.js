// Search page: checks the form before it is sent.
// Sending the form and the progress card are handled by progress.js (loaded first).

const form = document.getElementById("task-form");
const resumeChoice = document.getElementById("resume_choice");
const resumeFile = document.getElementById("resume_file");
const resumeNote = document.getElementById("resume-note");
const minSalary = document.getElementById("min_salary");
const maxSalary = document.getElementById("max_salary");

const MAX_UPLOAD = Number(form.dataset.maxUpload);
const ALLOWED_EXTENSIONS = [".pdf", ".docx"];

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

  const taken = [...resumeChoice.options].some((option) => option.value === file.name);
  resumeNote.textContent = `Using the uploaded file "${file.name}" instead of a saved resume.` +
    (taken ? " It will be saved as a new copy, so your existing file with that name is kept." : "");
});

function checkSalaryRange() {
  const low = Number(minSalary.value || 0);
  const high = maxSalary.value === "" ? Infinity : Number(maxSalary.value);
  maxSalary.setCustomValidity(high < low ? "Maximum salary cannot be lower than minimum salary." : "");
}
minSalary.addEventListener("input", checkSalaryRange);
maxSalary.addEventListener("input", checkSalaryRange);

setupTaskForm({ resultUrl: (id) => `/results/${id}` });
