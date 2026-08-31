(() => {
  const actionButton = document.querySelector('#bulk-delete-button');
  const modal = document.querySelector('#bulk-delete-modal');
  if (!actionButton || !modal) return;

  const countOutput = modal.querySelector('#bulk-delete-count');
  const cancelButton = modal.querySelector('#bulk-delete-cancel');
  const confirmButton = modal.querySelector('#bulk-delete-confirm');
  const recordCheckboxes = () => [...document.querySelectorAll('#records .record-select')]
    .filter((checkbox) => !checkbox.closest('tr').hidden);
  const selectedCheckboxes = () => recordCheckboxes().filter((checkbox) => checkbox.checked);

  const synchronizeSelection = () => {
    const checkboxes = recordCheckboxes();
    const selected = selectedCheckboxes();
    const selectAll = document.querySelector('#select-visible-records');
    actionButton.hidden = selected.length === 0;
    actionButton.querySelector('span').textContent = selected.length;
    if (selectAll) {
      selectAll.checked = checkboxes.length > 0 && selected.length === checkboxes.length;
      selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
    }
  };

  document.addEventListener('change', (event) => {
    if (event.target.matches('#select-visible-records')) {
      recordCheckboxes().forEach((checkbox) => {
        checkbox.checked = event.target.checked;
      });
      synchronizeSelection();
    } else if (event.target.matches('.record-select')) {
      synchronizeSelection();
    }
  });

  document.addEventListener('htmx:afterSwap', synchronizeSelection);
  document.addEventListener('records:visibility-changed', synchronizeSelection);

  actionButton.addEventListener('click', () => {
    const count = selectedCheckboxes().length;
    if (!count) return;
    countOutput.textContent = count;
    modal.showModal();
  });

  cancelButton.addEventListener('click', () => modal.close());

  confirmButton.addEventListener('click', async () => {
    const selected = selectedCheckboxes();
    if (!selected.length) {
      modal.close();
      return;
    }
    const body = new FormData();
    selected.forEach((checkbox) => body.append('record_ids', checkbox.value));
    modal.classList.add('deleting');
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    try {
      const response = await fetch('/records/actions/bulk-delete', {method: 'POST', body});
      if (!response.ok) throw new Error(await response.text() || 'Bulk deletion failed.');
      const result = await response.json();
      result.deleted_ids.forEach((recordId) => {
        document.querySelector(`#record-${recordId}`)?.remove();
      });
      document.dispatchEvent(new CustomEvent('records:changed'));
      if (result.errors.length) {
        const details = result.errors.map((item) => `${item.name}: ${item.error}`).join('\n');
        window.alert(`Some records could not be deleted:\n\n${details}`);
      }
      modal.close();
      synchronizeSelection();
    } catch (error) {
      window.alert(error.message || 'The selected records could not be deleted.');
    } finally {
      modal.classList.remove('deleting');
      confirmButton.disabled = false;
      cancelButton.disabled = false;
    }
  });

  synchronizeSelection();
})();
