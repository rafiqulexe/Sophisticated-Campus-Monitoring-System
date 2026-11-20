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
    // W2: camera capture, confidence bands, correction
    tabCamera: $('#tab-camera'),
    tabUpload: $('#tab-upload'),
    paneCamera: $('#pane-camera'),
    paneUpload: $('#pane-upload'),
    video: $('#camera-video'),
    cameraStage: $('#camera-stage'),
    cameraIdle: $('#camera-idle'),
    cameraIdleHint: $('#camera-idle-hint'),
    btnCameraStart: $('#btn-camera-start'),
    btnCameraStop: $('#btn-camera-stop'),
    btnSnap: $('#btn-snap'),
    deviceField: $('#camera-device-field'),
    deviceSelect: $('#camera-device'),
    tallyReview: $('#tally-review'),
    resReview: $('#res-review'),
  };

  /* Thresholds come from the server so the bands are defined in one place. */
  let BANDS = { match_threshold: 0.4, review_threshold: 0.6 };
  api('/api/config')
    .then((cfg) => { BANDS = { ...BANDS, ...cfg }; })
    .catch(() => { /* fall back to the defaults above */ });

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


  /* ═══ W2 · capture source tabs ═══════════════════════════════════════ */
  function showSource(which) {
    const camera = which === 'camera';
    els.tabCamera.classList.toggle('is-active', camera);
    els.tabUpload.classList.toggle('is-active', !camera);
    els.tabCamera.setAttribute('aria-selected', String(camera));
    els.tabUpload.setAttribute('aria-selected', String(!camera));
    els.paneCamera.classList.toggle('hidden', !camera);
    els.paneUpload.classList.toggle('hidden', camera);
    if (!camera) stopCamera();
  }

  els.tabCamera.addEventListener('click', () => showSource('camera'));
  els.tabUpload.addEventListener('click', () => showSource('upload'));

  /* ═══ W2 · live camera ═══════════════════════════════════════════════
     getUserMedia needs a secure context: https, or http on localhost.
     Opened over a LAN address it is blocked by the browser, not by us, so
     that case is reported explicitly rather than as a generic failure. */
  let stream = null;

  function secureContextHint() {
    if (window.isSecureContext) return null;
    return `The camera needs a secure connection. This page is on `
      + `${location.protocol}//${location.hostname}, so the browser blocks it. `
      + `Open the app on http://127.0.0.1:5000 or serve it over HTTPS.`;
  }

  function cameraErrorMessage(err) {
    switch (err && err.name) {
      case 'NotAllowedError':
      case 'SecurityError':
        return 'Camera permission was refused. Allow it in the browser address bar, then start the camera again.';
      case 'NotFoundError':
      case 'OverconstrainedError':
        return 'No camera was found on this device.';
      case 'NotReadableError':
        return 'The camera is already in use by another application.';
      default:
        return `Could not start the camera: ${(err && err.message) || 'unknown error'}`;
    }
  }

  async function listCameras() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cams = devices.filter((d) => d.kind === 'videoinput');
    // Labels are only populated once permission has been granted.
    if (cams.length < 2) { els.deviceField.classList.add('hidden'); return; }
    els.deviceSelect.innerHTML = cams.map((c, i) =>
      `<option value="${escapeHtml(c.deviceId)}">${escapeHtml(c.label || `Camera ${i + 1}`)}</option>`).join('');
    els.deviceField.classList.remove('hidden');
  }

  async function startCamera(deviceId) {
    const blocked = secureContextHint();
    if (blocked) { toast(blocked, 'error'); els.cameraIdleHint.textContent = blocked; return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('This browser does not support camera capture.', 'error');
      return;
    }

    setLoading(els.btnCameraStart, true, 'Starting camera');
    try {
      stopCamera();
      stream = await navigator.mediaDevices.getUserMedia({
        video: deviceId
          ? { deviceId: { exact: deviceId } }
          : { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      els.video.srcObject = stream;
      await els.video.play().catch(() => { /* autoplay policies; the frame still arrives */ });

      els.cameraStage.classList.add('is-live');
      els.cameraIdle.classList.add('hidden');
      els.btnCameraStart.classList.add('hidden');
      els.btnCameraStop.classList.remove('hidden');
      els.btnSnap.disabled = false;
      await listCameras();
    } catch (err) {
      const message = cameraErrorMessage(err);
      toast(message, 'error');
      els.cameraIdleHint.textContent = message;
    } finally {
      setLoading(els.btnCameraStart, false);
    }
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    els.video.srcObject = null;
    els.cameraStage.classList.remove('is-live');
    els.cameraIdle.classList.remove('hidden');
    els.btnCameraStart.classList.remove('hidden');
    els.btnCameraStop.classList.add('hidden');
    els.btnSnap.disabled = true;
  }

  /* Grab the current video frame at its native resolution. Downscaling
     happens afterwards in shrinkImage, same as the upload path. */
  function captureFrame() {
    const w = els.video.videoWidth;
    const h = els.video.videoHeight;
    if (!w || !h) return null;
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    canvas.getContext('2d').drawImage(els.video, 0, 0, w, h);
    return canvas.toDataURL('image/jpeg', 0.92);
  }

  els.btnCameraStart.addEventListener('click', () => startCamera(els.deviceSelect.value || null));
  els.btnCameraStop.addEventListener('click', stopCamera);
  els.deviceSelect.addEventListener('change', () => startCamera(els.deviceSelect.value));

  els.btnSnap.addEventListener('click', async () => {
    const frame = captureFrame();
    if (!frame) { toast('The camera has not produced a frame yet.', 'warn'); return; }
    try {
      const image = await shrinkImage(frame);
      processImage(image, els.btnSnap);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // Releasing the camera when the page goes away avoids leaving the
  // recording indicator lit on the user's device.
  window.addEventListener('pagehide', stopCamera);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stopCamera();
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

      // W2: how many matches landed in the review band.
      const review = res.review_count || 0;
      els.tallyReview.classList.toggle('hidden', review === 0);
      els.resReview.textContent = review;

      // Absent tally only means anything when a roster was in play.
      const scoped = res.class_id != null;
      els.tallyAbsent.classList.toggle('hidden', !scoped);
      if (scoped) els.resAbsent.textContent = res.absent_count;

      els.recognizedList.innerHTML = res.recognized.length
        ? `<ul class="roster">${res.recognized.map((r) => {
            const low = r.band === 'review';
            return `
            <li class="${low ? 'is-review' : ''}">
              <span class="check">${low ? ICONS.warn : ICONS.check}</span>
              <span>
                <strong>${escapeHtml(r.name)}</strong>
                <span class="sid">${escapeHtml(r.student_id)}</span>
              </span>
              ${low ? '<span class="chip chip-unknown">Confirm</span>' : ''}
              <span class="dist">d ${escapeHtml(r.distance)}</span>
            </li>`;
          }).join('')}</ul>`
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


  /* ═══ W2 · register cells ════════════════════════════════════════════ */

  /* A distance only means something for a student the system matched.
     Absent and Excused rows carry no measurement, so they show nothing
     rather than a misleading zero. */
  function confidenceCell(r) {
    if (r.manual_override) {
      return '<span class="conf conf-manual">set by teacher</span>';
    }
    if (r.confidence_score == null) return '<span class="text-3">—</span>';
    const d = Number(r.confidence_score);
    const band = d <= BANDS.match_threshold ? 'high'
      : d <= BANDS.review_threshold ? 'review' : 'low';
    const label = { high: 'High', review: 'Low', low: 'Very low' }[band];
    return `<span class="conf conf-${band}" title="cosine distance ${d.toFixed(4)}">`
      + `<i></i>${label} <span class="mono">${d.toFixed(2)}</span></span>`;
  }

  function statusChip(r) {
    const cls = { Present: 'chip-present', Absent: 'chip-absent', Excused: 'chip-info' };
    const chip = cls[r.status] || 'chip-plain';
    const flag = r.needs_review
      ? ' <span class="chip chip-unknown" title="Matched near the boundary">Confirm</span>'
      : '';
    return `<span class="chip ${chip}">${escapeHtml(r.status || 'Present')}</span>${flag}`;
  }

  /* One click per status. The control for the status a row already holds
     is marked current and disabled, so the buttons double as a readout. */
  function correctionButtons(r, classId) {
    if (!classId) return '<span class="text-3">—</span>';
    const sid = escapeHtml(r.student_id);
    return '<span class="correct">' + ['Present', 'Absent', 'Excused'].map((st) => {
      const current = r.status === st;
      return `<button class="btn btn-quiet btn-sm${current ? ' is-current' : ''}"`
        + ` type="button" data-set-status="${st}" data-student="${sid}"`
        + `${current ? ' disabled aria-current="true"' : ''}`
        + ` title="Mark ${escapeHtml(r.name || sid)} ${st.toLowerCase()}">${st[0]}</button>`;
    }).join('') + '</span>';
  }

  /* Delegated so it survives every re-render of the table. */
  els.logBody.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-set-status]');
    if (!btn || btn.disabled) return;
    const classId = selectedClassId();
    if (!classId) { toast('Choose a course section first.', 'warn'); return; }

    setLoading(btn, true, 'Saving');
    try {
      await api('/api/attendance/status', {
        method: 'POST',
        body: JSON.stringify({
          student_id: btn.dataset.student,
          status: btn.dataset.setStatus,
          class_id: Number(classId),
        }),
      });
      toast(`${btn.dataset.student} marked ${btn.dataset.setStatus.toLowerCase()}`, 'success');
      loadLog();
    } catch (err) {
      toast(err.message, 'error');
      setLoading(btn, false);
    }
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
        ? records.map((r) => `
          <tr class="${r.needs_review ? 'row-review' : ''}">
            <td><strong>${escapeHtml(r.name || 'Unknown student')}</strong></td>
            <td class="mono">${escapeHtml(r.student_id)}</td>
            <td class="mono">${escapeHtml(r.status === 'Present' ? ((r.timestamp || '').split(' ')[1] || '—') : '—')}</td>
            <td>${confidenceCell(r)}</td>
            <td>${statusChip(r)}</td>
            <td class="shrink">${correctionButtons(r, classId)}</td>
          </tr>`).join('')
        : `<tr><td colspan="6">${emptyState(
             'Register is empty',
             classId
               ? 'Nothing recorded for this section today. Scan the room, or close the register to mark everyone absent.'
               : 'Nobody has been marked present today yet.',
             'camera',
           )}</td></tr>`;
    } catch (err) {
      els.logBody.innerHTML = '<tr><td colspan="6"></td></tr>';
      els.logBody.querySelector('td').appendChild(
        retryState('Could not load the register.', loadLog),
      );
      toast(err.message, 'error');
    }
  }

  loadClasses().then(loadLog);
})();
