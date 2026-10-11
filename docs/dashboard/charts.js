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

/** Axis-trigger tooltip whose values go through the caller's formatter. */
const axisTooltip = (formatValue) => ({
  trigger: 'axis',
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
    axisLabel: { formatter: (value) => formatValue(value) },
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
