// What-if page: the two tabs (Try a skill / Plan what to learn), and sending a form as soon as a skill, the list
// size, the plan's goal or its length changes, so the answer updates without pressing a button and without
// reloading the page. Unticking the last skill clears the answer.
// Without JavaScript the tabs stay hidden and both forms show, one under the other, each with its own button.

const tabList = document.querySelector("[data-tabs]");
if (tabList) {
  const tabs = [...tabList.querySelectorAll('[role="tab"]')];

  // Shows one tab's panel and hides the other; only the chosen tab can be reached with Tab
  function select(tab, moveFocus) {
    for (const other of tabs) {
      const chosen = other === tab;
      other.setAttribute("aria-selected", String(chosen));
      other.tabIndex = chosen ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !chosen;
    }
    if (moveFocus) tab.focus();
  }

  tabList.hidden = false;
  select(tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0], false);
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab, false));
    // Left and right arrows move between the tabs, as in any tab list
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      const step = event.key === "ArrowRight" ? 1 : tabs.length - 1;
      select(tabs[(index + step) % tabs.length], true);
    });
  });
}

// With JavaScript the answer updates by itself, so the forms' own buttons ("See the difference", "Build my plan")
// are only for browsers without it
for (const button of document.querySelectorAll("[data-no-js]")) button.hidden = true;

// The answer is fetched in the background and only the result (and any problem message) is swapped in, so the page
// never reloads: it does not jump to the result, and the ticked skills stay where they are. Without JavaScript the
// forms post as usual and the page opens at #result.
let latest = 0;  // only the newest request may change the page, however the answers arrive

function removeResult() {
  document.getElementById("result")?.remove();
  for (const alert of document.querySelectorAll("main .alert-error")) alert.remove();
}

async function update(form) {
  const request = ++latest;
  let page;
  try {
    const reply = await fetch(form.action.split("#")[0], { method: "POST", body: new FormData(form) });
    if (!reply.ok) throw new Error(`HTTP ${reply.status}`);
    page = new DOMParser().parseFromString(await reply.text(), "text/html");
  } catch (error) {
    if (request === latest) form.submit();  // the background request failed: send the form the usual way
    return;
  }
  if (request !== latest) return;  // a newer choice was made while this one was on its way

  // The problem message, if any, goes where the page puts it: just before the tabs
  for (const alert of document.querySelectorAll("main .alert-error")) alert.remove();
  const problem = page.querySelector(".alert-error");
  const tabs = document.querySelector("[data-tabs]");
  if (problem && tabs) tabs.before(problem);

  // The result: replaced, added after the forms, or removed when the reply has none
  const fresh = page.getElementById("result");
  const current = document.getElementById("result");
  if (fresh && current) current.replaceWith(fresh);
  else if (fresh) document.getElementById("panel-plan").after(fresh);
  else if (current) current.remove();

  // The plan form keeps the list size in a hidden field; take the one the reply was made with
  const size = page.querySelector('#panel-plan input[name="top_n"]');
  const mine = document.querySelector('#panel-plan input[name="top_n"]');
  if (size && mine) mine.value = size.value;
}

for (const form of document.querySelectorAll("form[data-autosubmit]")) {
  form.addEventListener("submit", (event) => {  // the Add button, and every change below
    event.preventDefault();
    update(form);
  });
  form.addEventListener("change", (event) => {
    if (event.target.matches('input[type="radio"]')) {  // a plan's goal or length
      form.requestSubmit();
      return;
    }
    if (!event.target.matches('input[type="checkbox"], select')) return;  // a typed skill waits for its Add button
    const ticked = form.querySelector('input[name="skill"]:checked');
    const typed = form.querySelector('input[name="extra_skills"]');
    if (!ticked && !(typed && typed.value.trim())) {  // nothing ticked any more: no answer to show
      latest++;  // and an answer still on its way must not bring it back
      removeResult();
      return;
    }
    form.requestSubmit();
  });
}
