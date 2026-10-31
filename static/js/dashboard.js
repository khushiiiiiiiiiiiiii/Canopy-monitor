const POLL_MS = 5000;

const els = {
  soil: document.getElementById('val-soil'),
  temp: document.getElementById('val-temp'),
  humidity: document.getElementById('val-humidity'),
  predict: document.getElementById('val-predict'),
  predictUnit: document.getElementById('predict-unit'),
  savings: document.getElementById('water-savings'),
  connDot: document.getElementById('connDot'),
  connLabel: document.getElementById('connLabel'),
  alertList: document.getElementById('alertList'),
};

const cards = {
  soil_moisture: document.querySelector('.reading-card[data-metric="soil_moisture"]'),
  temperature: document.querySelector('.reading-card[data-metric="temperature"]'),
  humidity: document.querySelector('.reading-card[data-metric="humidity"]'),
};

function setConn(ok) {
  els.connDot.classList.toggle('live', ok);
  els.connDot.classList.toggle('down', !ok);
  els.connLabel.textContent = ok ? 'Live' : 'Disconnected';
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url + ' failed');
  return res.json();
}

function fmt(n, digits = 1) {
  return (n === null || n === undefined) ? '--' : Number(n).toFixed(digits);
}

function flagCard(card, value, bounds) {
  if (!card || value === null || value === undefined || !bounds) return;
  const out = value < bounds.min || value > bounds.max;
  card.classList.toggle('out-of-range', out);
}

async function updateLatest() {
  const [latest, thresholds] = await Promise.all([
    getJSON('/api/latest'),
    getJSON('/api/thresholds'),
  ]);
  if (!latest) return;
  els.soil.textContent = fmt(latest.soil_moisture);
  els.temp.textContent = fmt(latest.temperature);
  els.humidity.textContent = fmt(latest.humidity);

  flagCard(cards.soil_moisture, latest.soil_moisture, thresholds.soil_moisture);
  flagCard(cards.temperature, latest.temperature, thresholds.temperature);
  flagCard(cards.humidity, latest.humidity, thresholds.humidity);
}

async function updatePrediction() {
  const p = await getJSON('/api/predict');
  if (p.hours_until_watering === null || p.hours_until_watering === undefined) {
    els.predict.textContent = '--';
    els.savings.textContent = 'Estimating savings…';
    return;
  }
  els.predict.textContent = fmt(p.hours_until_watering, 1);
  els.savings.textContent = `rough estimate: ~${fmt(p.water_savings_pct, 0)}% less water than a fixed schedule`;
}

async function updateAlerts() {
  const alerts = await getJSON('/api/alerts');
  els.alertList.innerHTML = '';
  if (!alerts.length) {
    els.alertList.innerHTML = '<li class="alert-empty">No threshold breaches yet.</li>';
    return;
  }
  alerts.forEach(a => {
    const li = document.createElement('li');
    li.className = a.severity;
    const time = new Date(a.ts).toLocaleTimeString();
    li.innerHTML = `${a.message}<span class="alert-time">${time}</span>`;
    els.alertList.appendChild(li);
  });
}

// ---------------------------------------------------------------------
// D3 charts
// ---------------------------------------------------------------------
function makeLineChart(containerId, series) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';
  const width = container.clientWidth || 400;
  const height = container.clientHeight || 190;
  const margin = { top: 10, right: 14, bottom: 22, left: 36 };

  const svg = d3.select(container).append('svg')
    .attr('width', width).attr('height', height);

  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);
  const w = width - margin.left - margin.right;
  const h = height - margin.top - margin.bottom;

  return { svg, g, w, h, margin };
}

function renderSoilChart(history) {
  const chart = makeLineChart('chart-soil');
  if (!history.length) return;

  const x = d3.scaleTime()
    .domain(d3.extent(history, d => new Date(d.ts)))
    .range([0, chart.w]);
  const y = d3.scaleLinear().domain([0, 100]).range([chart.h, 0]);

  chart.g.append('g').attr('class', 'grid')
    .call(d3.axisLeft(y).ticks(4).tickSize(-chart.w).tickFormat(''));

  chart.g.append('g').attr('class', 'axis')
    .attr('transform', `translate(0,${chart.h})`)
    .call(d3.axisBottom(x).ticks(4).tickFormat(d3.timeFormat('%H:%M')));
  chart.g.append('g').attr('class', 'axis')
    .call(d3.axisLeft(y).ticks(4));

  const line = d3.line()
    .x(d => x(new Date(d.ts)))
    .y(d => y(d.soil_moisture))
    .curve(d3.curveMonotoneX);

  const area = d3.area()
    .x(d => x(new Date(d.ts)))
    .y0(chart.h)
    .y1(d => y(d.soil_moisture))
    .curve(d3.curveMonotoneX);

  chart.g.append('path').datum(history)
    .attr('fill', 'rgba(111,207,87,0.12)')
    .attr('d', area);

  chart.g.append('path').datum(history)
    .attr('fill', 'none')
    .attr('stroke', '#6fcf57')
    .attr('stroke-width', 2)
    .attr('d', line);
}

function renderEnvChart(history) {
  const chart = makeLineChart('chart-envc');
  if (!history.length) return;

  const x = d3.scaleTime()
    .domain(d3.extent(history, d => new Date(d.ts)))
    .range([0, chart.w]);
  const yTemp = d3.scaleLinear()
    .domain([10, 35]).range([chart.h, 0]);
  const yHum = d3.scaleLinear()
    .domain([0, 100]).range([chart.h, 0]);
  const humHistory = history.filter(d => d.humidity !== null && d.humidity !== undefined);

  chart.g.append('g').attr('class', 'grid')
    .call(d3.axisLeft(yTemp).ticks(4).tickSize(-chart.w).tickFormat(''));

  chart.g.append('g').attr('class', 'axis')
    .attr('transform', `translate(0,${chart.h})`)
    .call(d3.axisBottom(x).ticks(4).tickFormat(d3.timeFormat('%H:%M')));
  chart.g.append('g').attr('class', 'axis')
    .call(d3.axisLeft(yTemp).ticks(4));
  chart.g.append('g').attr('class', 'axis')
    .attr('transform', `translate(${chart.w},0)`)
    .call(d3.axisRight(yHum).ticks(4));

  const tempLine = d3.line()
    .x(d => x(new Date(d.ts))).y(d => yTemp(d.temperature))
    .curve(d3.curveMonotoneX);
  const humLine = d3.line()
    .x(d => x(new Date(d.ts))).y(d => yHum(d.humidity))
    .curve(d3.curveMonotoneX);

  chart.g.append('path').datum(history)
    .attr('fill', 'none').attr('stroke', '#ffb238').attr('stroke-width', 2)
    .attr('d', tempLine);
  chart.g.append('path').datum(humHistory)
    .attr('fill', 'none').attr('stroke', '#6fb2ff').attr('stroke-width', 1.6)
    .attr('stroke-dasharray', '3,2')
    .attr('d', humLine);

  chart.g.append('text').attr('x', 0).attr('y', -2)
    .attr('fill', '#ffb238').attr('font-size', 10).attr('font-family', 'IBM Plex Mono')
    .text('— temp °C');
  chart.g.append('text').attr('x', 70).attr('y', -2)
    .attr('fill', '#6fb2ff').attr('font-size', 10).attr('font-family', 'IBM Plex Mono')
    .text('- - humidity %');
}

async function updateCharts() {
  const history = await getJSON('/api/history?minutes=60');
  renderSoilChart(history);
  renderEnvChart(history);
}

async function updateAdvice() {
  const a = await getJSON('/api/advice');
  document.getElementById('advice').className = 'advice ' + a.status;
  document.getElementById('advice-title').textContent = a.title;
  document.getElementById('advice-detail').textContent = a.detail;
}

async function tick() {
  try {
    await Promise.all([updateLatest(), updatePrediction(), updateAlerts(), updateCharts(), updateAdvice()]);
    setConn(true);
  } catch (err) {
    console.error(err);
    setConn(false);
  }
}

tick();
setInterval(tick, POLL_MS);
window.addEventListener('resize', () => updateCharts());
