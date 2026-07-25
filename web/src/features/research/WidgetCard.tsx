import { buildChartModel } from "../../domain/agent-events";
import type { ChartModel, JsonObject, Widget } from "../../domain/types";

const CHART_COLORS = [
  "#14756f",
  "#c37b2a",
  "#6b5ca5",
  "#b54b62",
  "#4878a8",
  "#748238",
];

function WidgetTable({ rows }: { rows: JsonObject[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 8);
  return (
    <div className="widget-table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column}>{String(row[column] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WidgetChart({ model, widget }: { model: ChartModel; widget: Widget }) {
  const width = 680;
  const height = 300;
  const padding = { top: 20, right: 20, bottom: 58, left: 58 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const yRange = model.maximum - model.minimum;
  const xCenter = (index: number) =>
    padding.left + ((index + 0.5) * plotWidth) / model.labels.length;
  const yPosition = (value: number) =>
    padding.top + ((model.maximum - value) / yRange) * plotHeight;
  const baseline = yPosition(0);
  const numberFormat = new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  });
  const xLabelStep = Math.max(1, Math.ceil(model.labels.length / 8));

  return (
    <div className="widget-chart-wrap">
      <svg
        className="widget-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${widget.title || "Data"} ${model.kind} chart`}
      >
        <title>{widget.title || "Data chart"}</title>
        {Array.from({ length: 5 }, (_, index) => {
          const value = model.maximum - (index * yRange) / 4;
          const y = yPosition(value);
          return (
            <g key={index}>
              <line
                className="chart-grid"
                x1={padding.left}
                x2={width - padding.right}
                y1={y}
                y2={y}
              />
              <text
                className="chart-label chart-y-label"
                x={padding.left - 9}
                y={y + 4}
                textAnchor="end"
              >
                {numberFormat.format(value)}
              </text>
            </g>
          );
        })}
        <line
          className="chart-axis"
          x1={padding.left}
          x2={width - padding.right}
          y1={baseline}
          y2={baseline}
        />
        {model.labels.map((value, index) =>
          index % xLabelStep !== 0 && index !== model.labels.length - 1 ? null : (
            <text
              className="chart-label chart-x-label"
              key={`${value}:${index}`}
              x={xCenter(index)}
              y={height - padding.bottom + 23}
              textAnchor="middle"
            >
              {value.length > 16 ? `${value.slice(0, 15)}…` : value}
            </text>
          ),
        )}
        {model.kind === "bar"
          ? model.series.flatMap((series, seriesIndex) => {
              const groupWidth = plotWidth / model.labels.length;
              const barWidth = Math.max(1, (groupWidth * 0.72) / model.series.length);
              return series.values.map((value, rowIndex) => {
                if (value === null) return null;
                const valueY = yPosition(value);
                return (
                  <rect
                    className="chart-bar"
                    key={`${series.field}:${rowIndex}`}
                    x={
                      padding.left +
                      rowIndex * groupWidth +
                      groupWidth * 0.14 +
                      seriesIndex * barWidth
                    }
                    y={Math.min(valueY, baseline)}
                    width={Math.max(1, barWidth - 1)}
                    height={Math.max(1, Math.abs(baseline - valueY))}
                    fill={CHART_COLORS[seriesIndex % CHART_COLORS.length]}
                  />
                );
              });
            })
          : model.series.map((series, seriesIndex) => {
              let drawing = false;
              let pathData = "";
              series.values.forEach((value, rowIndex) => {
                if (value === null) {
                  drawing = false;
                  return;
                }
                pathData += `${drawing ? " L" : " M"} ${xCenter(rowIndex)} ${yPosition(
                  value,
                )}`;
                drawing = true;
              });
              return (
                <g key={series.field}>
                  {pathData ? (
                    <path
                      className="chart-line"
                      d={pathData}
                      stroke={CHART_COLORS[seriesIndex % CHART_COLORS.length]}
                    />
                  ) : null}
                  {model.labels.length <= 20
                    ? series.values.map((value, rowIndex) =>
                        value === null ? null : (
                          <circle
                            className="chart-point"
                            key={rowIndex}
                            cx={xCenter(rowIndex)}
                            cy={yPosition(value)}
                            r={3}
                            fill={CHART_COLORS[seriesIndex % CHART_COLORS.length]}
                          />
                        ),
                      )
                    : null}
                </g>
              );
            })}
      </svg>
      <div className="widget-legend">
        {model.series.map((series, index) => (
          <span key={series.field}>
            <i style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }} />
            {series.field}
          </span>
        ))}
      </div>
    </div>
  );
}

export function WidgetCard({ widget }: { widget: Widget }) {
  const rows = Array.isArray(widget.data) ? widget.data.slice(0, 50) : [];
  let content = null;
  if (widget.kind === "metric" && rows.length) {
    content = (
      <dl className="widget-metrics">
        {Object.entries(rows[0])
          .slice(0, 8)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{String(value ?? "—")}</dd>
            </div>
          ))}
      </dl>
    );
  } else if (["bar", "line"].includes(widget.kind || "") && rows.length) {
    try {
      const chartWidget = { ...widget, data: rows };
      content = (
        <WidgetChart model={buildChartModel(chartWidget)} widget={chartWidget} />
      );
    } catch {
      content = <WidgetTable rows={rows} />;
    }
  } else if (rows.length) {
    content = <WidgetTable rows={rows} />;
  }

  return (
    <section className="widget-card">
      <div className="widget-heading">
        <h3>{widget.title || "Result"}</h3>
        <span>{widget.kind || "data"}</span>
      </div>
      {widget.description ? <p>{widget.description}</p> : null}
      {content}
    </section>
  );
}
