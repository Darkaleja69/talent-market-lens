/**
 * charts.js — pure ECharts option builders for the public dashboard.
 *
 * Pure ES module: it does not import ECharts, does not touch the DOM and holds
 * no business logic. The caller (app.js) passes already-fetched data, already
 * localised labels and formatting callbacks; each function returns a plain
 * ECharts `option` object that app.js mounts with `state.echarts.init()`.
 * Identifiers and comments are in English (constitution 6); every user-facing
 * string travels through i18n.js.
 */

/** Fallback label/tooltip formatter: plain string conversion, never `undefined`. */
const defaultFormat = (value) => String(value ?? '');

/** Shared grid with room for axis labels on every side. */
const chartGrid = { left: 8, right: 16, top: 16, bottom: 8, containLabel: true };

/**
 * Axis-trigger tooltip whose values go through the caller's formatter.
 * `confine: true` keeps long tooltips inside the chart canvas (T-21): they are
 * never clipped by the viewport edge.
 */
const axisTooltip = (formatValue) => ({
  trigger: 'axis',
  confine: true,
  valueFormatter: (value) => formatValue(value),
});

/**
 * Builds a single-series line chart option.
 * @param {Object} params
 * @param {string[]} params.categories - ordered x-axis labels (already localised).
 * @param {number[]} params.values - data points aligned with `categories`.
 * @param {string} [params.seriesName] - series name used by the tooltip.
 * @param {(value: number) => string} [params.formatValue] - axis/tooltip formatter.
 * @param {boolean} [params.rotateLabels] - rotate dense category labels 45°.
 * @returns {Object} ECharts option.
 */
export function lineChart({
  categories,
  values,
  seriesName = '',
  formatValue = defaultFormat,
  rotateLabels = false,
}) {
  return {
    animationDuration: 250,
    grid: chartGrid,
    tooltip: axisTooltip(formatValue),
    xAxis: {
      type: 'category',
      data: categories,
      boundaryGap: false,
      axisLabel: { hideOverlap: true, rotate: rotateLabels ? 45 : 0 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { formatter: (value) => formatValue(value) },
    },
    series: [{
      name: seriesName,
      type: 'line',
      data: values,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { width: 2 },
    }],
  };
}

/**
 * Builds a single-series bar chart option, vertical (default) or horizontal.
 * Horizontal bars render the first category on top (`inverse`), which is the
 * order used by the ranking queries (largest first).
 * @param {Object} params
 * @param {string[]} params.categories - category-axis labels (already localised).
 * @param {number[]} params.values - values aligned with `categories`.
 * @param {string} [params.seriesName] - series name used by the tooltip.
 * @param {boolean} [params.horizontal] - `true` for horizontal bars.
 * @param {(value: number) => string} [params.formatValue] - axis/tooltip formatter.
 * @returns {Object} ECharts option.
 */
export function barChart({
  categories,
  values,
  seriesName = '',
  horizontal = false,
  formatValue = defaultFormat,
}) {
  const valueAxis = {
    type: 'value',
    minInterval: 1,
    axisLabel: { hideOverlap: true, formatter: (value) => formatValue(value) },
  };
  const categoryAxis = {
    type: 'category',
    data: categories,
    axisLabel: { hideOverlap: true },
  };
  return {
    animationDuration: 250,
    grid: chartGrid,
    tooltip: { ...axisTooltip(formatValue), axisPointer: { type: 'shadow' } },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: [{
      name: seriesName,
      type: 'bar',
      data: values,
      barMaxWidth: 22,
    }],
  };
}

/**
 * Builds a donut (pie) chart option with direct labels.
 * @param {Object} params
 * @param {Array<{name: string, value: number}>} params.items - slices (already
 *   ordered by the caller); names are shown as-is (data labels).
 * @param {string} [params.seriesName] - series name used by the tooltip.
 * @param {(value: number) => string} [params.formatValue] - value formatter.
 * @param {(ratio: number) => string} [params.formatPercent] - share formatter
 *   over a 0–1 ratio (ECharts percent is 0–100).
 * @returns {Object} ECharts option.
 */
export function donutChart({
  items,
  seriesName = '',
  formatValue = defaultFormat,
  formatPercent = defaultFormat,
}) {
  return {
    animationDuration: 250,
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params) => {
        const ratio = Number.isFinite(params.percent) ? params.percent / 100 : 0;
        return `${params.name}: ${formatValue(params.value)} (${formatPercent(ratio)})`;
      },
    },
    series: [{
      name: seriesName,
      type: 'pie',
      radius: ['40%', '62%'],
      center: ['50%', '52%'],
      avoidLabelOverlap: true,
      label: {
        show: true,
        fontSize: 11,
        formatter: (params) => {
          const ratio = Number.isFinite(params.percent) ? params.percent / 100 : 0;
          return `${params.name} ${formatPercent(ratio)}`;
        },
      },
      labelLayout: { hideOverlap: true },
      itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
      data: items.map(({ name, value }) => ({ name, value })),
    }],
  };
}

/**
 * Builds a choropleth (map series) option over a GeoJSON the caller has
 * already registered by name (`echarts.registerMap` needs the loaded GeoJSON,
 * so it stays in app.js and this module keeps being pure). Regions without a
 * row keep the base `itemStyle.areaColor` (light grey) and their tooltip
 * reports "no data"; only the rows present in `rows` go through the visualMap.
 * @param {Object} params
 * @param {string} params.mapName - name used with `echarts.registerMap`.
 * @param {Array<{country: string, job_offers: number}>} [params.rows] - mapped
 *   values; the caller must exclude labels without geometry from the map.
 * @param {string} [params.seriesName] - series name (used by tooltips/legend).
 * @param {(value: number) => string} [params.formatValue] - visualMap labels.
 * @param {(country: string, value: number) => string} [params.formatTooltip]
 *   - localised tooltip for regions with data.
 * @param {(country: string) => string} [params.formatNoData] - localised
 *   tooltip for regions without data.
 * @param {string} [params.moreLabel] - visualMap upper text (already localised).
 * @param {string} [params.lessLabel] - visualMap lower text (already localised).
 * @param {string} [params.noDataColor] - fill of regions without data.
 * @param {string[]} [params.colors] - visualMap ramp (low → high).
 * @returns {Object} ECharts option.
 */
export function choroplethOption({
  mapName,
  rows = [],
  seriesName = '',
  formatValue = defaultFormat,
  formatTooltip = null,
  formatNoData = null,
  moreLabel = '',
  lessLabel = '',
  noDataColor = '#e6e9ed',
  colors = ['#e3edf8', '#a8c8e8', '#5b93c9', '#1f5f9e'],
}) {
  const values = rows
    .map((row) => Number(row.job_offers))
    .filter((value) => Number.isFinite(value));
  const max = Math.max(1, ...values);
  return {
    animationDuration: 250,
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params) => {
        // Null/blank/absent values must take the "no data" branch too:
        // `Number(null)` is 0 and would print an invented "0 postings" (T-21).
        const raw = params.value;
        const value = raw == null || raw === '' ? NaN : Number(raw);
        if (Number.isFinite(value)) {
          return formatTooltip
            ? formatTooltip(params.name, value)
            : `${params.name}: ${formatValue(value)}`;
        }
        return formatNoData ? formatNoData(params.name) : params.name;
      },
    },
    visualMap: {
      min: 0,
      max,
      precision: 0,
      text: [moreLabel, lessLabel],
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: colors },
      textStyle: { fontSize: 11 },
    },
    series: [{
      name: seriesName,
      type: 'map',
      map: mapName,
      nameProperty: 'name',
      selectedMode: false,
      roam: false,
      label: { show: false },
      itemStyle: {
        areaColor: noDataColor,
        borderColor: '#ffffff',
        borderWidth: 0.6,
      },
      emphasis: { label: { show: true, fontSize: 10 } },
      // A row without a numeric value keeps no `value` at all, so its tooltip
      // takes the "no data" branch instead of showing a fabricated zero
      // (`Number(null)` would be 0).
      data: rows.map((row) => {
        const raw = row.job_offers;
        const value = raw == null || raw === '' ? NaN : Number(raw);
        return Number.isFinite(value) ? { name: row.country, value } : { name: row.country };
      }),
    }],
  };
}

/**
 * Builds a bubble (scatter) chart option: one series per category so the
 * ECharts legend lists/toggles categories, with the bubble diameter growing
 * with `size` on a square-root scale (bubble area roughly proportional to the
 * value). Items without a numeric y must be filtered by the caller: points
 * without a median salary are not representable and never reach this builder.
 * @param {Object} params
 * @param {Array<{name: string, category: string, x: number, y: number, size: number}>} params.items
 *   - points to plot; `name` is the series data label (tooltip only).
 * @param {string} [params.xName] - x-axis name (already localised).
 * @param {string} [params.yName] - y-axis name (already localised).
 * @param {(value: number) => string} [params.formatX] - x-axis/tooltip formatter.
 * @param {(value: number) => string} [params.formatY] - y-axis formatter.
 * @param {(item: {name: string, category: string, value: number[], size: number}) => string}
 *   [params.formatTooltip] - full localised tooltip; a plain fallback is used
 *   when omitted.
 * @param {number} [params.minSymbolSize] - diameter of the smallest bubble.
 * @param {number} [params.maxSymbolSize] - diameter of the largest bubble.
 * @returns {Object} ECharts option.
 */
export function bubbleChart({
  items = [],
  xName = '',
  yName = '',
  formatX = defaultFormat,
  formatY = defaultFormat,
  formatTooltip = null,
  minSymbolSize = 10,
  maxSymbolSize = 42,
}) {
  const maxSize = Math.max(1, ...items.map((item) => Number(item.size) || 0));
  const symbolSize = (size) => {
    const ratio = Math.sqrt(Math.max(Number(size) || 0, 0) / maxSize);
    return minSymbolSize + ratio * (maxSymbolSize - minSymbolSize);
  };
  const categories = [...new Set(items.map((item) => item.category || ''))];
  const series = categories.map((category) => ({
    name: category,
    type: 'scatter',
    data: items
      .filter((item) => (item.category || '') === category)
      .map((item) => ({
        name: item.name,
        category,
        value: [item.x, item.y],
        size: item.size,
      })),
    symbolSize: (value, params) => symbolSize(params.data.size),
  }));
  const fallbackTooltip = (item) => {
    const [x, y] = Array.isArray(item.value) ? item.value : [item.value, ''];
    return `${item.name}: ${formatX(x)} · ${formatY(y)}`;
  };
  return {
    animationDuration: 250,
    // Vertical room for the legend, the y-axis name at the top of the axis and
    // the x-axis name below it; without it the y-axis name overlapped its own
    // scale when the salary labels grew wide (T-21).
    grid: { ...chartGrid, top: 46, bottom: 44 },
    legend: categories.length > 1
      ? { type: 'scroll', top: 0, data: categories }
      : { show: false },
    tooltip: {
      trigger: 'item',
      confine: true,
      // ECharts pins `white-space: nowrap` on the tooltip, so on narrow
      // screens a long label overflowed the canvas and left the page
      // horizontally scrollable (`confine` only moves the box, it does not
      // shrink it). `width: max-content` keeps the natural single line and
      // the viewport-relative cap (80px reserves page/card padding and the
      // scrollbar, so on phones it lands on the chart container itself)
      // plus wrapping brings it back inside the page.
      extraCssText:
        'width: max-content; max-width: max(100%, calc(100vw - 80px)); white-space: normal; overflow-wrap: break-word;',
      formatter: (params) =>
        (formatTooltip ? formatTooltip(params.data) : fallbackTooltip(params.data)),
    },
    xAxis: {
      type: 'value',
      name: xName,
      nameLocation: 'middle',
      nameGap: 30,
      axisLabel: { formatter: (value) => formatX(value) },
    },
    yAxis: {
      type: 'value',
      name: yName,
      // `end` places the name above the axis, left-aligned with its first
      // tick, so it never sits on top of the salary scale.
      nameLocation: 'end',
      nameGap: 16,
      nameTextStyle: { align: 'left' },
      axisLabel: { formatter: (value) => formatY(value) },
    },
    series,
  };
}
