'use strict';
/* Offline jobs download no API token/credentials. Never executes a DLL or NX. */
globalThis.NXGeometry = (() => {
  const el = id => document.getElementById(id);
  let selected = null, selectedJob = null;
  const run = fn => async () => { try { await fn(); } catch (e) { message(e.message, true); } };
  async function loadAttachments() {
    selected = resources.find(r => r.id === el('step-resource').value) || null;
    selectedJob = null;
    const attachments = selected ? await Promise.all((selected.asset_ids || []).map(id => api('assets/' + id + '/metadata'))) : [];
    options(el('step-asset'), Object.fromEntries(attachments.filter(a => /\.(step|stp|p21)$/i.test(a.filename)).map(a => [a.id, a.filename])));
    await loadJobs();
  }
  async function loadJobs() {
    el('geometry-jobs').replaceChildren();
    if (!selected) return;
    const jobs = await api('geometry-jobs?resource_id=' + encodeURIComponent(selected.id));
    jobs.forEach(job => {
      const tr = document.createElement('tr');
      cell(tr, job.id.slice(0, 8) + ' / Rev. ' + job.resource_revision);
      cell(tr, job.input.filename); cell(tr, job.status);
      const td = cell(tr, '');
      td.append(button('Importpaket herunterladen', () => download(job)), button('Bericht zuordnen', () => {
        selectedJob = job;
        el('geometry-report-state').textContent = 'Bericht für Auftrag ' + job.id + ' wählen.';
        details(job);
      }), button('Details', () => details(job)));
      el('geometry-jobs').append(tr);
    });
  }
  async function download(job) {
    const response = await fetch('/api/v1/geometry-jobs/' + job.id + '/package', {headers: {Authorization: 'Bearer ' + token}});
    if (!response.ok) throw new Error((await response.json()).detail);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'NX_STEP_' + job.id + '.zip'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message('Paket herunterladen und LIESMICH.txt lesen. NX wird nicht automatisch gestartet.');
  }
  el('step-resource').onchange = run(loadAttachments);
  el('step-preview').onclick = run(async () => {
    if (!el('step-asset').value) throw new Error('Gespeichertes Werkzeug mit STEP-Anhang wählen.');
    details(await api('assets/' + el('step-asset').value + '/step-preview'));
    message('STEP-Text geprüft. Einheiten, Körper und CAM-Eignung sind dadurch nicht bestätigt.');
  });
  el('step-job-create').onclick = run(async () => {
    if (!selected || !el('step-asset').value) throw new Error('Gespeicherten Werkzeugentwurf mit STEP-Anhang wählen.');
    const job = await api('geometry-jobs', 'POST', {resource_id: selected.id, expected_revision: selected.revision,
      asset_id: el('step-asset').value, tool_type: el('step-tool-type').value});
    details(job); await loadJobs();
    message('Auftrag angelegt. Importpaket herunterladen; Modell bleibt Entwurf, kein CAM-Export.');
  });
  el('geometry-report-upload').onclick = run(async () => {
    if (!selectedJob) throw new Error('Zuerst beim Auftrag „Bericht zuordnen“ wählen.');
    const file = el('geometry-report-file').files[0];
    if (!file || file.size > 1024 * 1024) throw new Error('output/report.json mit maximal 1 MB wählen.');
    const job = await api('geometry-jobs/' + selectedJob.id + '/report', 'POST', {report: JSON.parse(await file.text())});
    details(job); await loadJobs();
    el('geometry-report-state').textContent = 'Diagnose zugeordnet. Keine automatische Übernahme/Freigabe von CAM-Daten oder PRT-Dateien.';
  });
  return {
    async refresh(capabilities) {
      if (capabilities) options(el('step-tool-type'), capabilities.geometry_preparation.tool_types);
      options(el('step-resource'), Object.fromEntries(resources.filter(r => ['tool', 'tool_assembly'].includes(r.kind) && r.cam_status === 'draft').map(r => [r.id, r.name])));
      await loadAttachments();
    },
    reset() { selected = selectedJob = null; el('geometry-jobs').replaceChildren(); el('step-resource').replaceChildren(); el('step-asset').replaceChildren(); el('geometry-report-file').value = ''; el('geometry-report-state').textContent = ''; }
  };
})();
