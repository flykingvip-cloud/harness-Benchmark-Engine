import type { FixedHarnessModelMetrics, ResultDetail, RoundMode } from "./api";
import { crossModelBars, formatRate, harnessDimensionBars, pairedIntervals, type BarMark, type IntervalPoint } from "./chartData";
import { DIMENSIONS, type DimensionId } from "./dimensions";
import { useI18n } from "./i18n/I18nProvider";
import type { MessageKey } from "./i18n/messages";

const NAME: Record<DimensionId, MessageKey> = {
  validity: "dim.validity",
  effectiveness: "dim.effectiveness",
  process: "dim.process",
  cost: "dim.cost",
  diagnosability: "dim.diagnosability",
};

const PALETTE = ["#3DDC97", "#7AA2FF", "#F0C14B", "#F2A2C0", "#C4B5FD"];

export function FrameworkCharts({
  detail,
  metrics,
  mode,
  harnessId,
  variant,
}: {
  detail: ResultDetail;
  metrics: FixedHarnessModelMetrics[] | null;
  mode: RoundMode | null;
  harnessId: string | null;
  variant: "full" | "compact";
}) {
  const { t } = useI18n();
  const points = pairedIntervals(detail);
  const bars = harnessDimensionBars(detail);
  const names = Object.fromEntries(DIMENSIONS.map((id) => [id, t(NAME[id])])) as Record<DimensionId, string>;
  const cross = mode === "cross_model" && metrics && metrics.length > 0 ? crossModelBars(metrics) : null;
  const harnesses = bars[0]?.marks.map((mark) => mark.seriesId) ?? [];
  return (
    <div className={variant === "compact" ? "chart-stack compact" : "chart-stack"}>
      <div className="chart-grid">
        <IntervalFigure points={points} title={t("chart.intervalTitle")} caption={t("chart.intervalCaption")} harnesses={harnesses} />
        <BarFigure
          categories={bars.map((item) => ({ id: names[item.id], marks: item.marks }))}
          title={t("chart.barTitle")}
          caption={t("chart.barCaption")}
          testId="dimension-bars"
        />
      </div>
      {cross ? (
        <BarFigure
          categories={cross.map((item) => ({ id: item.taskClass, marks: item.marks }))}
          title={t("chart.crossTitle")}
          caption={t("chart.crossCaption", { harness: harnessId ?? "" })}
          testId="cross-bars"
          secondary
        />
      ) : null}
    </div>
  );
}

function IntervalFigure({ points, title, caption, harnesses }: { points: IntervalPoint[]; title: string; caption: string; harnesses: string[] }) {
  const tasks = unique(points.map((point) => point.taskId));
  const series = harnesses.length > 0 ? harnesses : unique(points.map((point) => point.harnessId));
  const rowH = 48;
  const left = 156;
  const right = 52;
  const top = 8;
  const bottom = 22;
  const width = 640;
  const height = top + Math.max(tasks.length, 1) * rowH + bottom;
  const plotW = width - left - right;
  const x = (rate: number) => left + rate * plotW;
  const summary = points
    .map((point) => {
      const band = point.low !== null && point.high !== null ? ` [${formatRate(point.low)}, ${formatRate(point.high)}]` : "";
      return `${point.taskId} ${point.harnessId} ${formatRate(point.rate)}${band}`;
    })
    .join("; ");
  return (
    <figure className="chart-figure" data-testid="paired-interval">
      <h3>{title}</h3>
      <Legend series={series} />
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${summary || caption}`}>
        {[0, 0.5, 1].map((tick) => (
          <line key={tick} x1={x(tick)} x2={x(tick)} y1={top} y2={height - bottom} stroke="#2a2e38" />
        ))}
        {tasks.map((taskId, taskIndex) => {
          const rows = points.filter((point) => point.taskId === taskId);
          const mid = top + taskIndex * rowH + rowH / 2;
          return (
            <g key={taskId}>
              <text x={left - 10} y={mid + 4} textAnchor="end" fill="#c5c9d4" fontSize="11">
                {taskId}
              </text>
              {rows.map((point, index) => {
                const y = mid + (index - (rows.length - 1) / 2) * 16;
                const color = colorFor(series.indexOf(point.harnessId));
                return (
                  <g key={`${point.harnessId}-${index}`}>
                    {point.low !== null && point.high !== null ? (
                      <>
                        <line x1={x(point.low)} x2={x(point.high)} y1={y} y2={y} stroke={color} strokeWidth="2" />
                        <line x1={x(point.low)} x2={x(point.low)} y1={y - 4} y2={y + 4} stroke={color} strokeWidth="1.5" />
                        <line x1={x(point.high)} x2={x(point.high)} y1={y - 4} y2={y + 4} stroke={color} strokeWidth="1.5" />
                      </>
                    ) : null}
                    <circle cx={x(point.rate)} cy={y} r="4.5" fill={color} />
                    <text x={width - 6} y={y + 4} textAnchor="end" fill={color} fontSize="12" fontWeight="600">
                      {formatRate(point.rate)}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
        {[0, 0.5, 1].map((tick) => (
          <text key={`axis-${tick}`} x={x(tick)} y={height - 6} textAnchor="middle" fill="#8b93a3" fontSize="11">
            {tick}
          </text>
        ))}
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

function BarFigure({
  categories,
  title,
  caption,
  testId,
  secondary = false,
}: {
  categories: Array<{ id: string; marks: BarMark[] }>;
  title: string;
  caption: string;
  testId: string;
  secondary?: boolean;
}) {
  const series = categories[0]?.marks.map((mark) => mark.seriesId) ?? [];
  const width = 640;
  const height = 228;
  const left = 28;
  const right = 8;
  const top = 22;
  const bottom = categories.some((item) => item.id.length > 12) ? 46 : 36;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const groupW = categories.length > 0 ? plotW / categories.length : plotW;
  const barW = Math.min(26, series.length > 0 ? (groupW * 0.62) / series.length : 26);
  const gap = 8;
  const cluster = series.length * barW + Math.max(0, series.length - 1) * gap;
  const y = (ratio: number) => top + plotH - ratio * plotH;
  const summary = categories
    .map((category) => `${category.id} ${category.marks.map((mark) => `${mark.seriesId} ${mark.label}`).join(", ")}`)
    .join("; ");
  return (
    <figure className={secondary ? "chart-figure chart-secondary" : "chart-figure"} data-testid={testId}>
      <h3>{title}</h3>
      <Legend series={series} />
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${summary}`}>
        {[0, 0.5, 1].map((tick) => (
          <line key={tick} x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#2a2e38" />
        ))}
        {categories.map((category, index) => {
          const origin = left + index * groupW + (groupW - cluster) / 2;
          return (
            <g key={category.id}>
              {category.marks.map((mark, markIndex) => {
                const bx = origin + markIndex * (barW + gap);
                const color = colorFor(series.indexOf(mark.seriesId));
                if (mark.ratio === null) {
                  return (
                    <text key={mark.seriesId} x={bx + barW / 2} y={y(0) - 6} textAnchor="middle" fill="#8b93a3" fontSize="11">
                      {mark.label}
                    </text>
                  );
                }
                const barH = Math.max(mark.ratio * plotH, mark.ratio === 0 ? 2 : 0);
                return (
                  <g key={mark.seriesId}>
                    <rect x={bx} y={y(mark.ratio)} width={barW} height={barH} rx="3" fill={color} />
                    <text x={bx + barW / 2} y={y(mark.ratio) - 6} textAnchor="middle" fill={color} fontSize="11" fontWeight="600">
                      {mark.label}
                    </text>
                  </g>
                );
              })}
              <text x={left + index * groupW + groupW / 2} y={height - 14} textAnchor="middle" fill="#c5c9d4" fontSize="11">
                {category.id}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

function Legend({ series }: { series: string[] }) {
  if (series.length === 0) {
    return null;
  }
  return (
    <ul className="chart-legend">
      {series.map((name, index) => (
        <li key={name}>
          <span style={{ background: colorFor(index) }} />
          {name}
        </li>
      ))}
    </ul>
  );
}

function colorFor(index: number): string {
  return PALETTE[(index < 0 ? 0 : index) % PALETTE.length];
}

function unique(values: string[]): string[] {
  const seen: string[] = [];
  for (const value of values) {
    if (!seen.includes(value)) {
      seen.push(value);
    }
  }
  return seen;
}
