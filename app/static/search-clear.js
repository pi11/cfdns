(() => {
  const syncButton = (input) => {
    const button = input.closest('.search-field')?.querySelector('.search-clear');
    if (button) button.hidden = input.value.length === 0;
  };

  const initialize = (input) => {
    if (input.closest('.search-field')) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'search-field';
    input.before(wrapper);
    wrapper.append(input);

    const button = document.createElement('button');
    button.className = 'search-clear';
    button.type = 'button';
    button.textContent = '×';
    const label = input.dataset.clearable !== undefined
      ? `Clear ${input.labels[0]?.textContent.trim() || 'field'}`
      : 'Clear search';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.addEventListener('click', () => {
      input.value = '';
      syncButton(input);
      input.focus();
      input.dispatchEvent(new Event('input', {bubbles: true}));
      if (input.type === 'search' && !input.form?.hasAttribute('hx-get')) input.form?.requestSubmit();
    });
    wrapper.append(button);
    syncButton(input);
  };

  const initializeAll = () => {
    document.querySelectorAll('input[type="search"], input[data-clearable]').forEach(initialize);
  };

  document.addEventListener('DOMContentLoaded', initializeAll);
  document.addEventListener('input', (event) => {
    if (event.target.matches?.('input[type="search"], input[data-clearable]')) syncButton(event.target);
  });
  document.addEventListener('click', () => {
    requestAnimationFrame(() => {
      document.querySelectorAll('input[type="search"], input[data-clearable]').forEach(syncButton);
    });
  });
  document.addEventListener('htmx:afterSwap', initializeAll);
})();
