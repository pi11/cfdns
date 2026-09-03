document.addEventListener("DOMContentLoaded", () => {
  const table = document.querySelector(".atw-table");
  const all = document.querySelector("#toggle-all-atw-accounts");
  if (!table || !all) return;
  const buttons = () => [...table.querySelectorAll(".atw-account-toggle")];
  const set = (button, expanded) => {
    button.closest(".atw-account-group").querySelectorAll(".atw-service-row").forEach((row) => { row.hidden = !expanded; });
    button.setAttribute("aria-expanded", String(expanded));
  };
  const label = () => { const expanded = buttons().every((button) => button.getAttribute("aria-expanded") === "true"); all.textContent = expanded ? "Collapse all" : "Expand all"; };
  table.addEventListener("click", (event) => { const button = event.target.closest(".atw-account-toggle"); if (!button || event.target.closest("a")) return; set(button, button.getAttribute("aria-expanded") !== "true"); label(); });
  all.addEventListener("click", () => { const expand = !buttons().every((button) => button.getAttribute("aria-expanded") === "true"); buttons().forEach((button) => set(button, expand)); label(); });
  label();
});
