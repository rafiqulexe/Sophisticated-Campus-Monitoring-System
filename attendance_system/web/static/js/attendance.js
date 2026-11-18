'use strict';

(function initAttendance() {
  const els = {
    classSelect: $('#class-select'),
    classHint: $('#class-hint'),
    tallyAbsent: $('#tally-absent'),
    resAbsent: $('#res-absent'),
    logAbsent: $('#log-absent'),
    btnCloseRegister: $('#btn-close-register'),
    file: $('#att-file'),
    dropzone: $('#att-dropzone'),
    dropTitle: $('#att-drop-title'),
    dropHint: $('#att-drop-hint'),
    scanBar: $('#att-scan-bar'),
    fileList: $('#att-file-list'),
    btnAttendUpload: $('#btn-attend-upload'),
    btnNewScan: $('#btn-new-scan'),
    resultEmpty: $('#result-empty'),
    resultContent: $('#result-content'),
    resultScanning: $('#result-scanning'),
    scanStages: $('#scan-stages'),
    resultImg: $('#result-img'),
    resFaces: $('#res-faces'),
    resRecognized: $('#res-recognized'),
    resUnknown: $('#res-unknown'),
    recognizedList: $('#recognized-list'),
    unknownNote: $('#unknown-note'),
    logBody: $('#log-body'),
    logCount: $('#log-count'),
  };

  /* ---- course sections (W1) --------------------------------------------
     The chosen section scopes recognition to its roster and switches on
     absentee resolution. Deep links from a course page preselect it. */
  const selectedClassId = () => els.classSelect.value || null;

  async function loadClasses() {
    const preset = new URLSearchParams(location.search).get('class_id');
    try {
      const res = await api('/api/classes');
      const classes = res.classes || [];

      if (!classes.length) {
        els.classSelect.innerHTML = '<option value="">No sections yet</option>';
        els.classSelect.disabled = true;
        els.classHint.innerHTML =
          'No course sections exist yet. <a href="/courses">Create one</a> to record attendance '
          + 'against a roster — otherwise the scan is saved without a section and nobody is marked absent.';
        return;
      }

      els.classSelect.disabled = false;
      els.classSelect.innerHTML =
        '<option value="">No section — record present only</option>'
        + classes.map((c) =>
            `<option value="${c.id}">${escapeHtml(c.course_code)}-${escapeHtml(c.section)}`
            + ` · ${escapeHtml(c.course_name)} (${c.student_count})</option>`).join('');

      if (preset && classes.some((c) => String(c.id) === preset)) {
        els.classSelect.value = preset;
      }
      syncClassHint();
    } catch (err) {
      els.classSelect.innerHTML = '<option value="">Could not load sections</option>';
      toast(err.message, 'error');
    }
  }

  function syncClassHint() {
    const id = selectedClassId();
    els.classHint.textContent = id
      ? 'Recognition is limited to this roster; everyone else on it is marked absent.'
      : 'Without a section, recognised students are recorded but no absentees are calculated.';
    els.btnCloseRegister.classList.toggle('hidden', !id);
    loadLog();
  }

  els.classSelect.addEventListener('change', syncClassHint);

  /* Close the register manually when the class was never scanned, or when
     latecomers were added after the fact. */
  els.btnCloseRegister.addEventListener('click', async () => {
    const classId = selectedClassId();
    if (!classId) return;
    if (!confirm('Close today\'s register?\n\nEveryone on the roster who has not been recognised will be marked absent.')) return;

    setLoading(els.btnCloseRegister, true, 'Closing register');
    try {
      const res = await api('/api/attendance/resolve', {
        method: 'POST',
        body: JSON.stringify({ class_id: Number(classId) }),
      });
      toast(res.marked_absent
        ? `Marked ${res.marked_absent} student${res.marked_absent === 1 ? '' : 's'} absent`
        : 'Register was already complete', 'success');
      loadLog();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setLoading(els.btnCloseRegister, false);
    }
  });

  /* ---- staged progress -------------------------------------------------
     The API is a single call, so stages are advanced on our side: upload
     completes when the request goes out, detection is assumed once the
     server has the bytes, and matching runs until the response lands. */
  let stageTimer = null;

  function setStage(n) {
    $$('.scan-stage', els.scanStages).forEach((li) => {
      const value = parseInt(li.dataset.stage, 10);
      li.classList.toggle('is-done', value < n);
      li.classList.toggle('is-active', value === n);
    });
  }

  function startStages() {
    setStage(1);
    clearTimeout(stageTimer);
    stageTimer = setTimeout(() => {
      setStage(2);
      stageTimer = setTimeout(() => setStage(3), 900);
    }, 500);
  }

  function stopStages() {
    clearTimeout(stageTimer);
    setStage(4);
  }

  /* ---- processing ------------------------------------------------------ */
  async function processImage(image, button) {
    els.resultEmpty.classList.add('hidden');
    els.resultContent.classList.add('hidden');
    els.resultScanning.classList.remove('hidden');
    startStages();
    setLoading(button, true, 'Running recognition');

    try {
      const classId = selectedClassId();
      const res = await api('/api/attendance', {
        method: 'POST',
        body: JSON.stringify({
          image,
          class_id: classId ? Number(classId) : null,
        }),
      });
      stopStages();

      els.resFaces.textContent = res.faces_detected;
      els.resRecognized.textContent = res.recognized.length;
      els.resUnknown.textContent = res.unknown_count;
      els.resultImg.src = res.image;

      // Absent tally only means anything when a roster was in play.
      const scoped = res.class_id != null;
      els.tallyAbsent.classList.toggle('hidden', !scoped);
      if (scoped) els.resAbsent.textContent = res.absent_count;

      els.recognizedList.innerHTML = res.recognized.length
        ? `<ul class="roster">${res.recognized.map((r) => `
            <li>
              <span class="check">${ICONS.check}</span>
              <span>
                <strong>${escapeHtml(r.name)}</strong>
                <span class="sid">${escapeHtml(r.student_id)}</span>
              </span>
              <span class="dist">d ${escapeHtml(r.distance)}</span>
            </li>`).join('')}</ul>`
        : emptyState(
            'No enrolled student matched',
            'Faces were found but none matched an enrolled profile. Check that the students are registered and clearly visible.',
            'warn',
          );

      els.unknownNote.classList.toggle('hidden', res.unknown_count === 0);
      els.resultScanning.classList.add('hidden');
      els.resultContent.classList.remove('hidden');
      els.btnNewScan.classList.remove('hidden');

      if (res.recognized.length) {
        const tail = scoped ? ` · ${res.absent_count} absent of ${res.roster_size}` : '';
        toast(`Marked ${res.recognized.length} present${tail}`, 'success');
      } else if (res.faces_detected === 0) {
        toast('No faces were detected in this photo.', 'warn');
      }
      loadLog();
    } catch (err) {
      stopStages();
      els.resultScanning.classList.add('hidden');
      els.resultEmpty.classList.remove('hidden');
      toast(err.message, 'error');
    } finally {
      setLoading(button, false);
    }
  }

  /* ---- file selection -------------------------------------------------- */
  async function renderSelectedFile() {
    const file = els.file.files[0];
    els.btnAttendUpload.disabled = !file;

    if (!file) {
      els.dropzone.classList.remove('selected');
      els.dropzone.classList.remove('hidden');
      els.scanBar.classList.add('hidden');
      els.dropTitle.textContent = 'Choose a classroom photo';
      els.dropHint.innerHTML = 'JPG or PNG &nbsp;/&nbsp; drag and drop is supported';
      els.fileList.innerHTML = '';
      return;
    }

    let thumb = '';
    try {
      thumb = await shrinkImage(await readFileAsDataUrl(file), 140, 0.8);
    } catch (_) { /* preview is best-effort; processing surfaces real errors */ }

    /* Once a photo is chosen the dropzone steps aside for the result. */
    els.dropzone.classList.add('hidden');
    els.scanBar.classList.remove('hidden');
    els.fileList.innerHTML = `
      <li>
        ${thumb ? `<img class="file-thumb" src="${thumb}" alt="">` : ''}
        <span class="name">${escapeHtml(file.name)}</span>
        <span class="size">${formatSize(file.size)}</span>
        <button class="btn btn-quiet btn-sm" type="button" id="btn-clear-file">Change</button>
      </li>`;

    const clear = $('#btn-clear-file');
    if (clear) {
      clear.addEventListener('click', () => {
        els.file.value = '';
        renderSelectedFile();
      });
    }
  }

  els.file.addEventListener('change', renderSelectedFile);

  /* ---- drag and drop --------------------------------------------------- */
  ['dragenter', 'dragover'].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.add('dragging');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove('dragging');
    });
  });
  els.dropzone.addEventListener('drop', (event) => {
    const dropped = event.dataTransfer && event.dataTransfer.files;
    if (!dropped || !dropped.length) return;
    if (!/^image\/(jpeg|png)$/.test(dropped[0].type)) {
      toast('Only JPG and PNG photos are supported.', 'warn');
      return;
    }
    els.file.files = dropped;
    renderSelectedFile();
  });

  /* ---- run ------------------------------------------------------------- */
  els.btnAttendUpload.addEventListener('click', async () => {
    const file = els.file.files[0];
    if (!file) return;
    try {
      const image = await shrinkImage(await readFileAsDataUrl(file));
      processImage(image, els.btnAttendUpload);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  els.btnNewScan.addEventListener('click', () => {
    els.resultContent.classList.add('hidden');
    els.resultEmpty.classList.remove('hidden');
    els.btnNewScan.classList.add('hidden');
    els.file.value = '';
    renderSelectedFile();
    els.dropzone.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  /* ---- log ------------------------------------------------------------- */
  async function loadLog() {
    try {
      const classId = selectedClassId();
      const res = await api(
        `/api/attendance/records${classId ? `?class_id=${encodeURIComponent(classId)}` : ''}`,
      );
      const records = res.records || [];
      const present = records.filter((r) => r.status === 'Present');
      const absent = records.filter((r) => r.status === 'Absent');

      els.logCount.textContent = `${present.length} present`;
      els.logAbsent.classList.toggle('hidden', absent.length === 0);
      els.logAbsent.textContent = `${absent.length} absent`;

      els.logBody.innerHTML = records.length
        ? records.map((r) => {
            const isPresent = r.status === 'Present';
            const chip = isPresent ? 'chip-present' : 'chip-absent';
            return `
          <tr>
            <td><strong>${escapeHtml(r.name || 'Unknown student')}</strong></td>
            <td class="mono">${escapeHtml(r.student_id)}</td>
            <td class="mono">${escapeHtml(isPresent ? ((r.timestamp || '').split(' ')[1] || '—') : '—')}</td>
            <td><span class="chip ${chip}">${escapeHtml(r.status || 'Present')}</span></td>
          </tr>`;
          }).join('')
        : `<tr><td colspan="4">${emptyState(
             'Register is empty',
             classId
               ? 'Nothing recorded for this section today. Scan the room, or close the register to mark everyone absent.'
               : 'Nobody has been marked present today yet.',
             'camera',
           )}</td></tr>`;
    } catch (err) {
      els.logBody.innerHTML = '<tr><td colspan="4"></td></tr>';
      els.logBody.querySelector('td').appendChild(
        retryState('Could not load the register.', loadLog),
      );
      toast(err.message, 'error');
    }
  }

  loadClasses().then(loadLog);
})();
