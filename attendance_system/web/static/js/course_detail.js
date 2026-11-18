'use strict';

(function initCourseDetail() {
  const CLASS_ID = $('#course-root').dataset.classId;

  const els = {
    addForm: $('#add-form'),
    addIds: $('#add-ids'),
    btnAdd: $('#btn-add'),
    file: $('#roster-file'),
    dropzone: $('#roster-dropzone'),
    table: $('#roster-table'),
    body: $('#roster-body'),
    cards: $('#roster-cards'),
    empty: $('#roster-empty'),
    count: $('#roster-count'),
    noFace: $('#roster-noface'),
  };

  /* ---- render ---------------------------------------------------------- */
  function faceCell(student) {
    return student.has_face
      ? '<span class="chip chip-present">Enrolled</span>'
      : '<span class="chip chip-unknown">No face yet</span>';
  }

  function render(roster) {
    els.count.textContent = `${roster.length} student${roster.length === 1 ? '' : 's'}`;

    /* Students on the roster with no face profile can never be recognised,
       so they would silently count as absent forever. Surface that. */
    const missing = roster.filter((r) => !r.has_face).length;
    els.noFace.classList.toggle('hidden', missing === 0);
    if (missing) els.noFace.textContent = `${missing} need enrolment`;

    if (!roster.length) {
      els.body.innerHTML = '';
      els.cards.innerHTML = '';
      els.table.classList.add('hidden');
      els.empty.classList.remove('hidden');
      return;
    }
    els.table.classList.remove('hidden');
    els.empty.classList.add('hidden');

    els.body.innerHTML = roster.map((r) => `
      <tr>
        <td class="mono">${escapeHtml(r.student_id)}</td>
        <td>${r.name ? `<strong>${escapeHtml(r.name)}</strong>` : '<span class="text-3">—</span>'}</td>
        <td>${faceCell(r)}</td>
        <td class="shrink">
          <button class="btn btn-quiet btn-sm" type="button" data-remove="${escapeHtml(r.student_id)}">Remove</button>
        </td>
      </tr>`).join('');

    els.cards.innerHTML = roster.map((r) => `
      <div class="record-card">
        <span class="rc-name">${r.name ? escapeHtml(r.name) : escapeHtml(r.student_id)}</span>
        <span class="rc-status">${faceCell(r)}</span>
        <span class="rc-meta">${escapeHtml(r.student_id)}</span>
        <span class="rc-meta" style="grid-column:1/-1;margin-top:var(--s-2)">
          <button class="btn btn-quiet btn-sm" type="button" data-remove="${escapeHtml(r.student_id)}">Remove</button>
        </span>
      </div>`).join('');
  }

  async function load() {
    try {
      const res = await api(`/api/classes/${CLASS_ID}/roster`);
      render(res.roster || []);
    } catch (err) {
      toast(err.message, 'error');
      els.body.innerHTML = '';
      els.table.classList.add('hidden');
      els.cards.replaceChildren(retryState('Could not load the roster.', load));
    }
  }

  /* ---- add by ID ------------------------------------------------------- */
  els.addForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ids = els.addIds.value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    if (!ids.length) {
      toast('Enter at least one student ID.', 'warn');
      els.addIds.focus();
      return;
    }

    setLoading(els.btnAdd, true, 'Adding to roster');
    try {
      const res = await api(`/api/classes/${CLASS_ID}/roster`, {
        method: 'POST',
        body: JSON.stringify({ student_ids: ids }),
      });
      summarise(res);
      els.addIds.value = '';
      load();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setLoading(els.btnAdd, false);
    }
  });

  function summarise(res) {
    const added = (res.added || []).length;
    const skipped = (res.skipped || []).length;
    if (added) toast(`Added ${added} student${added === 1 ? '' : 's'} to the roster`, 'success');
    if (skipped) toast(`${skipped} already on the roster — skipped`, 'info');
    if (!added && !skipped) toast('Nothing to add.', 'warn');
  }

  /* ---- CSV import ------------------------------------------------------ */
  async function importFile(file) {
    if (!file) return;
    try {
      const text = await file.text();
      const res = await api(`/api/classes/${CLASS_ID}/roster/import`, {
        method: 'POST',
        body: JSON.stringify({ csv: text }),
      });
      summarise(res);
      load();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      els.file.value = '';
    }
  }

  els.file.addEventListener('change', () => importFile(els.file.files[0]));

  ['dragenter', 'dragover'].forEach((type) => {
    els.dropzone.addEventListener(type, (e) => { e.preventDefault(); els.dropzone.classList.add('dragging'); });
  });
  ['dragleave', 'drop'].forEach((type) => {
    els.dropzone.addEventListener(type, (e) => { e.preventDefault(); els.dropzone.classList.remove('dragging'); });
  });
  els.dropzone.addEventListener('drop', (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) importFile(f);
  });

  /* ---- remove ---------------------------------------------------------- */
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-remove]');
    if (!btn) return;
    const studentId = btn.dataset.remove;
    if (!confirm(`Remove ${studentId} from this roster?\n\nTheir face profile and past attendance are kept.`)) return;
    try {
      await api(`/api/classes/${CLASS_ID}/roster/${encodeURIComponent(studentId)}`, { method: 'DELETE' });
      toast(`${studentId} removed from the roster`, 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  load();
})();
