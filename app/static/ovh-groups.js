document.addEventListener("DOMContentLoaded", () => {
  const table = document.querySelector(".ovh-table");
  const toggleAll = document.querySelector("#toggle-all-ovh-accounts");
  if (!table || !toggleAll) return;

  const groups = () => [...table.querySelectorAll(".ovh-account-group")];
  const setGroup = (group, expanded) => {
    group.querySelectorAll(".ovh-service-row").forEach((row) => { row.hidden = !expanded; });
    const button = group.querySelector(".ovh-account-toggle");
    if (button) button.setAttribute("aria-expanded", String(expanded));
  };
  const updateAllLabel = () => {
    const everyExpanded = groups().every((group) =>
      group.querySelector(".ovh-account-toggle")?.getAttribute("aria-expanded") === "true"
    );
    toggleAll.textContent = everyExpanded ? "Collapse all" : "Expand all";
  };

  table.addEventListener("click", (event) => {
    const button = event.target.closest(".ovh-account-toggle");
    if (!button || event.target.closest("a")) return;
    const group = button.closest(".ovh-account-group");
    setGroup(group, button.getAttribute("aria-expanded") !== "true");
    updateAllLabel();
  });
  toggleAll.addEventListener("click", () => {
    const expand = !groups().every((group) =>
      group.querySelector(".ovh-account-toggle")?.getAttribute("aria-expanded") === "true"
    );
    groups().forEach((group) => setGroup(group, expand));
    updateAllLabel();
  });
  updateAllLabel();
});
