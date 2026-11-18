'use strict';

(function initStudents() {
  const els = {
    search: $('#student-search'),
    table: $('#students-table'),
    body: $('#students-body'),
    cards: $('#students-cards'),
    empty: $('#students-empty'),
    emptyTitle: $('#students-empty-title'),
    emptyBody: $('#students-empty-body'),
    count: $('#student-count'),
  };

  let all = [];

  function sections(student) {
    if (!student.classes || !student.classes.length) {
      return '<span class="text-3">Not on any roster</span>';
    }
    return student.classes
      .map((c) => `<span class="chip chip-plain">${escapeHtml(c)}</span>`)
      .join(' ');
  }

  function render(list) {
    els.count.textContent = `${list.length} of ${all.length}`;

    if (!list.length) {
      els.body.innerHTML = '';
      els.cards.innerHTML = '';
      els.table.classList.add('hidden');
      els.empty.classList.remove('hidden');
      const searching = els.search.value.trim().length > 0;
      els.emptyTitle.textContent = searching ? 'No matches' : 'No students enrolled';
      els.emptyBody.textContent = searching
        ? 'No student matches that search.'
        : 'Enrol a student to create their biometric profile.';
      return;
    }
    els.table.classList.remove('hidden');
    els.empty.classList.add('hidden');

    els.body.innerHTML = list.map((s) => `
      <tr>
        <td class="mono">${escapeHtml(s.student_id)}</td>
        <td><strong>${escapeHtml(s.name)}</strong></td>
        <td><span class="cluster">${sections(s)}</span></td>
        <td class="shrink">
          <button class="btn btn-quiet btn-sm" type="button" data-delete="${escapeHtml(s.student_id)}">Delete</button>
        </td>
      </tr>`).join('');

    els.cards.innerHTML = list.map((s) => `
      <div class="record-card">
        <span class="rc-name">${escapeHtml(s.name)}</span>
        <span class="rc-status">
          <button class="btn btn-quiet btn-sm" type="button" data-delete="${escapeHtml(s.student_id)}">Delete</button>
        </span>
        <span class="rc-meta">${escapeHtml(s.student_id)}</span>
        <span class="rc-meta cluster" style="grid-column:1/-1;margin-top:var(--s-2)">${sections(s)}</span>
      </div>`).join('');
  }

  function filter() {
    const q = els.search.value.trim().toLowerCase();
    render(!q ? all : all.filter((s) =>
      s.student_id.toLowerCase().includes(q) || (s.name || '').toLowerCase().includes(q)));
  }

  async function load() {
    try {
      const res = await api('/api/students');
      all = res.students || [];
      filter();
    } catch (err) {
      toast(err.message, 'error');
      els.body.innerHTML = '';
      els.table.classList.add('hidden');
      els.cards.replaceChildren(retryState('Could not load students.', load));
    }
  }

  els.search.addEventListener('input', filter);

  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-delete]');
    if (!btn) return;
    const studentId = btn.dataset.delete;
    const student = all.find((s) => s.student_id === studentId);
    const onRosters = student && student.classes && student.classes.length;

    /* Deleting a face profile is irreversible — the embeddings are gone and
       the student has to be photographed again — so say so plainly. */
    let warning = `Delete ${student ? student.name : studentId}?\n\n`
      + 'Their face profile is permanently removed and they must be enrolled again from photographs.';
    if (onRosters) {
      warning += `\n\nThey stay on ${student.classes.length} roster(s) and will count as absent until re-enrolled.`;
    }
    if (!confirm(warning)) return;

    try {
      await api(`/api/students/${encodeURIComponent(studentId)}`, { method: 'DELETE' });
      toast(`${studentId} deleted`, 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  load();
})();
