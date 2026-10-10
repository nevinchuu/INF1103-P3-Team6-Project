// Saved jobs page: filters and sorts the job cards in the browser, and asks before clearing them all.
// Each card carries data-order (position in the saved file), data-salary, data-match and data-text.

const list = document.getElementById("job-list");
const cards = [...list.children];
const filterInput = document.getElementById("job-filter");
const sortSelect = document.getElementById("job-sort");
const noMatch = document.getElementById("no-filter-match");
const clearForm = document.getElementById("clear-form");

const SORT_KEY = "jobMatcher.savedSort";

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
    const match = words.every((word) => card.dataset.text.includes(word));
    card.hidden = !match;
    if (match) shown++;
  }
  noMatch.hidden = shown > 0;
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

applySort();
