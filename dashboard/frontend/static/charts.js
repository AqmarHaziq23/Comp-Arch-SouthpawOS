/* Chart helpers. Chart.js is loaded by loadLib() in base.html. */
var COLORS = {ink: '#13201b', orange: '#e8873a', green: '#1e7a55', red: '#b5342a', grid: '#d9dfd7'};

function chartsReady(ids) {
  if (typeof Chart !== 'undefined') return true;
  ids.forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.parentNode.innerHTML = '<p class="empty">Charts need Chart.js. Run fetch_vendor.sh once while online.</p>';
  });
  return false;
}

function drawChart(id, type, labels, datasets, yTitle, yOpts) {
  var el = document.getElementById(id);
  if (!el) return;
  if (!labels.length) { el.parentNode.innerHTML = '<p class="empty">Not enough data yet.</p>'; return; }
  new Chart(el, {
    type: type,
    data: {labels: labels, datasets: datasets},
    options: {
      responsive: true, maintainAspectRatio: false, spanGaps: true,
      plugins: {legend: {display: datasets.length > 1}},
      scales: {
        x: {grid: {display: false}},
        y: Object.assign({grid: {color: COLORS.grid}, title: {display: !!yTitle, text: yTitle}}, yOpts || {})
      }
    }
  });
}

function ds(label, data, color, extra) {
  return Object.assign({label: label, data: data, borderColor: color, backgroundColor: color,
    borderWidth: 3, pointRadius: 4, tension: 0.25}, extra || {});
}
