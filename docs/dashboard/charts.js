/**
 * charts.js — pure ECharts option builders for the public dashboard.
 *
 * Pure ES module: it does not import ECharts, does not touch the DOM and holds
 * no business logic. The caller (app.js) passes already-fetched data, already
 * localised labels and formatting callbacks; each function returns a plain
 * ECharts `option` object that app.js mounts with `state.echarts.init()`.
 * Identifiers and comments are in English (constitution 6); every user-facing
 * string travels through i18n.js.
 *
 * T-27: every builder shares one visual theme — palette, typography, axes with
 * a soft split line and contained tooltips — so line, bar, donut, bubble and
 * map read as a single system. Colours are plain hex values derived from the
 * shell tokens in `styles.css` (`:root`) because a pure module never reads the
 * DOM: accent #1d4ed8, accent-2 #0e7490, text #101828, muted #5b6b82, borders
 * #e3e8f1 and surfaces #ffffff/#f7f9fd.
 */

/** Font stack of the shell (`body` in styles.css). */
const FONT_FAMILY =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

/** Shared palette and surfaces (T-27); keep in sync with the CSS tokens. */
const COLORS = {
  accent: '#1d4ed8',
  accentLight: '#3b82f6',
  text: '#101828',
  muted: '#5b6b82',
  axis: '#dfe5ef',
  split: '#eef1f7',
  border: '#e3e8f1',
  /** Leader line of the outside donut labels. */
  labelLine: '#c9d2e3',
  surface: '#ffffff',
  // Neutral grey of regions without data; greyer than the lightest blue of
  // `mapRamp` so "no data" never reads as a low value (T-25/T-27).
  noData: '#e6e9ed',
  /**
   * Categorical ramp (donut slices, bubble categories): the five shell accents
   * first, then their 600-level siblings; every hue stays distinguishable on
   * white and inside the legend.
   */
  categorical: [
    '#1d4ed8', '#0e7490', '#0f766e', '#b45309',
    '#6d28d9', '#2563eb', '#0891b2', '#7c3aed',
  ],
  /** Sequential choropleth ramp, light → dark (accent-soft → accent). */
  mapRamp: ['#dce7fb', '#aec5f0', '#5f88e3', '#1d4ed8'],
};

/** Fallback label/tooltip formatter: plain string conversion, never `undefined`. */
const defaultFormat = (value) => String(value ?? '');

/**
 * Shared grid with room for axis labels on every side. `containLabel` reserves
 * space for the labels that live inside the axis range, but the label centred
 * on the last tick/category can stick out past the canvas and get clipped
 * (e.g. "oct 2026" in the trend or the last value of a ranking); the extra
 * `right` keeps it visible (T-27).
 */
const chartGrid = { left: 8, right: 38, top: 16, bottom: 8, containLabel: true };

/** Rounded corners and soft shadow shared by every tooltip (T-27). */
const TOOLTIP_CSS =
  'border-radius: 8px; box-shadow: 0 8px 20px rgba(16, 24, 40, 0.14);';

/**
 * Shared tooltip surface (T-27). `confine: true` keeps long tooltips inside
 * the chart canvas (T-21): they are never clipped by the viewport edge.
 */
const tooltipBase = () => ({
  confine: true,
  backgroundColor: 'rgba(255, 255, 255, 0.98)',
  borderColor: COLORS.border,
  borderWidth: 1,
  padding: [6, 10],
  textStyle: { color: COLORS.text, fontSize: 12, fontFamily: FONT_FAMILY },
  extraCssText: TOOLTIP_CSS,
});

/** Axis label style; `hideOverlap` drops labels that would collide. */
const axisLabelStyle = (formatValue) => ({
  color: COLORS.muted,
  fontSize: 11,
  fontFamily: FONT_FAMILY,
  hideOverlap: true,
  ...(formatValue ? { formatter: (value) => formatValue(value) } : {}),
});

/** Themed value axis: no axis line/ticks, soft horizontal split lines. */
const valueAxisStyle = (formatValue) => ({
  type: 'value',
  minInterval: 1,
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: axisLabelStyle(formatValue),
  splitLine: { lineStyle: { color: COLORS.split } },
});

/** Themed category axis: a thin axis line, no ticks, no split lines. */
const categoryAxisStyle = (categories) => ({
  type: 'category',
  data: categories,
  axisLine: { lineStyle: { color: COLORS.axis } },
  axisTick: { show: false },
  axisLabel: axisLabelStyle(),
  splitLine: { show: false },
});

/**
 * Builds a single-series line chart option with a subtle area under the line.
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
    color: [COLORS.accent],
    grid: chartGrid,
    tooltip: {
      ...tooltipBase(),
      trigger: 'axis',
      valueFormatter: (value) => formatValue(value),
    },
    xAxis: {
      ...categoryAxisStyle(categories),
      boundaryGap: false,
      // `showMaxLabel` keeps the newest month visible on narrow screens, where
      // the label interval would skip it (the `right` padding above is what
      // keeps it from being clipped).
      axisLabel: { ...axisLabelStyle(), rotate: rotateLabels ? 45 : 0, showMaxLabel: true },
    },
    yAxis: valueAxisStyle(formatValue),
    series: [{
      name: seriesName,
      type: 'line',
      data: values,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color: COLORS.accent },
      itemStyle: { color: COLORS.accent, borderColor: COLORS.surface, borderWidth: 1 },
      // Soft area under the line: the accent tint fades to transparent so the
      // series keeps the focus without turning into a solid block (T-27).
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(29, 78, 216, 0.16)' },
            { offset: 1, color: 'rgba(29, 78, 216, 0)' },
          ],
        },
      },
    }],
  };
}

/**
 * Builds a single-series bar chart option, vertical (default) or horizontal,
 * with rounded corners on the value end.
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
  const valueAxis = valueAxisStyle(formatValue);
  const categoryAxis = categoryAxisStyle(categories);
  return {
    animationDuration: 250,
    color: [COLORS.accent],
    grid: chartGrid,
    tooltip: {
      ...tooltipBase(),
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(29, 78, 216, 0.06)' } },
      valueFormatter: (value) => formatValue(value),
    },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: [{
      name: seriesName,
      type: 'bar',
      data: values,
      barMaxWidth: 22,
      itemStyle: {
        // Rounded corners on the value end (T-27): the right side for
        // horizontal bars, the top for vertical ones.
        borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        // A light accent tint grows into the accent colour so a long ranking
        // stays readable without flat slabs.
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: horizontal ? 1 : 0,
          y2: horizontal ? 0 : 1,
          colorStops: [
            { offset: 0, color: COLORS.accentLight },
            { offset: 1, color: COLORS.accent },
          ],
        },
      },
    }],
  };
}

/**
 * Builds a donut (pie) chart option with a legend for the names and a short
 * percentage label outside every slice.
 * Categories that would read as "0,0 %" are dropped (value 0 or a share below
 * 0.05 %): such a slice only adds noise and the requirement forbids those
 * labels (T-27). The labels carry only the share —the name lives in the
 * legend— so they always fit: outside labels with the full text were clipped
 * with an ellipsis ("Unknown …") in the narrow cards and inside labels
 * overflowed the ring on its left/right sides.
 * @param {Object} params
 * @param {Array<{name: string, value: number}>} params.items - slices (already
 *   ordered by the caller); entries that would format as 0.0 % are filtered out.
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
  const slices = items
    .map(({ name, value }) => ({ name, value: Number(value) }))
    .filter(({ value }) => Number.isFinite(value) && value > 0);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const visible = slices.filter(({ value }) => total <= 0 || value / total >= 0.0005);
  return {
    animationDuration: 250,
    color: COLORS.categorical,
    legend: {
      show: visible.length > 1,
      type: 'plain',
      left: 'center',
      bottom: 0,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 10,
      textStyle: { color: COLORS.muted, fontSize: 10, fontFamily: FONT_FAMILY },
    },
    tooltip: {
      ...tooltipBase(),
      trigger: 'item',
      formatter: (params) => {
        const ratio = Number.isFinite(params.percent) ? params.percent / 100 : 0;
        return `${params.name}: ${formatValue(params.value)} (${formatPercent(ratio)})`;
      },
    },
    series: [{
      name: seriesName,
      type: 'pie',
      radius: ['42%', '60%'],
      center: ['50%', '40%'],
      avoidLabelOverlap: true,
      label: {
        show: true,
        position: 'outside',
        color: COLORS.text,
        fontSize: 10,
        fontFamily: FONT_FAMILY,
        formatter: (params) => {
          const ratio = Number.isFinite(params.percent) ? params.percent / 100 : 0;
          return formatPercent(ratio);
        },
      },
      labelLine: { show: true, length: 6, length2: 6, lineStyle: { color: COLORS.labelLine } },
      labelLayout: { hideOverlap: true, moveOverlap: 'shiftY' },
      itemStyle: { borderColor: COLORS.surface, borderWidth: 2 },
      data: visible,
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
 * @param {string} params.mapName - name used with `echarts.registerMap`
 *   (`tml-europe` or a country map such as `tml-spain`).
 * @param {Array<{name: string, value: number}>} [params.rows] - mapped values
 *   (name = country or region drawn on the map); the caller must exclude
 *   labels without geometry from the map.
 * @param {number[][]} [params.boundingCoords] - optional box boundary of the
 *   map, `[[minLng, maxLat], [maxLng, minLat]]`, used to fit a subset of the
 *   map features (e.g. the regions of one country) into the chart area.
 * @param {string} [params.seriesName] - series name (used by tooltips/legend).
 * @param {(value: number) => string} [params.formatValue] - visualMap labels.
 * @param {(name: string, value: number) => string} [params.formatTooltip]
 *   - localised tooltip for regions with data.
 * @param {(name: string) => string} [params.formatNoData] - localised
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
  boundingCoords = null,
  seriesName = '',
  formatValue = defaultFormat,
  formatTooltip = null,
  formatNoData = null,
  moreLabel = '',
  lessLabel = '',
  noDataColor = COLORS.noData,
  colors = COLORS.mapRamp,
}) {
  const values = rows
    .map((row) => Number(row.value))
    .filter((value) => Number.isFinite(value));
  const max = Math.max(1, ...values);
  return {
    animationDuration: 250,
    tooltip: {
      ...tooltipBase(),
      trigger: 'item',
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
      textStyle: { fontSize: 11, color: COLORS.muted, fontFamily: FONT_FAMILY },
    },
    series: [{
      name: seriesName,
      type: 'map',
      map: mapName,
      nameProperty: 'name',
      selectedMode: false,
      roam: false,
      // Fits the requested box (e.g. one country's regions) into the chart;
      // absent means the default fit of every registered feature.
      ...(boundingCoords ? { boundingCoords } : {}),
      label: { show: false },
      itemStyle: {
        areaColor: noDataColor,
        borderColor: COLORS.surface,
        borderWidth: 0.6,
      },
      emphasis: {
        label: { show: true, fontSize: 10, color: COLORS.text, fontFamily: FONT_FAMILY },
      },
      // A row without a numeric value keeps no `value` at all, so its tooltip
      // takes the "no data" branch instead of showing a fabricated zero
      // (`Number(null)` would be 0).
      data: rows.map((row) => {
        const raw = row.value;
        const value = raw == null || raw === '' ? NaN : Number(raw);
        return Number.isFinite(value) ? { name: row.name, value } : { name: row.name };
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
    // Slight transparency and a hairline border: dense areas stay readable.
    itemStyle: { opacity: 0.8, borderColor: COLORS.surface, borderWidth: 0.5 },
    emphasis: { itemStyle: { opacity: 1 } },
  }));
  const fallbackTooltip = (item) => {
    const [x, y] = Array.isArray(item.value) ? item.value : [item.value, ''];
    return `${item.name}: ${formatX(x)} · ${formatY(y)}`;
  };
  const legendStyle = {
    color: COLORS.muted,
    fontSize: 11,
    fontFamily: FONT_FAMILY,
  };
  return {
    animationDuration: 250,
    color: COLORS.categorical,
    // Vertical room for the legend, the y-axis name at the top of the axis and
    // the x-axis name below it; without it the y-axis name overlapped its own
    // scale when the salary labels grew wide (T-21), and `top: 56` also keeps
    // the legend clear of the axis name on narrow screens (T-27).
    grid: { ...chartGrid, top: 56, bottom: 44 },
    legend: categories.length > 1
      ? {
        type: 'scroll',
        top: 0,
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        itemGap: 10,
        data: categories,
        textStyle: legendStyle,
        pageTextStyle: legendStyle,
        pageIconColor: COLORS.muted,
        pageIconInactiveColor: COLORS.axis,
      }
      : { show: false },
    tooltip: {
      ...tooltipBase(),
      trigger: 'item',
      // ECharts pins `white-space: nowrap` on the tooltip, so on narrow
      // screens a long label overflowed the canvas and left the page
      // horizontally scrollable (`confine` only moves the box, it does not
      // shrink it). `width: max-content` keeps the natural single line and
      // the viewport-relative cap (80px reserves page/card padding and the
      // scrollbar, so on phones it lands on the chart container itself)
      // plus wrapping brings it back inside the page.
      extraCssText:
        `${TOOLTIP_CSS} width: max-content; max-width: max(100%, calc(100vw - 80px)); white-space: normal; overflow-wrap: break-word;`,
      formatter: (params) =>
        (formatTooltip ? formatTooltip(params.data) : fallbackTooltip(params.data)),
    },
    xAxis: {
      ...valueAxisStyle(formatX),
      name: xName,
      nameLocation: 'middle',
      nameGap: 30,
      nameTextStyle: { color: COLORS.muted, fontSize: 11, fontFamily: FONT_FAMILY },
    },
    yAxis: {
      ...valueAxisStyle(formatY),
      name: yName,
      // `end` places the name above the axis, left-aligned with its first
      // tick, so it never sits on top of the salary scale.
      nameLocation: 'end',
      nameGap: 18,
      nameTextStyle: { color: COLORS.muted, fontSize: 11, fontFamily: FONT_FAMILY, align: 'left' },
    },
    series,
  };
}
