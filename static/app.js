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
  const LABELS = { unset: 'Offen', ok: 'Verfügbar', warn: 'Mit Vorbehalt', no: 'Nicht verfügbar' };

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

  // --- Kleine Grid-Zellen (Staff-Ansicht) ---
  function setChip(cellBtn, status){
    const chip = cellBtn.querySelector('.chip');
    chip.className = 'chip ' + status;
    chip.innerHTML = ICONS[status] || '';
    cellBtn.dataset.status = status;
    cellBtn.setAttribute('aria-label', 'Status: ' + status);
  }

  document.querySelectorAll('.cell[data-status]').forEach(cellBtn => {
    setChip(cellBtn, cellBtn.dataset.status || 'unset');
  });

  // --- Große Status-Pills (Kartenansicht normaler Nutzer) ---
  function setPill(pillBtn, status){
    pillBtn.className = pillBtn.className.includes('compact') ? 'status-pill compact ' + status : 'status-pill ' + status;
    pillBtn.querySelector('.status-pill-icon').innerHTML = ICONS[status] || '<span class="pill-dot"></span>';
    const labelEl = pillBtn.querySelector('.status-pill-label');
    if(labelEl) labelEl.textContent = LABELS[status];
    pillBtn.dataset.status = status;
    pillBtn.setAttribute('aria-label', 'Status: ' + LABELS[status]);
  }

  document.querySelectorAll('.status-pill[data-status]').forEach(pillBtn => {
    setPill(pillBtn, pillBtn.dataset.status || 'unset');
  });

  async function cycleStatus(btn, isPill){
    const current = btn.dataset.status || 'unset';
    const next = STATES[(STATES.indexOf(current) + 1) % STATES.length];
    if(isPill) setPill(btn, next); else setChip(btn, next); // optimistic UI
    try{
      await api(entryUrl, {
        method: 'POST',
        body: JSON.stringify({
          person_id: Number(btn.dataset.person),
          date_id: Number(btn.dataset.date),
          status: next
        })
      });
    } catch(err){
      if(isPill) setPill(btn, current); else setChip(btn, current); // revert on failure
      alert('Status konnte nicht gespeichert werden: ' + err.message);
    }
  }

  panel.addEventListener('click', async (e) => {
    const cellBtn = e.target.closest('.cell');
    if(cellBtn){ await cycleStatus(cellBtn, false); return; }

    const pillBtn = e.target.closest('.status-pill');
    if(pillBtn){ await cycleStatus(pillBtn, true); return; }

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

    const removeAttachmentBtn = e.target.closest('[data-remove-attachment]');
    if(removeAttachmentBtn){
      if(!confirm('PDF-Anhang wirklich entfernen?')) return;
      try{
        await api('/api/date/' + removeAttachmentBtn.dataset.removeAttachment + '/attachment', { method: 'DELETE' });
        location.reload();
      } catch(err){ alert(err.message); }
      return;
    }
  });

  // Rename person
  panel.addEventListener('change', async (e) => {
    const renameInput = e.target.closest('[data-rename]');
    if(renameInput){
      try{
        await api('/api/person/' + renameInput.dataset.rename, {
          method: 'PATCH',
          body: JSON.stringify({ name: renameInput.value })
        });
      } catch(err){ alert(err.message); }
      return;
    }

    // PDF-Anhang hochladen
    const fileInput = e.target.closest('[data-upload-attachment]');
    if(fileInput){
      const file = fileInput.files[0];
      if(!file) return;
      if(!file.name.toLowerCase().endsWith('.pdf')){
        alert('Nur PDF-Dateien sind erlaubt.');
        fileInput.value = '';
        return;
      }
      const formData = new FormData();
      formData.append('file', file);
      try{
        const res = await fetch('/api/date/' + fileInput.dataset.uploadAttachment + '/attachment', {
          method: 'POST', body: formData
        });
        if(!res.ok){
          const data = await res.json().catch(() => ({}));
          throw new Error(data.error || 'Upload fehlgeschlagen');
        }
        location.reload();
      } catch(err){ alert(err.message); }
    }
  });

  // Add date (mit optionaler Notiz)
  const dateInput = document.getElementById('dateInput');
  const dateLabelInput = document.getElementById('dateLabelInput');
  const addDateBtn = document.getElementById('addDateBtn');
  if(addDateBtn){
    async function submitDate(){
      const value = dateInput.value;
      if(!value) return;
      try{
        await api(addDateUrl, { method: 'POST', body: JSON.stringify({ date: value, label: dateLabelInput.value.trim() }) });
        location.reload();
      } catch(err){ alert(err.message); }
    }
    addDateBtn.addEventListener('click', submitDate);
  }

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

  // Änderungsprotokoll-Modal
  const openLogBtn = document.getElementById('openLogBtn');
  const closeLogBtn = document.getElementById('closeLogBtn');
  const logModalBackdrop = document.getElementById('logModalBackdrop');
  if(openLogBtn && logModalBackdrop){
    openLogBtn.addEventListener('click', () => { logModalBackdrop.hidden = false; });
    closeLogBtn.addEventListener('click', () => { logModalBackdrop.hidden = true; });
    logModalBackdrop.addEventListener('click', (e) => { if(e.target === logModalBackdrop) logModalBackdrop.hidden = true; });
    document.addEventListener('keydown', (e) => { if(e.key === 'Escape') logModalBackdrop.hidden = true; });
  }
})();
