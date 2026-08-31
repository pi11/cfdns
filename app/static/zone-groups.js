(() => {
  const allToggle = document.querySelector('#toggle-all-zones');
  if (!allToggle) return;

  const zoneRows = (zoneId) => [
    ...document.querySelectorAll(`#records .zone-record[data-zone-id="${zoneId}"]`),
  ];

  const clearHiddenSelection = (rows) => {
    rows.forEach((row) => {
      if (!row.hidden) return;
      const checkbox = row.querySelector('.record-select');
      if (checkbox) checkbox.checked = false;
    });
    document.dispatchEvent(new CustomEvent('records:visibility-changed'));
  };

  const setZoneExpanded = (button, expanded) => {
    const rows = zoneRows(button.dataset.zoneId);
    rows.forEach((row) => { row.hidden = !expanded; });
    button.setAttribute('aria-expanded', String(expanded));
    if (!expanded) clearHiddenSelection(rows);
  };

  const synchronizeAllToggle = () => {
    const buttons = [...document.querySelectorAll('#records .zone-toggle')];
    const allExpanded = buttons.length > 0
      && buttons.every((button) => button.getAttribute('aria-expanded') === 'true');
    allToggle.textContent = allExpanded ? 'Collapse all' : 'Expand all';
    allToggle.dataset.expanded = String(allExpanded);
    allToggle.hidden = buttons.length === 0;
  };

  const synchronizeGroups = () => {
    document.querySelectorAll('#records .zone-group').forEach((group) => {
      const records = group.querySelectorAll('.zone-record');
      if (!records.length) {
        group.remove();
        return;
      }
      const count = group.querySelector('.zone-toggle small');
      if (count) count.textContent = `${records.length} record${records.length === 1 ? '' : 's'} on this page`;
    });
    synchronizeAllToggle();
  };

  document.addEventListener('click', (event) => {
    const button = event.target.closest('.zone-toggle');
    if (!button) return;
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    setZoneExpanded(button, expanded);
    synchronizeAllToggle();
  });

  allToggle.addEventListener('click', () => {
    const expand = allToggle.dataset.expanded !== 'true';
    document.querySelectorAll('#records .zone-toggle').forEach((button) => {
      setZoneExpanded(button, expand);
    });
    synchronizeAllToggle();
  });

  document.addEventListener('htmx:afterSwap', synchronizeGroups);
  document.addEventListener('records:changed', synchronizeGroups);
  synchronizeGroups();
})();
