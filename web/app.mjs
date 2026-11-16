const $ = id => document.getElementById(id);
const fmt = new Intl.NumberFormat('en-CA');
const source = 'https://lobbycanada.gc.ca/app/secure/ocl/lrs/do/cmmLgPblcVw?comlogId=';
const perPage = 15;
const shards = new Map();
let index, entries = [], current = null, currentRows = [], filtered = [];
let organizationsShown = 14, page = 1, selection = 0;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function option(label, value) {
  const el = node('option', label);
  el.value = value;
  return el;
}
function normalize(s) {
  return String(s).normalize('NFD').replace(/\p{Mark}/gu, '').toLowerCase().replace(/[^\p{Letter}\p{Number}]+/gu, ' ').trim();
}
function filterCode(field) { return $(field).value === '' ? null : Number($(field).value); }
async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}

function saveLink() {
  const params = new URLSearchParams();
  if (current) params.set('org', current[0]);
  for (const field of ['query', 'institution', 'subject', 'year']) if ($(field).value) params.set(field, $(field).value);
  const hash = params.toString();
  history.replaceState(null, '', hash ? '#' + hash : location.pathname);
}

function drawList() {
  const tokens = normalize($('query').value).split(/\s+/).filter(Boolean);
  const institution = filterCode('institution'), subject = filterCode('subject');
  const found = entries.filter(({ row, search }) => tokens.every(t => search.includes(t)) &&
    (institution === null || row[4].includes(institution)) && (subject === null || row[5].includes(subject)));
  $('list-status').textContent = fmt.format(found.length) + ' organizations';
  const children = found.slice(0, organizationsShown).map(({ row }) => {
    const button = node('button');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(current?.[0] === row[0]));
    button.append(node('span', row[1]), node('span', fmt.format(row[2]), 'metadata'));
    button.addEventListener('click', () => openOrg(row, '', true));
    return button;
  });
  $('organizations').replaceChildren(...children);
  $('more-orgs').hidden = organizationsShown >= found.length;
  saveLink();
}

function bars(id, grouping, fallback) {
  const max = Math.max(1, ...grouping.map(x => x[1]));
  const rows = grouping.map(([label, count]) => {
    const item = node('div', undefined, 'bar-item');
    const bar = node('span', undefined, 'bar');
    const fill = node('i');
    fill.style.width = (count / max * 100) + '%';
    bar.setAttribute('aria-hidden', 'true');
    bar.append(fill);
    item.append(node('span', label), bar, node('span', fmt.format(count)));
    return item;
  });
  $(id).replaceChildren(...(rows.length ? rows : [node('p', fallback, 'muted')]));
}

function tally(rows, col, names) {
  const counts = new Map();
  for (const row of rows) for (const id of row[col]) counts.set(id, (counts.get(id) || 0) + 1);
  return [...counts].sort((a,b) => b[1] - a[1]).slice(0, 10).map(([id, count]) => [names[id], count]);
}

function metric(value, text) {
  const el = node('div', undefined, 'metric');
  el.append(node('strong', fmt.format(value)), node('span', text));
  return el;
}

function drawReports() {
  if (!current || $('report-view').hidden) return;
  const institution = filterCode('institution'), subject = filterCode('subject'), year = $('year').value;
  const byFacet = currentRows.filter(r => (institution === null || r[2].includes(institution)) && (subject === null || r[3].includes(subject)));
  filtered = byFacet.filter(r => !year || r[0].startsWith(year));
  const years = new Map();
  for (const r of byFacet) {
    const y = r[0].slice(0, 4);
    years.set(y, (years.get(y) || 0) + 1);
  }
  bars('history', [...years].sort((a, b) => a[0].localeCompare(b[0])), 'No reports for these filters.');
  bars('institutions', tally(filtered, 2, index.institutions), 'No named institutions for these reports.');
  bars('subjects', tally(filtered, 3, index.subjects), 'No listed subjects for these reports.');
  $('report-status').textContent = fmt.format(filtered.length) + ' report' + (filtered.length === 1 ? '' : 's') + ' match.';
  $('metrics').replaceChildren(metric(filtered.length,'Reports'), metric(new Set(filtered.flatMap(r => r[2])).size,'Institutions'), metric(new Set(filtered.flatMap(r => r[3])).size,'Subject areas'));
  const pages = Math.max(1, Math.ceil(filtered.length / perPage));
  page = Math.min(pages, Math.max(1, page));
  $('rows').replaceChildren(...filtered.slice((page - 1) * perPage, page * perPage).map(row => {
    const tr = node('tr'), data = node('td'), official = node('td');
    const link = node('a', '#' + row[1] + ' ↗');
    link.href = source + row[1];
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    const i = row[2].map(x => index.institutions[x]).join(' · ');
    const s = row[3].map(x => index.subjects[x]).join(' · ');
    data.append(node('div', i || 'Institution unavailable'));
    if (s) data.append(node('small', s));
    official.append(link);
    tr.append(node('td', row[0]), data, official);
    return tr;
  }));
  $('previous').disabled = page === 1;
  $('next').disabled = page === pages;
  $('paging').hidden = pages <= 1;
  $('page-status').textContent = `Page ${page} of ${pages}`;
  $('export').disabled = !filtered.length;
  saveLink();
}

async function openOrg(row, savedYear = '', jump = false) {
  const request = ++selection;
  current = row;
  currentRows = [];
  page = 1;
  $('report-view').hidden = true;
  $('placeholder').hidden = false;
  $('placeholder').textContent = 'Opening reports for ' + row[1] + '…';
  drawList();
  try {
    const bucket = row[0] % index.buckets;
    if (!shards.has(bucket)) shards.set(bucket, await json('./data/' + bucket + '.json'));
    if (request !== selection) return;
    currentRows = shards.get(bucket)[row[0]];
    if (!Array.isArray(currentRows) || !currentRows.length) throw new Error('Report shard is incomplete');
  } catch {
    if (request === selection) $('placeholder').textContent = 'Could not load these reports. Select the organization again to retry.';
    return;
  }
  $('org-title').textContent = row[1];
  $('report-label').textContent = 'Registry organization ID ' + row[0];
  const years = new Set(currentRows.map(r => r[0].slice(0, 4)));
  $('year').replaceChildren(option('All years', ''), ...[...years].map(y => option(y, y)));
  $('year').value = years.has(savedYear) ? savedYear : '';
  $('placeholder').hidden = true;
  $('report-view').hidden = false;
  drawReports();
  if (jump) $('org-title').focus({preventScroll: window.matchMedia('(min-width: 851px)').matches});
}

function escapeCSV(value) {
  const text = String(value);
  return '"' + (/^[=+\-@\t\r]/.test(text) ? "'" : '') + text.replaceAll('"', '""') + '"';
}

function exportCSV() {
  const header = ['organization', 'communication_date', 'report_id', 'institutions', 'subjects', 'source'];
  const data = filtered.map(r => [current[1], r[0], r[1], r[2].map(i => index.institutions[i]).join('; '), r[3].map(i => index.subjects[i]).join('; '), source + r[1]]);
  const blob = new Blob([[header, ...data].map(row => row.map(escapeCSV).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = node('a'); a.href = url; a.download = 'lobby-trail-' + current[0] + '.csv'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

async function init() {
  try {
    index = await json('./index.json');
    entries = index.entries.map(row => ({ row, search: normalize([row[0], row[1], ...row[3]].join(' ')) }));
    $('coverage').textContent = `${fmt.format(index.reports)} reports · ${index.from} to ${index.to} · Source export ${index.sourceDate}`;
    $('snapshot-note').hidden = index.snapshotKind !== 'bundled';
    for (const [field, labels] of [['institution',index.institutions],['subject',index.subjects]]) {
      const used = new Set(index.entries.flatMap(r => r[field === 'institution' ? 4 : 5]));
      const opts = labels.map((name, id) => [name, id]).filter(([,id]) => used.has(id)).sort((a, b) => a[0].localeCompare(b[0]));
      $(field).append(...opts.map(([name, id]) => option(name, id)));
    }
  } catch {
    $('coverage').textContent = 'The local index could not be opened. Use the official open-data link or reload the page.';
    return;
  }
  const hash = new URLSearchParams(location.hash.slice(1));
  const savedYear = hash.get('year') || '';
  for (const field of ['query','institution','subject']) if (hash.has(field)) {
    $(field).value = hash.get(field).slice(0,120);
    if ($(field).value === null) $(field).value = '';
  }
  $('query').addEventListener('input', () => { organizationsShown = 14; drawList(); });
  for (const field of ['institution', 'subject']) $(field).addEventListener('change', () => { organizationsShown = 14; page = 1; drawList(); drawReports(); });
  $('more-orgs').addEventListener('click', () => { organizationsShown += 14; drawList(); });
  for (const button of document.querySelectorAll('[data-query]')) button.addEventListener('click', () => {
    $('query').value = button.dataset.query; organizationsShown = 14; drawList();
  });
  $('year').addEventListener('change', () => { page = 1; drawReports(); });
  $('previous').addEventListener('click', () => { page--; drawReports(); });
  $('next').addEventListener('click', () => { page++; drawReports(); });
  $('export').addEventListener('click', exportCSV);
  $('share').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); $('report-status').textContent = 'Link copied.'; }
    catch { $('report-status').textContent = 'Copy the URL from your browser’s address bar.'; }
  });
  const select = entries.find(e => e.row[0] === Number(hash.get('org')));
  if (select) openOrg(select.row, savedYear);
  else drawList();
}
init();
