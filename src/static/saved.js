// Saved jobs page: asks before clearing them all.

const clearForm = document.getElementById("clear-form");

clearForm.addEventListener("submit", (event) => {
  if (!confirm(`Remove all ${clearForm.dataset.total} saved jobs? This cannot be undone.`)) event.preventDefault();
});
