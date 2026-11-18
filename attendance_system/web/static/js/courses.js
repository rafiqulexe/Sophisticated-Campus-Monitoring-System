'use strict';

(function initCourses() {
  const els = {
    btnNew: $('#btn-new-course'),
    btnCancel: $('#btn-cancel-course'),
    formCard: $('#course-form-card'),
    formHeading: $('#course-form-heading'),
    form: $('#course-form'),
    hint: $('#course-form-hint'),
    btnSave: $('#btn-save-course'),
    id: $('#course-id'),
    code: $('#course-code'),
    name: $('#course-name'),
    section: $('#course-section'),
    semester: $('#course-semester'),
    table: $('#courses-table'),
    body: $('#courses-body'),
    cards: $('#course-cards'),
    empty: $('#courses-empty'),
    count: $('#course-count'),
  };

  /* ---- form ------------------------------------------------------------ */
  function openForm(course) {
    els.formCard.classList.remove('hidden');
    els.formHeading.textContent = course ? 'Edit course section' : 'New course section';
    els.id.value = course ? course.id : '';
    els.code.value = course ? course.course_code : '';
    els.name.value = course ? course.course_name : '';
    els.section.value = course ? course.section : '';
    els.semester.value = course ? course.semester : '';
    els.hint.textContent = 'All four fields are required.';
    els.code.focus();
    els.formCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeForm() {
    els.formCard.classList.add('hidden');
    els.form.reset();
    els.id.value = '';
    els.btnNew.focus();
  }

  els.btnNew.addEventListener('click', () => openForm(null));
  els.btnCancel.addEventListener('click', closeForm);

  els.form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const payload = {
      course_code: els.code.value.trim(),
      course_name: els.name.value.trim(),
      section: els.section.value.trim(),
      semester: els.semester.value.trim(),
    };
    const blank = Object.entries(payload).filter(([, v]) => !v).map(([k]) => k);
    if (blank.length) {
      els.hint.textContent = 'Fill in every field before saving.';
      const first = { course_code: els.code, course_name: els.name,
                      section: els.section, semester: els.semester }[blank[0]];
      first.focus();
      return;
    }

    const editingId = els.id.value;
    setLoading(els.btnSave, true, 'Saving course');
    try {
      await api(editingId ? `/api/classes/${editingId}` : '/api/classes', {
        method: editingId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      toast(
        `${payload.course_code}-${payload.section} ${editingId ? 'updated' : 'created'}`,
        'success',
      );
      closeForm();
      load();
    } catch (err) {
      els.hint.textContent = err.message;
      toast(err.message, 'error');
    } finally {
      setLoading(els.btnSave, false);
    }
  });

  /* ---- list ------------------------------------------------------------ */
  function rowActions(c) {
    return `
      <a class="btn btn-ghost btn-sm" href="/courses/${c.id}">Roster</a>
      <button class="btn btn-quiet btn-sm" type="button" data-edit="${c.id}">Edit</button>
      <button class="btn btn-quiet btn-sm" type="button" data-delete="${c.id}">Delete</button>`;
  }

  function render(courses) {
    els.count.textContent = `${courses.length} section${courses.length === 1 ? '' : 's'}`;

    if (!courses.length) {
      els.body.innerHTML = '';
      els.cards.innerHTML = '';
      els.table.classList.add('hidden');
      els.empty.classList.remove('hidden');
      return;
    }
    els.table.classList.remove('hidden');
    els.empty.classList.add('hidden');

    els.body.innerHTML = courses.map((c) => `
      <tr>
        <td>
          <strong>${escapeHtml(c.course_code)}</strong>
          <span class="text-3"> ${escapeHtml(c.course_name)}</span>
        </td>
        <td class="mono">${escapeHtml(c.section)}</td>
        <td>${escapeHtml(c.semester)}</td>
        <td class="mono">${c.student_count}</td>
        <td class="shrink"><span class="cluster">${rowActions(c)}</span></td>
      </tr>`).join('');

    els.cards.innerHTML = courses.map((c) => `
      <div class="record-card">
        <span class="rc-name">${escapeHtml(c.course_code)}-${escapeHtml(c.section)}</span>
        <span class="rc-status"><span class="chip chip-plain">${c.student_count} enrolled</span></span>
        <span class="rc-meta">${escapeHtml(c.course_name)} &nbsp;/&nbsp; ${escapeHtml(c.semester)}</span>
        <span class="rc-meta cluster" style="grid-column:1/-1;margin-top:var(--s-2)">${rowActions(c)}</span>
      </div>`).join('');
  }

  let cache = [];

  async function load() {
    try {
      const res = await api('/api/classes');
      cache = res.classes || [];
      render(cache);
    } catch (err) {
      toast(err.message, 'error');
      els.body.innerHTML = '';
      els.table.classList.add('hidden');
      els.cards.replaceChildren(retryState('Could not load your courses.', load));
    }
  }

  /* ---- row actions ----------------------------------------------------- */
  document.addEventListener('click', async (event) => {
    const edit = event.target.closest('[data-edit]');
    if (edit) {
      const course = cache.find((c) => String(c.id) === edit.dataset.edit);
      if (course) openForm(course);
      return;
    }

    const del = event.target.closest('[data-delete]');
    if (!del) return;

    const course = cache.find((c) => String(c.id) === del.dataset.delete);
    if (!course) return;
    const label = `${course.course_code}-${course.section}`;
    /* Deleting a section discards its register too, so the count is named
       explicitly rather than hidden behind a generic "are you sure". */
    const warning = course.student_count
      ? `Delete ${label}?\n\nThis removes its roster of ${course.student_count} student(s) and every attendance record for the section. Student face profiles are kept.`
      : `Delete ${label}?`;
    if (!confirm(warning)) return;

    try {
      await api(`/api/classes/${course.id}`, { method: 'DELETE' });
      toast(`${label} deleted`, 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  load();
})();
