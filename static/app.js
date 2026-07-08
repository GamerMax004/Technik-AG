(function(){
  const panel = document.getElementById('boardPanel');
  if(!panel) return;

  const addPersonUrl = panel.dataset.addPersonUrl;
  const addDateUrl = panel.dataset.addDateUrl;
  const entryUrl = panel.dataset.entryUrl;

  const STATES = ['unset', 'ok', 'warn', 'no'];
  const ICONS = {
    ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>',
    no: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
    unset: ''
  };

  async function api(url, options){
    const res = await fetch(url, Object.assign({
      headers: { 'Content-Type': 'application/json' }
    }, options));
    if(!res.ok){
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'Anfrage fehlgeschlagen');
    }
    return res.json();
  }

  function setChip(cellBtn, status){
    const chip = cellBtn.querySelector('.chip');
    chip.className = 'chip ' + status;
    chip.innerHTML = ICONS[status] || '';
    cellBtn.dataset.status = status;
    cellBtn.setAttribute('aria-label', 'Status: ' + status);
  }

  // Initial render of icons for server-rendered cells
  document.querySelectorAll('.cell[data-status]').forEach(cellBtn => {
    setChip(cellBtn, cellBtn.dataset.status || 'unset');
  });

  // Cell click -> cycle status
  panel.addEventListener('click', async (e) => {
    const cellBtn = e.target.closest('.cell');
    if(cellBtn){
      const current = cellBtn.dataset.status || 'unset';
      const next = STATES[(STATES.indexOf(current) + 1) % STATES.length];
      setChip(cellBtn, next); // optimistic UI
      try{
        await api(entryUrl, {
          method: 'POST',
          body: JSON.stringify({
            person_id: Number(cellBtn.dataset.person),
            date_id: Number(cellBtn.dataset.date),
            status: next
          })
        });
      } catch(err){
        setChip(cellBtn, current); // revert on failure
        alert('Status konnte nicht gespeichert werden: ' + err.message);
      }
      return;
    }

    const removeDateBtn = e.target.closest('[data-remove-date]');
    if(removeDateBtn){
      if(!confirm('Diesen Termin wirklich entfernen?')) return;
      try{
        await api('/api/date/' + removeDateBtn.dataset.removeDate, { method: 'DELETE' });
        location.reload();
      } catch(err){ alert(err.message); }
      return;
    }

    const removePersonBtn = e.target.closest('[data-remove-person]');
    if(removePersonBtn){
      if(!confirm('Diese Person wirklich entfernen?')) return;
      try{
        await api('/api/person/' + removePersonBtn.dataset.removePerson, { method: 'DELETE' });
        location.reload();
      } catch(err){ alert(err.message); }
      return;
    }
  });

  // Rename person
  panel.addEventListener('change', async (e) => {
    const input = e.target.closest('[data-rename]');
    if(!input) return;
    try{
      await api('/api/person/' + input.dataset.rename, {
        method: 'PATCH',
        body: JSON.stringify({ name: input.value })
      });
    } catch(err){ alert(err.message); }
  });

  // Add person
  const personInput = document.getElementById('personInput');
  const addPersonBtn = document.getElementById('addPersonBtn');
  async function submitPerson(){
    const name = personInput.value.trim();
    if(!name) return;
    try{
      await api(addPersonUrl, { method: 'POST', body: JSON.stringify({ name }) });
      location.reload();
    } catch(err){ alert(err.message); }
  }
  addPersonBtn.addEventListener('click', submitPerson);
  personInput.addEventListener('keydown', e => { if(e.key === 'Enter') submitPerson(); });

  // Add date
  const dateInput = document.getElementById('dateInput');
  const addDateBtn = document.getElementById('addDateBtn');
  async function submitDate(){
    const value = dateInput.value;
    if(!value) return;
    try{
      await api(addDateUrl, { method: 'POST', body: JSON.stringify({ date: value }) });
      location.reload();
    } catch(err){ alert(err.message); }
  }
  addDateBtn.addEventListener('click', submitDate);

  // Registrierten Nutzer per Dropdown hinzufügen
  const userSelect = document.getElementById('userSelect');
  const addUserBtn = document.getElementById('addUserBtn');
  if(addUserBtn){
    addUserBtn.addEventListener('click', async () => {
      const userId = userSelect.value;
      if(!userId) return;
      try{
        await api(addPersonUrl, { method: 'POST', body: JSON.stringify({ user_id: Number(userId) }) });
        location.reload();
      } catch(err){ alert(err.message); }
    });
  }
})();
