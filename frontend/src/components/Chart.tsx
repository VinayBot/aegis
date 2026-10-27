// ECharts, tree-shaken to the pieces used here, themed from the CSS tokens. Every chart ships a
// table fallback ("Show as table") so the data is readable without the canvas.
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "../lib/motion";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface Series {
  name: string;
  data: (number | null)[];
  color: string; // a CSS variable name, e.g. --cyan
  type?: "line" | "bar";
  stack?: string;
  area?: boolean;
  dashed?: boolean;
}

export default function Chart({
  title,
  t,
  series,
  unit = "",
  format = (v: number) => `${Math.round(v * 100) / 100}${unit}`,
  height = 220,
  markLine,
  yMax,
}: {
  title: string;
  t: number[];
  series: Series[];
  unit?: string;
  format?: (v: number) => string;
  height?: number;
  markLine?: { value: number; label: string; color: string };
  yMax?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const reduced = useReducedMotion();
  const [table, setTable] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chart.current) return;
    const ink = token("--ink-mute");
    const line = token("--line");
    const labels = t.map((ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false, minute: "2-digit", second: "2-digit" }));
    chart.current.setOption(
      {
        animation: !reduced,
        animationDurationUpdate: 300,
        grid: { left: 8, right: 12, top: 28, bottom: 4, containLabel: true },
        legend: series.length > 1 ? { top: 0, right: 0, textStyle: { color: ink, fontSize: 11 }, itemWidth: 10, itemHeight: 6, icon: "roundRect" } : undefined,
        tooltip: {
          trigger: "axis",
          backgroundColor: token("--glass-strong"),
          borderColor: token("--glass-edge-hi"),
          textStyle: { color: token("--ink"), fontSize: 12 },
          valueFormatter: (v: unknown) => (typeof v === "number" ? format(v) : "—"),
        },
        xAxis: { type: "category", data: labels, boundaryGap: series.some((s) => s.type === "bar"), axisLine: { lineStyle: { color: line } }, axisTick: { show: false }, axisLabel: { color: ink, fontSize: 10, interval: Math.ceil(labels.length / 6) } },
        yAxis: { type: "value", max: yMax, splitLine: { lineStyle: { color: line } }, axisLabel: { color: ink, fontSize: 10, formatter: (v: number) => format(v) } },
        series: series.map((s, i) => ({
          name: s.name,
          type: s.type ?? "line",
          data: s.data,
          stack: s.stack,
          smooth: 0.25,
          showSymbol: true,
          symbolSize: 5,
          connectNulls: false,
          barMaxWidth: 10,
          itemStyle: {
            color: s.type === "bar"
              ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: token(s.color) }, { offset: 1, color: token("--space-2") }])
              : token(s.color),
            borderRadius: s.type === "bar" ? [3, 3, 0, 0] : 0,
            shadowBlur: s.type === "bar" ? 12 : 0,
            shadowColor: token(s.color),
          },
          lineStyle: { width: 2.4, color: token(s.color), type: s.dashed ? "dashed" : "solid", shadowBlur: 14, shadowColor: token(s.color) },
          areaStyle: s.area || (s.type !== "bar" && series.length === 1)
            ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: token(s.color) }, { offset: 1, color: "rgba(0,0,0,0)" }]), opacity: 0.35 }
            : undefined,
          markLine: i === 0 && markLine ? { symbol: "none", silent: true, data: [{ yAxis: markLine.value }], lineStyle: { color: token(markLine.color), type: "dashed" }, label: { formatter: markLine.label, color: token(markLine.color), fontSize: 10, position: "insideEndTop" } } : undefined,
        })),
      },
      { notMerge: true },
    );
  }, [t, series, reduced, format, markLine, yMax]);

  const hasData = series.some((s) => s.data.some((v) => v !== null && v !== 0));
  return (
    <figure className="glass p-4 transition-transform duration-300 hover:-translate-y-0.5">
      <figcaption className="flex items-center justify-between gap-3">
        <span className="font-semibold text-ink">{title}</span>
        <button type="button" className="text-xs text-ink-mute underline underline-offset-2 hover:text-ink" onClick={() => setTable(!table)} aria-expanded={table}>
          {table ? "Show chart" : "Show as table"}
        </button>
      </figcaption>
      <div className="relative" style={{ display: table ? "none" : "block" }}>
        <div ref={ref} style={{ height }} role="img" aria-label={`${title} chart; use "Show as table" for values`} />
        {!hasData && <p className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-ink-faint" aria-hidden="true">no traffic in this window</p>}
      </div>
      {table && (
        <div className="max-h-[220px] overflow-auto" style={{ height }}>
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-ink-mute">
                <th className="py-1 pr-3 font-medium">time</th>
                {series.map((s) => <th key={s.name} className="py-1 pr-3 text-right font-medium">{s.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {t.map((ts, i) => (
                <tr key={ts} className="text-ink-soft">
                  <td className="py-0.5 pr-3 font-tabular">{new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false })}</td>
                  {series.map((s) => <td key={s.name} className="py-0.5 pr-3 text-right font-tabular">{s.data[i] === null ? "—" : format(s.data[i] as number)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}
