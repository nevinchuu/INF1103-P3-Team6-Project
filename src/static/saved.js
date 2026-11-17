// Saved jobs page: filters and sorts the job cards in the browser, and asks before removing one job or all of them.
// Each card carries data-order (position in the saved file), data-status, data-salary, data-match and data-text.
// The tracker's stage buttons (Saved, Applied, ...) filter by status; a second click, or "Show all", clears it.

const list = document.getElementById("job-list");
const cards = [...list.children];
const filterInput = document.getElementById("job-filter");
const sortSelect = document.getElementById("job-sort");
const noMatch = document.getElementById("no-filter-match");
const clearForm = document.getElementById("clear-form");
const stageButtons = [...document.querySelectorAll(".tracker-button")];
const statusNote = document.getElementById("status-note");
const statusName = document.getElementById("status-note-name");

const SORT_KEY = "jobMatcher.savedSort";
const STATUS_KEY = "jobMatcher.savedStatus";

let statusFilter = "";  // the stage shown, or "" for every stage

// Compare functions for each sort option; later-saved jobs come first when tied
const byNewest = (a, b) => b.dataset.order - a.dataset.order;
const SORTS = {
  newest: byNewest,
  oldest: (a, b) => a.dataset.order - b.dataset.order,
  match: (a, b) => b.dataset.match - a.dataset.match || byNewest(a, b),
  salary: (a, b) => b.dataset.salary - a.dataset.salary || byNewest(a, b),
};

function applySort() {
  const compare = SORTS[sortSelect.value] || SORTS.newest;
  list.append(...[...cards].sort(compare));  // append moves the existing cards into the new order
  try {
    localStorage.setItem(SORT_KEY, sortSelect.value);
  } catch {
    // Storage may be blocked; sorting still works for this visit
  }
}

function applyFilter() {
  const words = filterInput.value.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  for (const card of cards) {
    const match = words.every((word) => card.dataset.text.includes(word)) &&
      (!statusFilter || card.dataset.status === statusFilter);
    card.hidden = !match;
    if (match) shown++;
  }
  noMatch.hidden = shown > 0;
}

// Shows only the jobs at one stage ("" shows every stage), presses its button and remembers it
function setStatus(status) {
  statusFilter = status;
  for (const button of stageButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.status === status));
  }
  statusNote.hidden = !status;
  statusName.textContent = status;
  try {
    localStorage.setItem(STATUS_KEY, status);
  } catch {
    // Storage may be blocked; the filter still works for this visit
  }
  applyFilter();
}

for (const button of stageButtons) {
  button.addEventListener("click", () => setStatus(statusFilter === button.dataset.status ? "" : button.dataset.status));
}
document.getElementById("status-show-all").addEventListener("click", () => setStatus(""));

// The stage remembered from last time, if it still has jobs (a disabled button means none)
function rememberedStatus() {
  let status = "";
  try {
    status = localStorage.getItem(STATUS_KEY) || "";
  } catch {
    return "";
  }
  const button = stageButtons.find((candidate) => candidate.dataset.status === status);
  if (!button || button.disabled) return "";
  // A job just changed (e.g. Saved -> Applied) is reopened by the page: never hide it behind the old filter
  const changed = document.getElementById("changed-job");
  if (changed && changed.closest("li").dataset.status !== status) return "";
  return status;
}

try {
  const savedSort = localStorage.getItem(SORT_KEY);
  if (savedSort in SORTS) sortSelect.value = savedSort;
} catch {
  // Use the default sort
}

sortSelect.addEventListener("change", applySort);
filterInput.addEventListener("input", applyFilter);
clearForm.addEventListener("submit", (event) => {
  if (!confirm(`Remove all ${clearForm.dataset.total} saved jobs? This cannot be undone.`)) event.preventDefault();
});
// Removing one job can't be undone either, so ask first, as for Clear all
for (const form of document.querySelectorAll(".remove-form")) {
  form.addEventListener("submit", (event) => {
    if (!confirm(`Remove "${form.dataset.title}" from your saved jobs?`)) event.preventDefault();
  });
}

applySort();
setStatus(rememberedStatus());
