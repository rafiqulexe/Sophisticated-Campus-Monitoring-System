'use strict';

(function initUnknownFaces() {
  const els = {
    grid: $('#face-grid'),
    empty: $('#faces-empty'),
    count: $('#face-count'),
    btnReload: $('#btn-reload'),
  };

  function card(face) {
    const name = escapeHtml(face.name);
    return `
      <figure class="face-card" data-name="${name}">
        <img src="/api/unknown-faces/${encodeURIComponent(face.name)}"
             alt="Unrecognised face captured on ${escapeHtml(face.captured)}" loading="lazy">
        <figcaption>
          <span class="mono">${escapeHtml(face.captured)}</span>
          <span class="face-actions">
            <a class="btn btn-quiet btn-sm" href="/register"
               title="Open enrolment to add this person">Enrol</a>
            <button class="btn btn-quiet btn-sm" type="button"
                    data-discard="${name}" title="Delete this crop">Discard</button>
          </span>
        </figcaption>
      </figure>`;
  }

  function render(faces, unreadable) {
    els.count.textContent = `${faces.length} face${faces.length === 1 ? '' : 's'}`;

    /* Empty files on disk cannot be reviewed, so they are reported rather
       than shown as broken thumbnails. */
    if (unreadable) {
      toast(`${unreadable} capture${unreadable === 1 ? ' is' : 's are'} empty `
        + 'and cannot be shown.', 'warn');
    }

    if (!faces.length) {
      els.grid.innerHTML = '';
      els.empty.classList.remove('hidden');
      return;
    }
    els.empty.classList.add('hidden');
    els.grid.innerHTML = faces.map(card).join('');
  }

  async function load() {
    setLoading(els.btnReload, true, 'Refreshing');
    try {
      const res = await api('/api/unknown-faces');
      render(res.faces || [], res.unreadable || 0);
    } catch (err) {
      toast(err.message, 'error');
      els.grid.replaceChildren(retryState('Could not load captured faces.', load));
    } finally {
      setLoading(els.btnReload, false);
    }
  }

  els.btnReload.addEventListener('click', load);

  els.grid.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-discard]');
    if (!btn) return;
    const name = btn.dataset.discard;
    /* Deleting the crop is irreversible and the image is the only record of
       that detection, so it is confirmed rather than done on one click. */
    if (!confirm('Discard this face?\n\nThe image is deleted permanently.')) return;

    setLoading(btn, true, 'Discarding');
    try {
      await api(`/api/unknown-faces/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const card = btn.closest('.face-card');
      if (card) card.remove();
      const left = els.grid.querySelectorAll('.face-card').length;
      els.count.textContent = `${left} face${left === 1 ? '' : 's'}`;
      if (!left) els.empty.classList.remove('hidden');
      toast('Face discarded', 'success');
    } catch (err) {
      toast(err.message, 'error');
      setLoading(btn, false);
    }
  });

  load();
})();
