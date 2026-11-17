// Results page: sorts the job cards in the browser, like the Saved jobs page.
// Each card carries data-order (its place in the match ranking), data-posted ("2026-11-12", or "" if unknown),
// data-salary, data-match and data-outcome. Exact matches and close matches are two lists, each sorted on its own,
// so close matches always stay below. In every order, Strong matches come first, then Partial, then jobs missing a
// requirement. Any order but "Recommended" hides the rank numbers.

const sortBar = document.getElementById("sort-bar");
const sortSelect = document.getElementById("job-sort");
const lists = [...document.querySelectorAll(".job-list")].map((list) => ({ list, cards: [...list.children] }));

// Compare functions for each option; ties keep the match ranking
const byRank = (a, b) => a.dataset.order - b.dataset.order;
// A job with no posting date sorts last either way
const byDate = (newestFirst) => (a, b) => {
  const first = a.dataset.posted, second = b.dataset.posted;
  if (!first || !second) return (!first - !second) || byRank(a, b);
  return (newestFirst ? second.localeCompare(first) : first.localeCompare(second)) || byRank(a, b);
};
const SORTS = {
  recommended: byRank,
  newest: byDate(true),
  oldest: byDate(false),
  salary: (a, b) => b.dataset.salary - a.dataset.salary || byRank(a, b),
  match: (a, b) => b.dataset.match - a.dataset.match || byRank(a, b),
};

// Strong, then Partial, then missing a requirement; the chosen order within each group
const bestOutcomeFirst = (compare) => (a, b) => (b.dataset.outcome - a.dataset.outcome) || compare(a, b);

function applySort() {
  const compare = bestOutcomeFirst(SORTS[sortSelect.value] || byRank);
  for (const { list, cards } of lists) {
    list.append(...[...cards].sort(compare));  // append moves the existing cards into the new order
    list.classList.toggle("unranked", sortSelect.value !== "recommended");
  }
}

sortBar.hidden = false;
sortSelect.addEventListener("change", applySort);
