/* Chart helpers. Chart.js is loaded by loadLib() in base.html. */
var COLORS = {ink: '#13201b', orange: '#e8873a', green: '#1e7a55', red: '#b5342a', grid: '#d9dfd7',
              muted: '#5d6b63', panel: '#fafbf8',
              landed: '#2a78d6', missed: '#eb6834'};   /* a blue/orange pair that stays apart for colour-blind readers */

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

/* The two charts of one round (templates/_round.html).
   round = {by_type: [{name, landed, missed, thrown}], punches: [{t, peak_g, landed, hand_name, name, impact}], duration_s} */
function roundCharts(round) {
  var legend = {position: 'bottom', labels: {usePointStyle: true, color: COLORS.ink}};

  /* Landed + missed stacked per punch type: the bar's height is how many were thrown. */
  new Chart(document.getElementById('byType'), {
    type: 'bar',
    data: {
      labels: round.by_type.map(function (t) { return t.name; }),
      datasets: [
        {label: 'Landed', data: round.by_type.map(function (t) { return t.landed; }), backgroundColor: COLORS.landed},
        {label: 'Missed', data: round.by_type.map(function (t) { return t.missed; }), backgroundColor: COLORS.missed}
      ].map(function (d) {
        return Object.assign(d, {borderColor: COLORS.panel, borderWidth: 2, borderRadius: 4, maxBarThickness: 56});
      })
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: legend,
        tooltip: {callbacks: {footer: function (items) {
          var t = round.by_type[items[0].dataIndex];
          return t.thrown + ' thrown, ' + t.accuracy + '% landed';
        }}}
      },
      scales: {
        x: {stacked: true, grid: {display: false}},
        y: {stacked: true, beginAtZero: true, ticks: {precision: 0}, grid: {color: COLORS.grid},
            title: {display: true, text: 'punches'}}
      }
    }
  });

  /* One mark per punch: when it was thrown against how hard. Shape repeats what colour says. */
  var timed = round.punches.filter(function (p) { return p.t !== null && p.peak_g !== null; });
  var el = document.getElementById('timeline');
  if (!timed.length) { el.parentNode.innerHTML = '<p class="empty">The punches were uploaded without times or peak g.</p>'; return; }
  function series(label, landed, color, shape) {
    return {label: label, backgroundColor: color, borderColor: COLORS.panel, borderWidth: 2,
            pointStyle: shape, pointRadius: 7, pointHoverRadius: 9,
            data: timed.filter(function (p) { return !!p.landed === landed; })
                       .map(function (p) { return {x: p.t, y: p.peak_g, p: p}; })};
  }
  new Chart(el, {
    type: 'scatter',
    data: {datasets: [series('Landed', true, COLORS.landed, 'circle'), series('Missed', false, COLORS.missed, 'triangle')]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: legend,
        tooltip: {callbacks: {label: function (item) {
          var p = item.raw.p;
          return p.hand_name + ' ' + p.name.toLowerCase() + ' at ' + p.t.toFixed(1) + ' s: ' + p.peak_g + ' g, ' +
                 (p.landed ? 'landed' + (p.impact !== null ? ' (bag impact ' + p.impact + '/100)' : '') : 'missed');
        }}}
      },
      scales: {
        x: {min: 0, max: round.duration_s || undefined, grid: {display: false},
            title: {display: true, text: 'seconds into the round'}},
        y: {beginAtZero: true, grid: {color: COLORS.grid}, title: {display: true, text: 'peak g'}}
      }
    }
  });
}
